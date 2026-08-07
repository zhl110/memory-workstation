"""TierMixin — 记忆分层 / 时序管理 / 实体解析（v0.20.0）

纯 C++ Storage 委派。MemoryClient 通过继承使用。
"""
from __future__ import annotations
from pathlib import Path
from typing import TYPE_CHECKING

from .types import CandidateDict, MemoryDetailDict
from .utils import cpp_to_dict

if TYPE_CHECKING:
    from .client import MemoryClient


class TierMixin:
    """记忆分层 / 时序管理 / 实体解析"""

    _cpp_storage: object
    _conn: object  # sqlite3.Connection, for entity lookup in get_current_valid

    def set_tier(self: MemoryClient, doc_id: int, tier: str, reason: str = "") -> bool:
        return self._cpp_storage.set_tier(doc_id, tier, reason)

    def get_tier(self: MemoryClient, doc_id: int) -> str:
        return self._cpp_storage.get_tier(doc_id)

    def get_hot_memories(self: MemoryClient, limit: int = 100) -> list[CandidateDict]:
        return [cpp_to_dict(r) for r in self._cpp_storage.get_hot_memories(limit)]

    def archive_memory(self: MemoryClient, doc_id: int, reason: str = "") -> bool:
        return self._cpp_storage.archive_memory(doc_id, reason)

    def forget_memory(self: MemoryClient, doc_id: int, reason: str = "") -> bool:
        # 校验 doc 真实存在，避免对不存在的 id 误报"已删除"（假删除）
        try:
            row = self._conn.execute(
                "SELECT 1 FROM memory_classify WHERE doc_id = ?", (doc_id,)
            ).fetchone()
        except Exception:
            row = None
        if row is None:
            return False

        ok = self._cpp_storage.forget_memory(doc_id, reason)
        if ok:
            # 清理 memory_fts 残留行，否则软删除后仍会被 FTS 搜索到
            try:
                self._conn.execute("DELETE FROM memory_fts WHERE doc_id = ?", (doc_id,))
                self._conn.commit()
            except Exception:
                pass
            try:
                from .sync import MemorySync
                export_dir = Path(self._db_path).parent / "memory_export_all"
                sync = MemorySync(self._db_path, str(export_dir), conn=self._conn)
                sync.delete_one_md(doc_id)
            except Exception:
                pass
        return ok

    def set_valid_time(self: MemoryClient, doc_id: int,
                       valid_from: str = "", valid_until: str = "") -> bool:
        return self._cpp_storage.set_valid_time(doc_id, valid_from, valid_until)

    def get_current_valid(self: MemoryClient, entity_name: str) -> list[MemoryDetailDict]:
        results = self._cpp_storage.get_current_valid(entity_name)
        return [{
            "doc_id": r.doc_id,
            "file_path": "",
            "summary": r.summary or "",
            "label": r.label,
            "importance": r.importance,
            "weight": r.weight,
            "category": r.category or "",
            "sub_category": r.sub_category or "",
            "depth": "",
            "entities": [],  # entities 需要额外查询，按需调用 get_memory 获取
        } for r in results]

    def resolve_entity(self: MemoryClient, name: str, alias: str) -> bool:
        return self._cpp_storage.resolve_entity(name, alias)

    def update_entity_mention(self: MemoryClient, entity_id: int,
                              memory_id: int, context: str = "") -> bool:
        return self._cpp_storage.update_entity_mention(entity_id, memory_id, context)
