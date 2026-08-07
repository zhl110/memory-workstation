"""EvolutionMixin — 进化系统：correction / decay / tier / always_load

C++ Storage 委派 + Python 层的 always_load（meta JSON 操作）。
MemoryClient 通过继承使用。
"""
from __future__ import annotations
import json
from typing import Any, Optional, TYPE_CHECKING

from .types import (
    CandidatesDict, CandidateDict, IncrementCorrectionDict, EvolutionStatsDict,
)
from .utils import cpp_to_dict

if TYPE_CHECKING:
    from .client import MemoryClient


class EvolutionMixin:
    """进化系统（correction / decay / tier evolution / always_load）"""

    _cpp_storage: object
    _conn: object  # sqlite3.Connection
    _pool_conn: object | None

    def decay_weights(self: MemoryClient, factor: float = 0.8,
                      min_weight: int = 10, decay_days: int = 30) -> int:
        count = self._cpp_storage.decay_weights(factor, min_weight, decay_days)
        if count > 0:
            self._auto_tier_transition()
        return count

    def get_candidates(self: MemoryClient, scope: str = "own", cold_days: int = 90,
                       cold_max_weight: int = 20, hot_min_weight: int = 80) -> CandidatesDict:
        cold, hot = [], []
        if scope in ("own", "all"):
            cold, hot = self._get_own_candidates_impl(cold_days, cold_max_weight, hot_min_weight)
        if scope in ("pool", "all") and self._pool_conn:
            pc, ph = self._get_pool_candidates_impl(cold_days, cold_max_weight, hot_min_weight)
            cold.extend(pc)
            hot.extend(ph)
        return {"cold": cold, "hot": hot}

    def _get_own_candidates_impl(self, cold_days: int, cold_max_weight: int,
                                 hot_min_weight: int) -> tuple[list, list]:
        results = self._cpp_storage.get_own_candidates(cold_days, cold_max_weight)
        cold = []
        hot = []
        for r in results:
            d = cpp_to_dict(r)
            if d.get("importance", "P2") in ("P0", "P1"):
                hot.append(d)
            else:
                cold.append(d)
        return cold, hot

    def _get_pool_candidates_impl(self, cold_days: int, cold_max_weight: int,
                                  hot_min_weight: int) -> tuple[list, list]:
        """从共享知识库读候选"""
        if not self._pool_conn:
            return [], []
        try:
            rows = self._pool_conn.execute(
                """SELECT c.doc_id, c.summary, c.importance, c.weight
                   FROM memory_classify c
                   JOIN document_files d ON c.doc_id = d.id
                   WHERE d.is_deleted = 0 AND c.compact_content != ''"""
            ).fetchall()
            cold, hot = [], []
            for r in rows:
                d = {"doc_id": r["doc_id"], "summary": r["summary"],
                     "importance": r["importance"], "weight": r["weight"]}
                if d["importance"] in ("P0", "P1") and d["weight"] >= hot_min_weight:
                    hot.append(d)
                elif d["weight"] <= cold_max_weight:
                    cold.append(d)
            return cold, hot
        except Exception:
            return [], []

    def increment_correction(self: MemoryClient, pattern: str, summary: str,
                             context: str = "") -> IncrementCorrectionDict:
        count, is_new = self._cpp_storage.increment_correction(pattern, summary, context)
        return {"count": count, "is_new": is_new}

    def get_correction_pending(self: MemoryClient, min_count: int = 3) -> list[dict[str, Any]]:
        return [cpp_to_dict(r) for r in self._cpp_storage.get_correction_pending(min_count)]

    def suppress_correction(self: MemoryClient, pattern: str) -> bool:
        return self._cpp_storage.suppress_correction(pattern)

    def promote_correction(self: MemoryClient, pattern: str) -> bool:
        return self._cpp_storage.promote_correction(pattern)

    def list_corrections(self: MemoryClient, limit: int = 20) -> list[dict[str, Any]]:
        return [cpp_to_dict(r) for r in self._cpp_storage.list_corrections(limit)]

    def log_event(self: MemoryClient, event_type: str, trigger: str,
                  target_doc_id: Optional[int] = None,
                  detail: str = "", certainty: float = 0.0) -> int:
        return self._cpp_storage.log_event(event_type, trigger, target_doc_id or 0, detail, certainty)

    def get_evolution_log(self: MemoryClient, event_type: Optional[str] = None,
                          limit: int = 20) -> list[dict[str, Any]]:
        return [cpp_to_dict(e) for e in self._cpp_storage.get_evolution_log(event_type or "", limit)]

    def apply_tier_change(self: MemoryClient, doc_id: int, from_tier: str,
                          to_tier: str, reason: str = "") -> bool:
        return self._cpp_storage.apply_tier_change(doc_id, from_tier, to_tier, reason)

    def get_tier_history(self: MemoryClient, doc_id: Optional[int] = None,
                         limit: int = 20) -> list[dict[str, Any]]:
        return [cpp_to_dict(t) for t in self._cpp_storage.get_tier_history(doc_id or 0, limit)]

    def set_always_load(self: MemoryClient, doc_id: int, enabled: bool = True) -> bool:
        return self._cpp_storage.set_always_load(doc_id, enabled)

    def get_always_load(self: MemoryClient, limit: int = 5) -> list[dict[str, Any]]:
        return [cpp_to_dict(r) for r in self._cpp_storage.get_always_load(limit)]

    def clear_always_load(self: MemoryClient, doc_id: Optional[int] = None) -> int:
        return self._cpp_storage.clear_always_load(doc_id or 0)

    def get_evolution_stats(self: MemoryClient) -> EvolutionStatsDict:
        stats = self._cpp_storage.get_evolution_stats()
        by_tier = {}
        for k, v in stats.items():
            if k.startswith("tier_"):
                by_tier[k[5:]] = v
        return {
            "corrections_total": stats.get("corrections_total", 0),
            "corrections_pending": stats.get("corrections_pending", 0),
            "corrections_promoted": stats.get("corrections_promoted", 0),
            "evolution_events": stats.get("evolution_events", 0),
            "tier_changes": stats.get("tier_changes", 0),
            "by_tier": by_tier,
        }
