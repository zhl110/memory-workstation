"""融合检索引擎：向量+BM25+Entity 三层融合"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class SearchEngine:
    def __init__(self, storage, llm=None):
        self.storage = storage
        self.llm = llm

    def search(self, query: str, top_k: int = 10) -> list[dict]:
        # ----- 1. 向量检索 -----
        vector_scores = {}
        if self.llm and self.llm.has_embed_model:
            try:
                qv = self.llm.embed(query)
                if qv:
                    vresults = self.storage.vector.search(qv, top_k=top_k * 2, threshold=0.5)
                    for r in vresults:
                        vector_scores[r["doc_id"]] = r.get("similarity", 0)
            except Exception as e:
                logger.debug("Vector search failed: %s", e)

        # ----- 2. BM25 检索 -----
        bm25_scores = {}
        try:
            ft_results = self.storage.sqlite.search_fts(query, limit=top_k * 2)
            for r in ft_results:
                bm25_scores[r["doc_id"]] = r["score"]
        except Exception as e:
            logger.debug("FTS search failed: %s", e)

        # ----- 3. Entity 匹配检索 -----
        entity_scores = {}
        tokens = [t.strip() for t in query.replace(" ", ",").replace("，", ",").split(",") if len(t.strip()) > 1]
        if tokens:
            try:
                placeholders = " OR ".join(["entity_name LIKE ?" for _ in tokens])
                params = [f"%{t}%" for t in tokens]
                rows = self.storage.sqlite._conn.execute(
                    f"SELECT doc_id, SUM(weight) as total FROM memory_entity WHERE {placeholders} GROUP BY doc_id",
                    params,
                ).fetchall()
                for r in rows:
                    entity_scores[r[0]] = min(r[1] / 10.0, 1.0)
            except Exception as e:
                logger.debug("Entity search failed: %s", e)

        # ----- 4. 融合打分 -----
        all_ids = set(vector_scores.keys()) | set(bm25_scores.keys()) | set(entity_scores.keys())
        if not all_ids:
            return []

        max_bm25 = max(bm25_scores.values()) if bm25_scores else 1
        if max_bm25 <= 0:
            max_bm25 = 1

        merged = {}
        for doc_id in all_ids:
            vs = vector_scores.get(doc_id, 0)
            bs = bm25_scores.get(doc_id, 0) / max_bm25
            es = entity_scores.get(doc_id, 0)
            final = vs * 0.5 + bs * 0.3 + es * 0.2

            # 时效 boost：最近 7 天被访问过则 × 1.3
            try:
                recent = self.storage.sqlite._conn.execute(
                    "SELECT COUNT(*) FROM memory_access_record WHERE doc_id=? AND access_time > datetime('now', '-7 days')",
                    (doc_id,),
                ).fetchone()[0]
                if recent > 0:
                    final *= 1.3
            except Exception:
                pass

            merged[doc_id] = {
                "doc_id": doc_id,
                "score": round(final, 4),
                "signals": {"vector": round(vs, 4), "bm25": round(bs, 4), "entity": round(es, 4)},
            }

        sorted_ids = sorted(merged.keys(), key=lambda d: merged[d]["score"], reverse=True)[:top_k]

        # ----- 5. 补齐文档信息 -----
        results = []
        for doc_id in sorted_ids:
            row = self.storage.sqlite._conn.execute(
                "SELECT c.importance, c.weight, c.summary, c.content_category, c.compact_content "
                "FROM memory_classify c WHERE c.doc_id=?",
                (doc_id,),
            ).fetchone()
            if row:
                results.append({
                    "doc_id": doc_id,
                    "summary": row[2] or "",
                    "category": row[3] or "",
                    "importance": row[0] or "P2",
                    "weight": row[1] or 50,
                    "score": merged[doc_id]["score"],
                    "signals": merged[doc_id]["signals"],
                })
            else:
                results.append({
                    "doc_id": doc_id,
                    "summary": "",
                    "category": "",
                    "importance": "P2",
                    "weight": 50,
                    "score": merged[doc_id]["score"],
                    "signals": merged[doc_id]["signals"],
                })

            self.storage.sqlite.record_access(doc_id, "search")

        return results
