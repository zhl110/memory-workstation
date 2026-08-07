from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import lancedb
import pyarrow as pa

logger = logging.getLogger(__name__)

EMBED_DIM = 768
TABLE_NAME = "doc_embedding"
VALID_LABELS = {"chat_log", "compact_archive", "memory_layer", "planning_doc",
                "self_improve_learn", "meta_rule", "unknown"}
VALID_TIERS = {"short", "work", "long", "archive", "meta"}

SCHEMA = pa.schema([
    pa.field("doc_id", pa.int64()),
    pa.field("file_path", pa.utf8()),
    pa.field("vector", pa.list_(pa.float32(), list_size=EMBED_DIM)),
    pa.field("label", pa.utf8()),
    pa.field("memory_tier", pa.utf8()),
    pa.field("create_utc", pa.utf8()),
])


class VectorStore:
    def __init__(self, vector_path: str):
        self.vector_path = Path(vector_path)
        self.vector_path.mkdir(parents=True, exist_ok=True)
        self._db: Optional[lancedb.DBConnection] = None
        self._table = None

    def connect(self):
        self._db = lancedb.connect(str(self.vector_path))
        if TABLE_NAME in self._db.table_names():
            self._table = self._db.open_table(TABLE_NAME)
            logger.info("LanceDB table opened: %s", TABLE_NAME)
            try:
                if self._table.count_rows() > 2000:
                    self._table.create_index(
                        vector_column_name="vector",
                        index_type="IVF_PQ",
                        num_partitions=256,
                        num_sub_vectors=64,
                    )
                    logger.info("LanceDB IVF_PQ index created")
            except Exception as e:
                logger.debug("LanceDB index creation skipped: %s", e)
        else:
            self._table = None
            logger.info("LanceDB initialized at %s (no table yet)", self.vector_path)

    def upsert(
        self,
        doc_id: int,
        file_path: str,
        vector: list[float],
        label: str,
        memory_tier: str,
        create_utc: str,
    ):
        if len(vector) != EMBED_DIM:
            logger.warning("Vector dim mismatch: expected %d, got %d", EMBED_DIM, len(vector))
            return

        import math
        norm = math.sqrt(sum(x * x for x in vector))
        if norm == 0:
            logger.warning("Skip upsert: zero vector for doc_id=%d", doc_id)
            return
        vector = [x / norm for x in vector]

        data = [{
            "doc_id": doc_id,
            "file_path": file_path,
            "vector": vector,
            "label": label,
            "memory_tier": memory_tier,
            "create_utc": create_utc,
        }]

        if self._table is None:
            self._table = self._db.create_table(TABLE_NAME, data=data, schema=SCHEMA)
        else:
            existing = self._table.search().where(f"doc_id = {doc_id}").to_list()
            if existing:
                self._table.delete(f"doc_id = {doc_id}")
            self._table.add(data)

    def search(
        self,
        query_vector: list[float],
        top_k: int = 15,
        threshold: float = 0.5,
        label: Optional[str] = None,
        tier: Optional[str] = None,
    ) -> list[dict]:
        """向量相似度搜索

        距离转换公式: similarity = 1 / (1 + L2_distance)
        归一化向量的 L2 距离范围 [0, 2]，对应 similarity 范围 [0.333, 1.0]
        threshold=0.5 对应 L2 距离 < 1.0，过滤掉明显不相关的结果
        """
        if self._table is None:
            return []

        import math
        norm = math.sqrt(sum(x * x for x in query_vector))
        if norm == 0:
            logger.debug("Search with zero vector, returning empty")
            return []
        query_vector = [x / norm for x in query_vector]

        query = self._table.search(query_vector).limit(top_k)
        where_parts = []
        if label and label in VALID_LABELS:
            where_parts.append(f"label = '{label}'")
        if tier and tier in VALID_TIERS:
            where_parts.append(f"memory_tier = '{tier}'")
        if where_parts:
            query = query.where(" AND ".join(where_parts))

        results = query.to_list()
        filtered = []
        for r in results:
            dist = r.get("_distance", 1.0)
            similarity = 1.0 / (1.0 + dist)
            if similarity >= threshold:
                r["similarity"] = round(similarity, 4)
                filtered.append(r)
        return filtered

    def delete_by_doc_id(self, doc_id: int):
        if self._table:
            self._table.delete(f"doc_id = {doc_id}")

    def count(self) -> int:
        if self._table is None:
            return 0
        return self._table.count_rows()

    def compact(self):
        if self._table:
            self._table.compact_files()
            logger.info("LanceDB compacted")
