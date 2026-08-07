"""知识领域归一化模块 — 将LLM自由命名的领域名合并到已有领域"""
import json
import logging
import struct
import threading
from typing import Optional

logger = logging.getLogger(__name__)

COSINE_SIMILARITY_THRESHOLD = 0.82  # 高于此值认为同一领域


class DomainNormalizer:
    """领域归一化：新文档的领域名 vs 已有领域名称做向量相似度匹配"""

    def __init__(self, sqlite_store, llm_manager=None):
        self._store = sqlite_store
        self._llm = llm_manager
        self._lock = threading.Lock()
        self._cache: dict[str, list[dict]] = {}  # namespace -> domains list

    def invalidate_cache(self):
        self._cache.clear()

    def _get_domains(self, namespace: str = "default") -> list[dict]:
        if namespace in self._cache:
            return self._cache[namespace]

        rows = self._store.get_domains_raw(namespace)

        domains = []
        for r in rows:
            emb = self._deserialize_embedding(r["embedding"]) if r["embedding"] else None
            domains.append({
                "id": r["id"],
                "name": r["name"],
                "doc_count": r["doc_count"],
                "embedding": emb,
            })

        self._cache[namespace] = domains
        return domains

    def normalize(self, candidate: str, namespace: str = "default") -> tuple[str, bool]:
        """返回 (归一化后的领域名, 是否新建)"""
        if not candidate or not candidate.strip():
            return "未分类", True

        candidate = candidate.strip()

        # 1. 精确匹配
        existing = self._get_domains(namespace)
        for d in existing:
            if d["name"] == candidate:
                return d["name"], False

        # 2. 向量相似度匹配：嵌入模型就绪时用语义相似度判断是否同一领域
        if self._llm and self._llm.has_embed_model and existing:
            try:
                candidate_vec = self._llm.embed(candidate)
                if candidate_vec:
                    best_match, best_sim = None, 0.0
                    for d in existing:
                        if d["embedding"] is None:
                            continue
                        sim = self._cosine_similarity(candidate_vec, d["embedding"])
                        if sim > best_sim:
                            best_sim = sim
                            best_match = d

                    if best_match and best_sim >= COSINE_SIMILARITY_THRESHOLD:
                        logger.info(
                            "Domain normalize: '%s' -> '%s' (sim=%.3f)",
                            candidate, best_match["name"], best_sim,
                        )
                        return best_match["name"], False
            except Exception as e:
                logger.warning("Domain vector matching failed: %s", e)

        # 3. 新建领域：同时生成 embedding 存入 SQLite，供后续归一化使用
        logger.info("Domain normalize: new domain '%s'", candidate)
        embedding = None
        if self._llm and self._llm.has_embed_model:
            try:
                embedding = self._llm.embed(candidate)
            except Exception:
                pass
        self._store.add_domain(candidate, namespace, embedding)
        self.invalidate_cache()
        return candidate, True

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(x * x for x in b) ** 0.5
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    @staticmethod
    def _serialize_embedding(vec: list[float]) -> bytes:
        return struct.pack(f"{len(vec)}f", *vec)

    @staticmethod
    def _deserialize_embedding(data: bytes) -> Optional[list[float]]:
        try:
            count = len(data) // 4
            return list(struct.unpack(f"{count}f", data))
        except Exception:
            return None
