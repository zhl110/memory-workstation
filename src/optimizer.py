"""Memory Optimizer - 合并/去重/衰减定时任务"""
import json
import logging
import re
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import jieba

from .core.token_counter import truncate_tokens

logger = logging.getLogger(__name__)


class MemoryOptimizer:
    def __init__(self, storage, config=None, llm_manager=None):
        self.storage = storage
        self.config = config
        self.llm = llm_manager
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._interval_h = 24

    def start(self, interval_h: int = 24):
        self._interval_h = interval_h
        self._running = True
        self._thread = threading.Thread(target=self._optimize_loop, daemon=True)
        self._thread.start()
        logger.info("MemoryOptimizer started (interval=%dh)", interval_h)

    def stop(self):
        self._running = False

    def run_once(self):
        try:
            decayed = self.decay_weights()
            merged = self.merge_duplicates()
            removed = self.dedup()
            hot_upgraded = self.storage.sqlite.upgrade_hot_rules()
            cold_processed = self.storage.sqlite.expire_cold_rules()
            compressed = self._compress_old_summaries()
            refined = self._refine_chat_logs()
            # ── V8 第8步：进化候选发现（只查不写，用户通过 /mw-evolve 确认后才写库） ──
            cold_candidates, hot_candidates = self._find_evolve_candidates()
            logger.info("Optimize done: decayed=%d, merged=%d, removed=%d, hot_upgraded=%d, cold_processed=%d, compressed=%d, refined=%d, cold_candidates=%d, hot_candidates=%d",
                       decayed, merged, removed, hot_upgraded, cold_processed, compressed, refined,
                       len(cold_candidates), len(hot_candidates))
            return {"decayed": decayed, "merged": merged, "removed": removed,
                    "hot_upgraded": hot_upgraded, "cold_processed": cold_processed, "compressed": compressed,
                    "refined": refined, "cold_candidates": len(cold_candidates), "hot_candidates": len(hot_candidates)}
        except Exception as e:
            logger.error("Optimize failed: %s", e)
            return {"error": str(e)}

    def _optimize_loop(self):
        while self._running:
            time.sleep(self._interval_h * 3600)
            if self._running:
                self.run_once()

    def decay_weights(self, decay_rate: float = 0.9, min_weight: int = 10,
                      inactive_days: int = 90) -> int:
        conn = self.storage.sqlite._conn
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=inactive_days)).isoformat()

        rows = conn.execute(
            """SELECT c.doc_id, c.weight FROM memory_classify c
               JOIN document_files d ON c.doc_id = d.id
               WHERE c.weight > ? AND c.doc_id NOT IN (
                   SELECT doc_id FROM memory_access_record WHERE access_time > ?
               )""",
            (min_weight, cutoff),
        ).fetchall()

        updated = 0
        for r in rows:
            new_weight = max(int(r["weight"] * decay_rate), min_weight)
            if new_weight < r["weight"]:
                conn.execute(
                    "UPDATE memory_classify SET weight=? WHERE doc_id=?",
                    (new_weight, r["doc_id"]),
                )
                updated += 1

        conn.commit()
        if updated:
            logger.info("Decayed weights for %d inactive documents", updated)
        return updated

    def dedup(self) -> int:
        conn = self.storage.sqlite._conn
        rows = conn.execute(
            """SELECT id, file_path, file_hash, raw_text_snippet
               FROM document_files WHERE is_deleted=0"""
        ).fetchall()

        hash_map = {}
        for r in rows:
            h = r["file_hash"]
            if h in hash_map:
                hash_map[h].append(r["id"])
            else:
                hash_map[h] = [r["id"]]

        removed = 0
        for h, doc_ids in hash_map.items():
            if len(doc_ids) > 1:
                keep = doc_ids[0]
                for did in doc_ids[1:]:
                    conn.execute("DELETE FROM memory_classify WHERE doc_id=?", (did,))
                    conn.execute("DELETE FROM memory_access_record WHERE doc_id=?", (did,))
                    conn.execute("DELETE FROM document_files WHERE id=?", (did,))
                    removed += 1

        conn.commit()
        if removed:
            logger.info("Dedup removed %d duplicate documents", removed)
        return removed

    def merge_duplicates(self, similarity_threshold: float = 0.85) -> int:
        conn = self.storage.sqlite._conn
        rows = conn.execute(
            """SELECT c.doc_id, c.label, c.weight, d.file_path, d.raw_text_snippet,
                      c.content_category
               FROM memory_classify c
               JOIN document_files d ON c.doc_id = d.id
               WHERE c.label != 'unknown' AND d.is_deleted=0
               ORDER BY c.weight DESC"""
        ).fetchall()

        domain_groups = {}
        for r in rows:
            domain = r["content_category"] or r["label"] or "_other"
            domain_groups.setdefault(domain, []).append(r)

        merged = 0
        threshold = similarity_threshold

        for domain, group in domain_groups.items():
            if len(group) < 2:
                continue

            # V10: 纯文本Jaccard相似度，不走embed模型
            threshold = 0.7

            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    a_text = group[i]["raw_text_snippet"] or ""
                    b_text = group[j]["raw_text_snippet"] or ""

                    sim = self._text_similarity(a_text, b_text)

                    if sim >= threshold:
                        merged_content = f"{a_text}\n---\n{b_text}"
                        self.storage.sqlite.merge_memories(
                            [group[i]["doc_id"], group[j]["doc_id"]],
                            merged_content,
                            label=group[j]["label"],
                        )
                        merged += 1

        if merged:
            logger.info("Merged %d duplicate pairs", merged)
        return merged

    def _compress_old_summaries(self, days: int = 30, max_weight: int = 30) -> int:
        conn = self.storage.sqlite._conn
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = conn.execute("""
            SELECT c.doc_id, c.compact_content FROM memory_classify c
            JOIN document_files d ON c.doc_id = d.id
            WHERE c.weight <= ? AND c.doc_id NOT IN (
                SELECT doc_id FROM memory_access_record WHERE access_time > ?
            ) AND c.compact_content IS NOT NULL AND c.compact_content != ''
        """, (max_weight, cutoff)).fetchall()
        count = 0
        for r in rows:
            self.storage.sqlite.compress_summary(r["doc_id"], r["compact_content"])
            count += 1
        if count:
            logger.info("Compressed %d old summaries", count)
        return count

    def _refine_chat_logs(
        self,
        prompt: str = "",
        max_docs: int = 10,
    ) -> int:
        """扫描 chat_log 和 archive，尝试从中提炼出可用的规则/经验/promote到更高级别"""
        conn = self.storage.sqlite._conn
        rows = conn.execute("""
            SELECT c.doc_id, c.compact_content, c.label, d.file_path
            FROM memory_classify c
            JOIN document_files d ON c.doc_id = d.id
            WHERE c.label IN ('chat_log', 'compact_archive')
              AND (c.compact_content IS NOT NULL AND c.compact_content != '')
            ORDER BY c.weight DESC
            LIMIT ?
        """, (max_docs,)).fetchall()
        if not rows:
            return 0

        if not self.llm:
            logger.info("Refine skipped: LLM not available")
            return 0

        # V10: LLM classify 已移除，refine 功能暂不可用
        logger.info("Refine skipped: LLM classify removed in V10")
        return 0

    def _text_similarity(self, a: str, b: str) -> float:
        if not a or not b:
            return 0.0

        def tokenize(text: str) -> set:
            words = jieba.lcut(text)
            result = []
            for w in words:
                w = w.lower().strip()
                if not w:
                    continue
                if re.match(r'^[a-z0-9_\.\-]+$', w):
                    result.append(w)
                else:
                    result.append(w)
            return set(result)

        set_a = tokenize(a)
        set_b = tokenize(b)
        if not set_a or not set_b:
            return 0.0
        intersection = set_a & set_b
        union = set_a | set_b
        return len(intersection) / len(union) if union else 0.0

    # ── V8: 行为进化候选发现（只查不写） ──
    # 返回 (cold_candidates, hot_candidates)，两个列表都是 dict 列表
    # 约束：不写 evolution_tier / tier_history / importance / weight，不做用户交互
    # 用户通过 /mw-evolve 确认后才写入
    def _find_evolve_candidates(self, cold_days: int = 90, cold_max_weight: int = 20,
                                hot_min_weight: int = 80) -> tuple[list[dict], list[dict]]:
        """发现冷/热候选：只 SQL 查询，返回候选列表，不实际改任何数据。"""
        conn = self.storage.sqlite._conn
        now = datetime.now(timezone.utc)

        # ── 冷候选：weight <= cold_max_weight + cold_days 天无访问 + evolution_tier != 'cold' ──
        cold_cutoff = (now - timedelta(days=cold_days)).isoformat()
        try:
            cold_rows = conn.execute("""
                SELECT c.doc_id, c.compact_content, c.importance, c.weight, c.evolution_tier
                FROM memory_classify c
                JOIN document_files d ON c.doc_id = d.id
                WHERE d.is_deleted = 0
                  AND (c.evolution_tier IS NULL OR c.evolution_tier != 'cold')
                  AND c.weight <= ?
                  AND c.doc_id NOT IN (
                      SELECT doc_id FROM memory_access_record WHERE access_time > ?
                  )
                ORDER BY c.weight ASC
                LIMIT 20
            """, (cold_max_weight, cold_cutoff)).fetchall()
            cold_candidates = [dict(r) for r in cold_rows]
        except Exception as e:
            logger.warning("Cold candidate scan failed: %s", e)
            cold_candidates = []

        # ── 热候选：weight >= hot_min_weight + importance P0/P1 + evolution_tier != 'hot' ──
        try:
            hot_rows = conn.execute("""
                SELECT c.doc_id, c.compact_content, c.importance, c.weight, c.evolution_tier
                FROM memory_classify c
                JOIN document_files d ON c.doc_id = d.id
                WHERE d.is_deleted = 0
                  AND (c.evolution_tier IS NULL OR c.evolution_tier != 'hot')
                  AND c.weight >= ?
                  AND c.importance IN ('P0', 'P1')
                ORDER BY c.weight DESC
                LIMIT 10
            """, (hot_min_weight,)).fetchall()
            hot_candidates = [dict(r) for r in hot_rows]
        except Exception as e:
            logger.warning("Hot candidate scan failed: %s", e)
            hot_candidates = []

        return cold_candidates, hot_candidates
