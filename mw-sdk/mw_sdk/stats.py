"""StatsMixin — 统计 / 健康检查 / Agent 注册

C++ 引擎委派 + Python 层的 agent 注册表管理。
MemoryClient 通过继承使用。
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

from .types import (
    StatsDict, HealthCheckDict, AgentInfoDict, AgentRegisterDict, AgentUnregisterDict,
)

if TYPE_CHECKING:
    from .client import MemoryClient


class StatsMixin:
    """统计 / 健康检查 / Agent 注册"""

    _conn: object
    _cpp_storage: object
    _cpp_search: object
    _cpp_graph: object
    _pool_conn: object | None
    _db_path: str

    def get_stats(self: MemoryClient) -> StatsDict:
        total_docs = self._conn.execute(
            "SELECT COUNT(*) FROM document_files WHERE is_deleted=0"
        ).fetchone()[0]
        total_memories = self._conn.execute(
            "SELECT COUNT(*) FROM memory_classify"
        ).fetchone()[0]
        label_rows = self._conn.execute(
            "SELECT label, COUNT(*) as cnt FROM memory_classify GROUP BY label"
        ).fetchall()
        by_label = {r["label"]: r["cnt"] for r in label_rows}
        imp_rows = self._conn.execute(
            "SELECT importance, COUNT(*) as cnt FROM memory_classify GROUP BY importance"
        ).fetchall()
        by_importance = {r["importance"]: r["cnt"] for r in imp_rows}
        cross_ref_count = self._conn.execute(
            "SELECT COUNT(*) FROM memory_cross_ref"
        ).fetchone()[0]
        type_rows = self._conn.execute(
            "SELECT relation_type, COUNT(*) as cnt FROM memory_cross_ref GROUP BY relation_type"
        ).fetchall()
        cross_ref_by_type = {r["relation_type"]: r["cnt"] for r in type_rows}
        total_with_content = self._conn.execute(
            "SELECT COUNT(*) FROM memory_classify WHERE compact_content != ''"
        ).fetchone()[0]
        avg_refs = round(cross_ref_count / total_with_content, 1) if total_with_content > 0 else 0
        orphan = self._conn.execute(
            """SELECT COUNT(*) FROM memory_classify c
               WHERE c.compact_content != ''
               AND NOT EXISTS (SELECT 1 FROM memory_cross_ref cr
                               WHERE cr.doc_id = c.doc_id)"""
        ).fetchone()[0]
        entity_count = self._conn.execute(
            "SELECT COUNT(DISTINCT entity_name) FROM memory_entity"
        ).fetchone()[0]
        return {
            "total_docs": total_docs,
            "total_memories": total_memories,
            "by_label": by_label,
            "by_importance": by_importance,
            "cross_ref_count": cross_ref_count,
            "cross_ref_by_type": cross_ref_by_type,
            "avg_refs_per_doc": avg_refs,
            "orphan_count": orphan,
            "entity_count": entity_count,
            "correction_count": self._conn.execute(
                "SELECT COUNT(*) FROM correction_log"
            ).fetchone()[0],
            "evolution_events": self._conn.execute(
                "SELECT COUNT(*) FROM evolution_log"
            ).fetchone()[0],
            "tier_changes": self._conn.execute(
                "SELECT COUNT(*) FROM tier_history"
            ).fetchone()[0],
        }

    def health_check(self: MemoryClient) -> HealthCheckDict:
        result = {}
        h = self._cpp_storage.health_check()
        result["database"] = {"status": "ok" if h.db_ok else "error",
                              "fts5_entries": h.fts5_entries,
                              "fts5_behind": h.fts5_behind}
        if self._pool_conn:
            try:
                self._pool_conn.execute("SELECT 1")
                result["pool"] = {"status": "ok"}
            except Exception as e:
                result["pool"] = {"status": "error", "detail": str(e)}
        else:
            result["pool"] = {"status": "warning", "detail": "未连接"}
        result["c_engine"] = {"status": "ok", "detail": "C++ 引擎"}
        has_hnsw = self._cpp_search.has_vector_index() if self._cpp_search else False
        result["vector"] = {"status": "ok" if has_hnsw else "warning",
                            "detail": "HNSW 已就绪" if has_hnsw else "未构建"}
        try:
            stats = self._cpp_graph.get_stats()
            orphan_status = "ok" if stats.orphan_rate < 0.1 else "warning" if stats.orphan_rate < 0.3 else "critical"
            result["graph"] = {"status": orphan_status,
                               "nodes": stats.total_nodes,
                               "edges": stats.total_edges,
                               "orphan_rate": stats.orphan_rate}
        except Exception as e:
            result["graph"] = {"status": "error", "detail": str(e)}
        return result

    def record_access(self: MemoryClient, doc_id: int) -> None:
        self._cpp_storage.record_access(doc_id)

    def promote_to_global(self: MemoryClient, min_weight: int = 100,
                          min_access: int = 10) -> list[int]:
        rows = self._conn.execute(
            """SELECT c.doc_id FROM memory_classify c
               JOIN memory_access_record a ON c.doc_id = a.doc_id
               WHERE c.scope = 'project' AND c.weight >= ? AND c.workspace_id != 'global'
               GROUP BY c.doc_id
               HAVING COUNT(DISTINCT a.id) >= ?
               LIMIT 50""",
            (min_weight, min_access),
        ).fetchall()
        promoted = []
        for r in rows:
            self._conn.execute(
                "UPDATE memory_classify SET scope = 'global', workspace_id = 'global' WHERE doc_id = ?",
                (r["doc_id"],),
            )
            promoted.append(r["doc_id"])
        if promoted:
            self._conn.commit()
        return promoted

    def get_promotion_candidates(self: MemoryClient, min_weight: int = 80) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT c.doc_id, c.summary, c.weight, c.scope
               FROM memory_classify c
               WHERE c.scope = 'project' AND c.weight >= ?
               ORDER BY c.weight DESC LIMIT 20""",
            (min_weight,),
        ).fetchall()
        return [dict(r) for r in rows]

    def _record_access_batch(self: MemoryClient, doc_ids: list[int]) -> None:
        if not doc_ids:
            return
        self._cpp_storage.record_access_batch(doc_ids)

    def register_agent(self: MemoryClient, name: str, agent_type: str = "custom",
                       db_path: str = "") -> AgentRegisterDict:
        from .utils import get_agent_db
        from .client import MemoryClient as MC
        if not db_path:
            db_path = get_agent_db(name)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        agent_client = MC(db_path)
        agent_client.init_schema()
        agent_client.close()
        registry = self._load_registry()
        registry[name] = {
            "name": name, "type": agent_type,
            "db": db_path, "status": "active",
            "registered_at": str(Path(db_path).stat().st_mtime),
        }
        self._save_registry(registry)
        return {"name": name, "db_path": db_path, "status": "active"}

    def unregister_agent(self: MemoryClient, name: str,
                         delete_db: bool = False) -> AgentUnregisterDict:
        registry = self._load_registry()
        if name not in registry:
            return {"name": name, "deleted": False}
        agent_info = registry.pop(name)
        self._save_registry(registry)
        if delete_db and "db" in agent_info:
            db_path = agent_info["db"]
            if os.path.exists(db_path):
                os.remove(db_path)
        return {"name": name, "deleted": True}

    def list_agents(self: MemoryClient) -> list[AgentInfoDict]:
        registry = self._load_registry()
        return [info for info in registry.values()]

    def get_agent(self: MemoryClient, name: str) -> AgentInfoDict | None:
        registry = self._load_registry()
        return registry.get(name)

    def _load_registry(self: MemoryClient) -> dict:
        from .utils import get_agents_registry_path
        path = Path(get_agents_registry_path())
        if not path.exists():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_registry(self: MemoryClient, registry: dict) -> None:
        from .utils import get_agents_registry_path
        path = Path(get_agents_registry_path())
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(registry, f, ensure_ascii=False, indent=2)
