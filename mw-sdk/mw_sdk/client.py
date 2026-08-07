"""MemoryClient — 纯数据引擎，零 LLM 依赖

SDK 不再调用任何 LLM API。classify / fuse / rerank 由上层 Agent 本人完成。
SDK 只负责：SQLite 存取 + FTS5 索引 + 交叉引用 + 导出/备份。

架构：
  skill (Claude Code) ─┐
  skill (Codex)      ──┼─ Agent 本人做分类/融合，调 sdk 存取
  exe (桌面软件)      ──┘
                   │ import
                   ▼
              MemoryClient (本文件)
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import re
import shutil
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .utils import get_agent_name, _DEFAULT_DATA_DIR, validate_utf8, safe_truncate
from .security import detect_secrets, redact_secrets
from .audit import AuditLog
from .schema import SCHEMA_SQL, INDEX_TEMPLATE, LOG_TEMPLATE, LINT_TEMPLATE
from .types import (
    SearchResultDict, SearchExplainDict, MemoryDetailDict, LinkedDict,
    RuleDict, EntityDict, EntityMiniDict,
    GraphStatsDict, BfsNodeDict, PathNodeDict, CrossRefCandidateDict,
    StatsDict, HealthCheckDict, HealthComponentDict,
    CandidatesDict, CandidateDict, IncrementCorrectionDict, EvolutionStatsDict,
    CrawlStatsDict, RebuildLinksDict, CleanupStatsDict,
    VectorBuildDict, VectorStatsDict,
    AgentInfoDict, AgentRegisterDict, AgentUnregisterDict,
    CrossRefRefDict, ClassificationDict,
    SceneDict, SceneRuleDict, EmotionDict, SessionStateDict,
    TierChangeDict,
)
from .scene import SceneMixin
from .tier import TierMixin
from .graph import GraphMixin
from .evolution import EvolutionMixin
from .stats import StatsMixin

# C++ 引擎（可选）
try:
    from ._core import mw_core as _cpp_core
    _CPP_AVAILABLE = _cpp_core is not None
except ImportError:
    _cpp_core = None
    _CPP_AVAILABLE = False

import logging
logger = logging.getLogger(__name__)

if _CPP_AVAILABLE:
    logger.info("MW C++ 引擎已加载 (v%s)", _cpp_core.version())

def _cpp_to_dict(obj):
    """Convert pybind11 struct to dict"""
    return {attr: getattr(obj, attr) for attr in dir(obj) if not attr.startswith('_')}


class MemoryClient(SceneMixin, TierMixin, GraphMixin, EvolutionMixin, StatsMixin):
    """纯数据引擎 — SQLite 存取 + FTS5 索引，不含 LLM 调用

    ⚠️ 排查要点：
    - 三个Agent（Claude/MiMo/Codex）共用 meta_agents.sqlite
    - export_md() 导出目录统一为 memory_export_all/
    - _sanitize_filename() 如果炸 OSError，检查是否漏了过滤 \\n 等字符
    - 每次 search() 会自动记访问 + weight+5（上限100）
    """

    # ── Rules helpers ──────────────────────────────────────────
    @staticmethod
    def _get_rules(conn: sqlite3.Connection, category: str = "", limit: int = 20) -> list[dict]:
        if category:
            rows = conn.execute(
                "SELECT id, rule_text, category, sub_category, priority, confidence, "
                "conflict_with, complements "
                "FROM global_rules WHERE status='active' AND category LIKE ? "
                "ORDER BY confidence DESC LIMIT ?",
                (f"%{category}%", limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, rule_text, category, sub_category, priority, confidence, "
                "conflict_with, complements "
                "FROM global_rules WHERE status='active' "
                "ORDER BY confidence DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _get_rules_from_pool(pool_conn: sqlite3.Connection, category: str = "",
                             limit: int = 20) -> list[dict]:
        try:
            if category:
                rows = pool_conn.execute(
                    "SELECT id, rule_text, category, sub_category, priority, confidence, "
                    "conflict_with, complements "
                    "FROM global_rules WHERE status='active' AND category LIKE ? "
                    "ORDER BY confidence DESC LIMIT ?",
                    (f"%{category}%", limit),
                ).fetchall()
            else:
                rows = pool_conn.execute(
                    "SELECT id, rule_text, category, sub_category, priority, confidence, "
                    "conflict_with, complements "
                    "FROM global_rules WHERE status='active' "
                    "ORDER BY confidence DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error as e:
            logger.warning("_get_rules 查询失败: %s", e)
            return []

    @staticmethod
    def _get_entities(conn: sqlite3.Connection, name: str = "", limit: int = 50) -> list[dict]:
        if name:
            rows = conn.execute(
                "SELECT e.doc_id, e.entity_name, e.entity_type, e.weight, c.summary "
                "FROM memory_entity e "
                "JOIN memory_classify c ON c.doc_id = e.doc_id "
                "WHERE e.entity_name LIKE ? "
                "ORDER BY e.weight DESC LIMIT ?",
                (f"%{name}%", limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT e.doc_id, e.entity_name, e.entity_type, e.weight, c.summary "
                "FROM memory_entity e "
                "JOIN memory_classify c ON c.doc_id = e.doc_id "
                "ORDER BY e.weight DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # 默认大池子路径（exe 的 meta.sqlite，只读）
    _DEFAULT_POOL = os.path.join(_DEFAULT_DATA_DIR, "meta.sqlite")

    # 全局 watcher 注册表（同 db_path 只启动一个监听）
    _watchers: dict[str, object] = {}
    _watcher_refcount: dict[str, int] = {}

    def __init__(self, db_path: str, mode: str = "rrf", k: int = 60,
                 weights: tuple[float, float, float] = (0.4, 0.2, 0.4),
                 watch_md: bool = False):
        """初始化客户端

        Args:
            db_path: SQLite 数据库文件路径，如 "D:/MemoryWorkstation/.memory-workstation/meta_agents.sqlite"
            mode: 搜索模式
                - "rrf": 使用 RRF 融合（更稳定的排序，默认值）
                - "hybrid": RRF + Ebbinghaus 遗忘曲线
            k: RRF 参数，控制排名融合的平滑度（默认60）
            weights: 三路融合权重 (fts5, entity, vector)，默认 (0.4, 0.2, 0.4) FTS5 与向量均衡
            watch_md: 是否自动启动 MD 文件监听（watchdog，同 db_path 只启一个）

        ⚠️ 排查：
        - 大池子自动连接是只读的（mode=ro），连不上也不报错（静默降级）
        - db_path 由 MW_AGENT_ID 环境变量决定，不设则默认 claude
        - 同一进程可创建多个 MemoryClient 实例连不同 db，但注意写锁冲突
        """
        self._db_path = str(Path(db_path))
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        # C++ 引擎（优先）或 Python 引擎
        self._use_cpp = False
        self._cpp_storage = None
        self._cpp_search = None
        self._cpp_graph = None
        self._cpp_rules = None
        self._conn = None
        self._search_mode = mode
        self._search_k = k
        self._search_weights = weights

        if _CPP_AVAILABLE:
            try:
                self._cpp_storage = _cpp_core.Storage(self._db_path)
                self._cpp_storage.init_schema()  # 幂等：建全部表（含 FTS5）
                # 映射搜索模式
                mode_map = {"rrf": _cpp_core.SearchMode.RRF,
                           "hybrid": _cpp_core.SearchMode.Hybrid}
                config = _cpp_core.SearchConfig()
                config.mode = mode_map.get(mode, _cpp_core.SearchMode.RRF)
                config.k = k
                config.set_weights(list(weights))
                self._cpp_search = _cpp_core.SearchEngine(self._cpp_storage, config)
                self._cpp_graph = _cpp_core.GraphEngine(self._cpp_storage)
                self._cpp_rules = _cpp_core.Rules(self._cpp_storage)
                self._use_cpp = True
                logger.info("使用 C++ 引擎")
                try:
                    from .embed import init_embedding, MODELS_DIR
                    ok = init_embedding(self._cpp_storage)
                    logger.info("ONNX 模型加载%s (%s)", "成功" if ok else "失败", MODELS_DIR)
                except Exception as e:
                    logger.warning("ONNX 模型加载失败: %s", e)
                # 自动加载已保存的 HNSW 索引
                index_path = Path(self._db_path).parent / "vector_index.hnsw"
                if index_path.exists():
                    try:
                        raw = index_path.read_bytes()
                        # C++ binding 期望 Sequence[str]，将 bytes 转为 list[str]
                        raw_list = [chr(b) for b in raw]
                        _cpp_core.search_engine_load_vector_index(
                            self._cpp_search, raw_list)
                        if self._cpp_search.has_vector_index():
                            logger.info("向量索引已加载 (%s)", index_path)
                        else:
                            logger.warning("向量索引加载后无效（数据损坏？自动重建中…）: %s", index_path)
                    except Exception as e:
                        logger.warning("向量索引加载失败: %s", e)
            except Exception as e:
                logger.warning("C++ 引擎初始化失败: %s", e)
                self._cpp_storage = None

        if not self._use_cpp:
            raise RuntimeError(
                "C++ 引擎不可用，请先编译: cd cpp && cmake --build build\n"
                "Python fallback 已移除，仅支持 C++ 引擎"
            )

        # C++ 模式下仍创建 Python 连接（用于导出/CLI 等辅助操作）
        # 文件数据库：两边指向同一文件，共享数据
        # :memory: 数据库：两边各自独立（C++ 是主引擎，Python 仅用于兼容）
        self._write_lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        # 容错：损坏的UTF-8用替换字符代替，避免整个查询失败
        self._conn.text_factory = lambda x: x.decode('utf-8', 'replace')
        self._ensure_fts5()
        self._ensure_synonyms()

        # ZVEC 向量引擎
        self._zvec = None
        # 自动连大池子（图书馆，共享知识库，只读，不存在也不报错）
        self._pool_path = self._DEFAULT_POOL
        self._pool_conn: sqlite3.Connection | None = None
        self._connect_pool()
        # 审计日志（所有 Agent 共用一个）
        audit_log_path = Path(self._db_path).parent / "audit.log"
        self._audit = AuditLog(str(audit_log_path))

        # 自动启动 MD 文件监听（同 db_path 只启一个，引用计数管理）
        if watch_md:
            rc = MemoryClient._watcher_refcount
            rc[self._db_path] = rc.get(self._db_path, 0) + 1
            if self._db_path not in MemoryClient._watchers:
                try:
                    from .sync import MdWatcher
                    export_dir = Path(self._db_path).parent / "memory_export_all"
                    if export_dir.exists():
                        watcher = MdWatcher(self._db_path, str(export_dir))
                        watcher.start()
                        MemoryClient._watchers[self._db_path] = watcher
                        # 进程退出时自动清理
                        atexit.register(lambda p=self._db_path: MemoryClient.stop_watcher(p))
                except Exception:
                    pass  # 监听启动失败不阻塞初始化

    @classmethod
    def stop_watcher(cls, db_path: str | None = None):
        """停止文件监听"""
        if db_path:
            watcher = cls._watchers.pop(db_path, None)
            if watcher:
                watcher.stop()
        else:
            for w in cls._watchers.values():
                w.stop()
            cls._watchers.clear()

    def set_mode(self, mode: str) -> None:
        """切换搜索模式（运行时热切换，无需重建客户端）

        Args:
            mode: "rrf" / "hybrid"

        Examples:
            c = MemoryClient("db.sqlite")
            c.set_mode("hybrid")  # 切换到遗忘曲线模式
            results = c.search("关键词")
        """
        mode_map = {"rrf": _cpp_core.SearchMode.RRF,
                    "hybrid": _cpp_core.SearchMode.Hybrid}
        if mode not in mode_map:
            raise ValueError(f"Unknown mode: {mode!r}. Choose from: {list(mode_map.keys())}")
        config = _cpp_core.SearchConfig()
        config.mode = mode_map[mode]
        config.k = self._search_k
        config.set_weights(list(self._search_weights))
        self._cpp_search = _cpp_core.SearchEngine(self._cpp_storage, config)
        self._search_mode = mode
        logger.info("搜索模式切换为: %s", mode)

    def init_schema(self) -> None:
        """首次使用时建全部表（幂等，重复调用无副作用）

        建表清单：
        - document_files: 文档文件元数据
        - memory_classify: 分类结果
        - memory_entity: 实体关联
        - memory_fts: FTS5 全文索引
        - memory_cross_ref: 交叉引用（V7新增）
        - lint_log: 健康检查日志（V7新增）
        - global_rules: 全局规则

        同时自动创建配套辅助文件（index/log/lint_report）。
        """
        self._cpp_storage.init_schema()
        self._conn.executescript(SCHEMA_SQL)
        self._ensure_fts5()
        self._apply_migrations()
        self._conn.commit()
        self._ensure_companion_files()

    def _ensure_companion_files(self) -> None:
        """在 db 同目录下创建配套辅助文件（不存在时才创建）

        文件命名规则：
        - meta_agents.sqlite → memory_index_agents.md, log_agents.md, lint_report_agents.md
        - meta.sqlite        → memory_index.md,        log.md,        lint_report.md
        """
        db_path = Path(self._db_path)
        parent = db_path.parent
        stem = db_path.stem  # 如 meta_agents

        # 提取 agent 标识后缀
        suffix = ""
        if stem.startswith("meta_"):
            suffix = "_" + stem[5:]  # meta_agents → _agents

        files = {
            parent / f"memory_index{suffix}.md": INDEX_TEMPLATE,
            parent / f"log{suffix}.md": LOG_TEMPLATE,
            parent / f"lint_report{suffix}.md": LINT_TEMPLATE,
        }
        for path, template in files.items():
            if not path.exists():
                content = template.format(date=datetime.now().strftime("%Y-%m-%d %H:%M"))
                path.write_text(content, encoding="utf-8")

    def _apply_migrations(self) -> None:
        """安全执行增量迁移（ALTER TABLE 等非幂等操作）

        每个迁移先检查目标是否已生效，避免重复执行报错。
        """
        # V8: evolution_tier 列
        cols = [r[1] for r in self._conn.execute("PRAGMA table_info(memory_classify)")]
        if "evolution_tier" not in cols:
            self._conn.execute(
                "ALTER TABLE memory_classify ADD COLUMN evolution_tier TEXT DEFAULT 'warm'"
            )

        # Phase 1: meta 列（用于 always_load 等属性）
        if "meta" not in cols:
            self._conn.execute(
                "ALTER TABLE memory_classify ADD COLUMN meta TEXT DEFAULT '{}'"
            )

        # v0.7.0: workspace_id 列（旧数据库用 namespace）
        if "workspace_id" not in cols and "namespace" in cols:
            self._conn.execute(
                "ALTER TABLE memory_classify ADD COLUMN workspace_id TEXT DEFAULT 'default'"
            )
            self._conn.execute(
                "UPDATE memory_classify SET workspace_id = namespace WHERE workspace_id IS NULL"
            )

        # v0.16.0: keywords 列（搜索关键词索引）
        if "keywords" not in cols:
            self._conn.execute(
                "ALTER TABLE memory_classify ADD COLUMN keywords TEXT DEFAULT ''"
            )

        # v0.16.0: summary 列（搜索摘要）
        if "summary" not in cols:
            self._conn.execute(
                "ALTER TABLE memory_classify ADD COLUMN summary TEXT DEFAULT ''"
            )

        # v0.18.0: scope 列（记忆所属范围：global/project/session）
        if "scope" not in cols:
            self._conn.execute(
                "ALTER TABLE memory_classify ADD COLUMN scope TEXT DEFAULT 'global'"
            )

        # v0.18.0: project 列（项目名称，scope=project 时必填）
        if "project" not in cols:
            self._conn.execute(
                "ALTER TABLE memory_classify ADD COLUMN project TEXT DEFAULT ''"
            )

        # v0.19.0: scene 列（场景标签）
        if "scene" not in cols:
            self._conn.execute(
                "ALTER TABLE memory_classify ADD COLUMN scene TEXT DEFAULT ''"
            )

        # v0.19.0: emotion 列（情绪标签）
        if "emotion" not in cols:
            self._conn.execute(
                "ALTER TABLE memory_classify ADD COLUMN emotion TEXT DEFAULT ''"
            )

        # v0.20.0: 时序管理字段
        if "valid_from" not in cols:
            self._conn.execute(
                "ALTER TABLE memory_classify ADD COLUMN valid_from TEXT"
            )
        if "valid_until" not in cols:
            self._conn.execute(
                "ALTER TABLE memory_classify ADD COLUMN valid_until TEXT"
            )
        if "invalidated_by" not in cols:
            self._conn.execute(
                "ALTER TABLE memory_classify ADD COLUMN invalidated_by INTEGER DEFAULT 0"
            )

        # v0.20.0: 记忆分层字段
        if "tier" not in cols:
            self._conn.execute(
                "ALTER TABLE memory_classify ADD COLUMN tier TEXT DEFAULT 'warm'"
            )
        if "tier_updated_at" not in cols:
            self._conn.execute(
                "ALTER TABLE memory_classify ADD COLUMN tier_updated_at TEXT"
            )

    def _expand_query(self, query: str) -> str:
        """扩展搜索关键词：同义词（数据库驱动） + 数据驱动（已有关键词记忆）
        """
        expanded = [query]
        query_lower = query.lower()

        # 1. 同义词扩展（数据库驱动，空表自动降级无扩展）
        try:
            cur = self._conn.execute("SELECT word, synonyms FROM memory_synonyms")
            for word, syns_str in cur:
                if word in query_lower:
                    expanded.extend(syns_str.split())
        except sqlite3.Error:
            logger.warning("同义词表查询失败", exc_info=True)

        # 2. 数据驱动：从数据库命中记录的 keywords 中提取相关词
        try:
            like = f"%{query}%"
            cur = self._conn.execute(
                "SELECT keywords FROM memory_classify "
                "WHERE (summary LIKE ? OR compact_content LIKE ? OR keywords LIKE ?) "
                "AND keywords IS NOT NULL AND keywords != '' LIMIT 8",
                (like, like, like)
            )
            seen = set(expanded)
            for row in cur:
                for kw in row[0].split():
                    w = kw.strip()
                    if w not in seen and len(w) > 1:
                        seen.add(w)
                        expanded.append(w)
                        if len(expanded) >= 12:
                            break
                if len(expanded) >= 12:
                    break
        except sqlite3.Error:
            logger.warning("关键词扩展 SQL 查询失败", exc_info=True)

        # 去重（保留顺序）并限制长度
        seen = set()
        result = []
        for word in expanded:
            if word not in seen and len(word) > 1:
                seen.add(word)
                result.append(word)
                if len(result) >= 12:
                    break

        return " ".join(result)

    def search(self, query: str, top_k: int = 10, explain: bool = False,
               enable_vector: bool = True, enable_graph: bool = True,
               graph_expand_top: int = 3, graph_max_hops: int = 2,
               extra_keywords: list[str] | None = None,
               scene: str = "") -> list[SearchResultDict]:
        """搜索记忆 — 语义主导的多路融合搜索（含 C++ 图桥接多跳展开）

        Args:
            query: 搜索关键词
            top_k: 返回条数上限
            explain: 是否返回匹配详情（调试用）
            enable_vector: 是否启用向量语义搜索（默认开启，权重0.4）
            enable_graph: 是否启用图谱关联展开（默认开启）
            graph_expand_top: 对前 N 个结果展开图谱关联
            graph_max_hops: 图谱展开最大跳数
            extra_keywords: 额外关键词列表，传给 C++ 层在 FTS5 中用 OR 合并扩大覆盖
            scene: 场景过滤（空字符串=不过滤，如 "code" 只搜代码相关记忆）

        Returns:
            搜索结果列表，每条含 doc_id, summary, category, importance, weight, score
            explain=True时额外包含 explain 字段：
                - search_mode: 当前搜索模式 (rrf/hybrid)
                - matched_by: 匹配来源 (fts5/entity/vector/graph)
                - matches: 匹配的信号列表
                - signals: 原始信号分数
                - contributions: 各信号贡献度

        搜索权重（FTS5 与向量均衡）：
            - FTS5 BM25: 0.4（关键词匹配）
            - Entity: 0.2（实体匹配）
            - Vector: 0.4（语义匹配）

        ⚠️ 排查：
        - 搜索结果自动调 _record_access_batch → weight+5（上限100）
        - 图桥接由 C++ expand_graph() 实现，BFS 多跳 + 批量 SQL + graph_expand 信号
        """
        # 合并 extra_keywords 为字符串，传给 C++
        extra_str = " OR ".join(extra_keywords) if extra_keywords else ""

        # C++ 引擎：FTS5 + Entity 用原始查询，向量用扩展查询的 embedding
        # 注意：graph_expand_top/graph_max_hops 必须显式传值，不依赖 C++ 默认值
        if enable_vector:
            expanded = self._expand_query(query)
            query_vec = _cpp_core.storage_embed_text(self._cpp_storage, expanded)
            cpp_results = self._cpp_search.search_with_embedding(
                query, query_vec, top_k=top_k * 2,
                enable_graph=enable_graph,
                graph_expand_top=graph_expand_top,
                graph_max_hops=graph_max_hops,
                extra_keywords=extra_str
            )
        else:
            cpp_results = self._cpp_search.search(
                query, top_k=top_k * 2, enable_vector=False,
                enable_graph=enable_graph,
                graph_expand_top=graph_expand_top,
                graph_max_hops=graph_max_hops,
                extra_keywords=extra_str
            )
        # 统一在 Python 层记录访问（两条路径行为一致，避免跨语言回调开销）
        if cpp_results:
            self._cpp_storage.record_access_batch(
                [r.doc_id for r in cpp_results]
            )
            self._auto_tier_transition()
        # 转换为 dict 格式
        results = []
        for r in cpp_results:
            results.append({
                "doc_id": r.doc_id,
                "summary": r.summary,
                "category": r.category,
                "importance": r.importance,
                "weight": r.weight,
                "score": r.score,
                "scope": r.scope if hasattr(r, 'scope') else "",
                "project": r.project if hasattr(r, 'project') else "",
                "signals": r.signals if hasattr(r, 'signals') else {},
            })
        
        # explain字段始终存在（建在全量结果上，不截断）
        for r in results:
            if explain:
                signals = r.get("signals", {})
                matches = []
                if signals.get("bm25", 0) > 0 or signals.get("bm25_rank"):
                    matches.append("fts5")
                if signals.get("entity", 0) > 0 or signals.get("entity_rank"):
                    matches.append("entity")
                if signals.get("vector", 0) > 0 or signals.get("vector_rank"):
                    matches.append("vector")
                if signals.get("graph_expand"):
                    matches.append("graph")

                contributions = {}
                total_score = r.get("score", 0) or 1
                if signals.get("bm25", 0) > 0:
                    contributions["fts5"] = round(signals["bm25"] * 0.5 / total_score, 2)
                if signals.get("entity", 0) > 0:
                    contributions["entity"] = round(signals["entity"] * 0.3 / total_score, 2)
                if signals.get("vector", 0) > 0:
                    contributions["vector"] = round(signals["vector"] * 0.2 / total_score, 2)
                if signals.get("rrf_score"):
                    contributions["rrf"] = round(signals["rrf_score"] / total_score, 2)

                r["explain"] = {
                    "query": query,
                    "search_mode": self._search_mode,
                    "matched_by": "+".join(matches) if len(matches) > 1 else (matches[0] if matches else "unknown"),
                    "matches": matches,
                    "signals": signals,
                    "contributions": contributions,
                }
            else:
                r["explain"] = None

        # scene 过滤（在截断之前，避免图谱展开的结果被误丢）
        if scene:
            scene_filtered = []
            for r in results:
                doc_id = r.get("doc_id")
                if doc_id:
                    row = self._conn.execute(
                        "SELECT scene FROM memory_classify WHERE doc_id = ?",
                        (doc_id,)
                    ).fetchone()
                    mem_scene = row[0] if row else ""
                    if mem_scene == scene or not mem_scene:
                        scene_filtered.append(r)
                else:
                    scene_filtered.append(r)
            results = scene_filtered

        # 最终截断（C++ 已返回 top_k*2，这里截到 top_k）
        return results[:top_k]

    def get_all_related(self, query: str, top_k: int = 5, max_results: int = 20) -> list[MemoryDetailDict]:
        """获取所有相关记忆（直接+间接），供Agent挑选
        
        Args:
            query: 搜索关键词
            top_k: 直接搜索返回数量
            max_results: 最终返回数量上限
        
        Returns:
            所有相关记忆列表（去重）
        """
        # 1. 直接搜索
        direct = self.search(query, top_k)
        
        # 2. 收集所有相关ID
        all_ids = set()
        for r in direct:
            all_ids.add(r["doc_id"])
            
            # 展开关联
            try:
                refs = self.get_linked(r["doc_id"])
                for ref in refs:
                    all_ids.add(ref["doc_id"])
            except Exception as e:
                logger.debug(f"get_linked失败: {e}")
        
        # 3. 读取所有相关记忆（限制数量）
        all_memories = []
        for doc_id in list(all_ids)[:max_results]:
            try:
                memory = self.get_memory(doc_id)
                if memory:
                    all_memories.append(memory)
            except Exception as e:
                logger.debug(f"get_memory失败: {e}")
        
        return all_memories

    def _connect_pool(self):
        """自动连图书馆（大池子），连不上也不报错

        ⚠️ 排查：
        - 大池子是 meta.sqlite（exe 桌面软件的数据库），只读打开（mode=ro）
        - 不存在时不报错，self._pool_conn 保持 None
        - 调用 set_pool_path() 可覆盖默认大池子路径
        - 所有 Agent 共享大池子，不是 exe 私有
        """
        try:
            if Path(self._pool_path).exists():
                self._pool_conn = sqlite3.connect(
                    f"file:{self._pool_path}?mode=ro",
                    uri=True,
                    check_same_thread=False,
                )
                self._pool_conn.row_factory = sqlite3.Row
                self._pool_conn.text_factory = lambda x: x.decode('utf-8', 'replace')
        except Exception as e:
            if Path(self._pool_path).exists():
                logger.warning("大池子连接失败（共享知识库不可用）: %s", e)
            else:
                logger.info("大池子不存在（首次运行可忽略）: %s", e)
            self._pool_conn = None

    def set_pool_path(self, pool_path: str) -> None:
        """设置大池子路径并重新连接（覆盖默认值）

        Args:
            pool_path: 大池子数据库文件路径，如 "D:/MemoryWorkstation/.memory-workstation/meta.sqlite"
        """
        self._pool_path = pool_path
        self._connect_pool()

    def get_rules(self, category: str = "", limit: int = 20) -> list[RuleDict]:
        """读全局规则 — 自己库 + 大池子合并

        Args:
            category: 按分类过滤，空字符串表示全部
            limit: 返回条数上限

        Returns:
            规则列表，每条含 id, rule_text, category, priority, confidence
        """
        # 先查自己库
        rules = self._get_rules(self._conn, category, limit)
        # 不够就从大池子补
        if len(rules) < limit and self._pool_conn:
            pool_rules = self._get_rules_from_pool(self._pool_conn, category, limit - len(rules))
            existing_ids = {r.get("id") for r in rules}
            for r in pool_rules:
                if r.get("id") not in existing_ids:
                    rules.append(r)
                    existing_ids.add(r.get("id"))
        return rules[:limit]

    def get_entities(self, name: str = "", limit: int = 50) -> list[EntityDict]:
        """读实体列表

        Args:
            name: 按实体名过滤，空字符串表示全部
            limit: 返回条数上限

        Returns:
            实体列表，每条含 doc_id, entity_name, entity_type, weight
        """
        return self._get_entities(self._conn, name, limit)

    def search_rules_by_intent(self, intent: str, top_k: int = 10,
                               min_confidence: float = 0.6) -> list[RuleDict]:
        """根据意图搜索需要的全局规则

        Args:
            intent: 任务意图（如 "code", "deploy", "config", "architecture", "debug", "general"）
            top_k: 返回条数上限
            min_confidence: 最小置信度阈值（低于此值不返回）

        Returns:
            规则列表，每条含 rule_text, category, priority, confidence

        降级方案：
            - 如果意图识别置信度低，返回空列表，让 Agent 使用通用规则
        """
        INTENT_KEYWORDS = {
            "code": (["代码规范", "命名", "注释", "代码风格"], 0.8),
            "deploy": (["打包", "部署", "版本号", "发布"], 0.9),
            "config": (["配置", "密钥", "安全", "环境变量"], 0.8),
            "architecture": (["架构", "设计", "选型", "方案"], 0.7),
            "debug": (["调试", "排错", "日志", "错误"], 0.8),
            "general": (["规则", "规范", "纪律", "必须", "禁止"], 0.5),
        }

        keywords, confidence = INTENT_KEYWORDS.get(intent, (["规则"], 0.3))

        if confidence < min_confidence:
            logger.debug("意图识别置信度 %s < %s，跳过规则搜索", confidence, min_confidence)
            return []

        rules = []
        seen = set()

        # 搜索全局规则表
        for kw in keywords:
            results = self.get_rules(category=kw, limit=5)
            for r in results:
                rule_id = r.get("id")
                if rule_id not in seen:
                    seen.add(rule_id)
                    r["confidence"] = confidence
                    rules.append(r)

        # 搜索 meta_rule 记忆
        for kw in keywords:
            results = self.search(kw, top_k=3)
            for r in results:
                rule_id = r.get("doc_id")
                # 获取 label
                row = self._conn.execute(
                    "SELECT label FROM memory_classify WHERE doc_id=?", (rule_id,)
                ).fetchone()
                label = row["label"] if row else ""
                if label == "meta_rule" and rule_id not in seen:
                    seen.add(rule_id)
                    r["confidence"] = confidence * 0.8
                    rules.append(r)

        # 获取 always_load 记忆
        always_load_rows = self._conn.execute(
            """SELECT doc_id, compact_content as summary, label, importance, weight
               FROM memory_classify
               WHERE json_extract(meta, '$.always_load') = 1 AND compact_content != ''"""
        ).fetchall()
        for row in always_load_rows:
            rule_id = row["doc_id"]
            if rule_id not in seen:
                seen.add(rule_id)
                rules.append({
                    "doc_id": rule_id,
                    "summary": row["summary"],
                    "label": row["label"],
                    "importance": row["importance"],
                    "weight": row["weight"],
                    "confidence": 1.0,
                })

        # 按 priority 排序
        priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4}
        rules.sort(key=lambda x: priority_order.get(x.get("priority", x.get("importance", "P2")), 2))

        return rules[:top_k]

    # ═══════════════════════════════════════════════════════════
    # 写入方法（上层 Agent 已完成分类/融合，这里纯写入）
    # ═══════════════════════════════════════════════════════════

    def _dedup_check(self, content: str, embed_vec: list[float] | None, vec_text_len: int = 0) -> int | None:
        """写入前去重检测：返回已有 doc_id 或 None

        两路检测：
        1. 精确 hash 匹配（memory_vector.content_hash）
        2. 向量余弦 > 0.99（仅对较长文本有效，避免短文本前缀误判）
        """
        # Level 1: Exact hash match
        content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()
        row = self._conn.execute(
            "SELECT doc_id FROM memory_vector WHERE content_hash = ? LIMIT 1",
            (content_hash,)
        ).fetchone()
        if row:
            return row[0]

        # Also check compact_content exact match (catch entries without vector layer)
        # C++ batch_ingest 将 compact_content 设为全文，update_memory 不覆盖 compact_content
        row = self._conn.execute(
            "SELECT doc_id FROM memory_classify WHERE compact_content = ? LIMIT 1",
            (content,)
        ).fetchone()
        if row:
            return row[0]

        # Level 2: Vector cosine > 0.99（只对较长文本有效，避免短文本前缀误判）
        if embed_vec and self._cpp_search.has_vector_index() and vec_text_len >= 15:
            vec_results = _cpp_core.search_engine_vector_search(
                self._cpp_search, embed_vec, top_k=1)
            if vec_results:
                doc_id, score = vec_results[0]
                if score > 0.99:
                    return doc_id

        return None

    def _detect_conflicts(self, doc_id: int, classification: dict) -> int:
        """写入后冲突检测：找同 label+同 category 的矛盾记忆，返回冲突边数"""
        label = classification.get("label", "")
        category = classification.get("category", "")
        if not label and not category:
            return 0

        rows = self._conn.execute(
            "SELECT doc_id, compact_content FROM memory_classify "
            "WHERE (label = ? OR content_category = ? OR sub_category = ?) "
            "AND doc_id != ? AND compact_content != '' "
            "ORDER BY weight DESC LIMIT 20",
            (label, category, category, doc_id)
        ).fetchall()
        if not rows:
            return 0

        new_content = classification.get("summary", "") or ""
        conflict_pairs = [
            ("禁止", "允许"), ("必须", "不要"), ("always", "never"),
            ("必须", "禁止"), ("要", "不要"), ("do", "don't"),
        ]
        count = 0
        for other_id, other_content in rows:
            for a, b in conflict_pairs:
                has_a = a in new_content and b in other_content
                has_b = b in new_content and a in other_content
                if has_a or has_b:
                    try:
                        self._conn.execute(
                            "INSERT OR IGNORE INTO memory_cross_ref "
                            "(doc_id, related_doc_id, relation_type, note) "
                            "VALUES (?, ?, 'refute', ?)",
                            (doc_id, other_id, f"冲突: {a}/{b}")
                        )
                        self._conn.execute(
                            "INSERT OR IGNORE INTO memory_cross_ref "
                            "(doc_id, related_doc_id, relation_type, note) "
                            "VALUES (?, ?, 'refute', ?)",
                            (other_id, doc_id, f"冲突: {a}/{b}")
                        )
                        self._conn.commit()
                        count += 1
                    except Exception:
                        pass
        return count

    def _insert_classified(self, content: str, classification: dict, source: str = "sdk",
                           auto_refs: bool = True, ref_candidates: list[dict] | None = None,
                           ref_top_k: int = 10) -> int:
        """上层已分类，纯写入 → doc_id（仅 cli_ingest 内部调用）

        Args:
            content: 原始内容
            classification: 分类结果字典，格式见计划文档 §0.3
                {
                    "label": "meta_rule",           # 七个 label 之一
                    "importance": "P1",
                    "category": "打包部署",
                    "sub_category": "验证流程",
                    "summary": "打包前必须运行 python -c ... 验证语法",
                    "knowledge_type": "行为规则",
                    "applicability": "通用规则",     # → weight: 通用规则=95, 场景知识=50, 会话痕迹=20
                    "depth": "概述",
                    "content_type": "规则",
                    "entities": [{"name": "打包", "type": "concept"}, ...]
                }
            source: 来源标记

        Returns:
            新插入的 doc_id

        ⚠️ 排查：
        - applicability → weight 映射：通用规则=95, 场景知识=50, 会话痕迹=20
        - FTS5 写入失败不影响主流程（try/except pass），此时 FTS5 搜不到但 SQL LIKE 仍可用
        - entity 有 ON CONFLICT DO UPDATE，同名 entity 不会重复插入，而是 weight+1
        - 每次 insert 都 commit，大循环中注意性能
        - 安全：自动检测并脱敏 API 密钥（sk-/AKIA 等）
        - 审计：所有写入操作记录到 audit_*.log
        """
        # 编码校验：拒绝非法 UTF-8 序列
        validate_utf8(content)
        if classification:
            validate_utf8(classification.get("summary", "") or "")

        # 安全：检测并脱敏密钥
        secrets = detect_secrets(content)
        if secrets:
            content = redact_secrets(content)
            summary_text = classification.get("summary", "")
            if detect_secrets(summary_text):
                classification["summary"] = redact_secrets(summary_text)

        # C++ 引擎：单次 batch_ingest，内部一个事务
        if not self._use_cpp or not self._cpp_storage:
            raise RuntimeError("C++ 引擎不可用，无法写入记忆")

        # 预计算 embedding（用于 dedup 和后续向量索引，避免重复计算）
        embed_vec = None
        vec_text = classification.get("summary", "") or content
        if vec_text:
            try:
                embed_vec = _cpp_core.storage_embed_text(self._cpp_storage, vec_text[:5000])
            except Exception:
                pass

        # 去重检测（写入前）
        existing = self._dedup_check(content, embed_vec, vec_text_len=len(vec_text or ""))
        if existing:
            self._cpp_storage.record_access(existing)
            return existing

        cpp_cls = {k: str(v) for k, v in classification.items()
                   if k not in ("entities", "tags", "project_update") and v is not None}
        entity_pairs = [(e.get("name", "").strip(), e.get("type", "").strip())
                        for e in classification.get("entities", [])]
        entity_pairs = [(n, t) for n, t in entity_pairs if n and t]

        result = self._cpp_storage.batch_ingest(
            content, cpp_cls, entity_pairs, source,
            auto_refs=auto_refs, ref_top_k=ref_top_k)

        if result.doc_id < 0:
            return -1

        # 修复：C++ batch_ingest 没有写入 summary 字段，Python 层补充
        summary = classification.get("summary", "")
        if summary:
            try:
                self._conn.execute(
                    "UPDATE memory_classify SET summary = ? WHERE doc_id = ?",
                    (summary, result.doc_id)
                )
                self._conn.commit()
            except Exception:
                pass

        # 如果 Agent 提供了 title，更新 title（覆盖 C++ 自动生成的截取式 title）
        agent_title = classification.get("title", "")
        if agent_title:
            try:
                self._conn.execute(
                    "UPDATE memory_classify SET title = ? WHERE doc_id = ?",
                    (agent_title, result.doc_id)
                )
                self._conn.commit()
            except Exception:
                pass

        # 向量索引（复用预计算的 embedding）
        if embed_vec:
            try:
                content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()
                self._conn.execute(
                    "INSERT OR REPLACE INTO memory_vector (doc_id, embedding, content_hash) "
                    "VALUES (?, ?, ?)",
                    (result.doc_id, json.dumps(embed_vec), content_hash)
                )
                self._conn.commit()
                _cpp_core.search_engine_add_vector(
                    self._cpp_search, result.doc_id, embed_vec)
                self._save_vector_index()
            except Exception:
                pass

        # 冲突检测（写入后）
        conflicts = self._detect_conflicts(result.doc_id, classification)

        self._audit.log("insert", result.doc_id, {
            "source": source,
            "label": classification.get("label", ""),
            "category": classification.get("category", ""),
            "secrets_found": len(secrets) if secrets else 0,
            "conflicts": conflicts,
        })

        # v0.21.0: 项目连续上下文 — scope=project 且有 project_update 时更新快照
        # 只有 Agent 主动传入 project_update 才触发性更新，避免每次 ingest 重置 trigger
        _pu = classification.pop("project_update", None)
        if _pu and classification.get("scope") == "project" and classification.get("project"):
            try:
                self._update_project_status(
                    project_name=classification["project"],
                    update=_pu,
                    trigger_doc_id=result.doc_id,
                    trigger_message=classification.get("summary", "") or content[:200],
                )
            except Exception:
                pass

        self._auto_tier_transition()
        return result.doc_id

    def insert_classified(self, content: str, classification: dict, source: str = "sdk") -> int:
        """上层已分类，纯写入 → doc_id（公开接口，委托给 _insert_classified）"""
        return self._insert_classified(content, classification, source)

    def ingest_pure(self, content: str, classification: dict, source: str = "sdk:pure") -> int:
        """纯插入 — 无判断、无合并、无分类，Agent 直接调用

        Args:
            content: 要写入的内容
            classification: 分类结果（Agent 已完成分类，必须包含 label/importance/category）
            source: 来源标记

        Returns:
            新插入的 doc_id

        与 insert_classified 的区别：ingest_pure 是面向 Agent 的公开接口，语义更清晰。
        """
        return self._insert_classified(content, classification, source)

    def update_memory(self, doc_id: int, summary: str, importance: str = "",
                      weight: int = 0, scope: str = "", category: str = "",
                      scene: str = "", emotion: str = "") -> bool:
        """融合时更新已有记忆

        Args:
            doc_id: 要更新的文档 ID
            summary: 新摘要（融合后的内容）
            importance: 新重要性，空字符串表示不更新
            weight: 新权重，0 表示不更新
            scope: 新 scope，空字符串表示不更新
            category: 新 category，空字符串表示不更新
            scene: 新 scene，空字符串表示不更新
            emotion: 新 emotion，空字符串表示不更新

        Returns:
            是否更新成功（doc_id不存在返回False）

        ⚠️ 安全：自动检测并脱敏 API 密钥
        ⚠️ 审计：所有更新操作记录到 audit_*.log
        """
        validate_utf8(summary)

        secrets = detect_secrets(summary)
        if secrets:
            summary = redact_secrets(summary)

        # C++ 引擎（注意：C++ update_memory 会覆盖 memory_fts.compact_content，需保护原始全文）
        orig_fts_compact = self._conn.execute(
            "SELECT compact_content FROM memory_fts WHERE doc_id = ?", (doc_id,)
        ).fetchone()
        ok = self._cpp_storage.update_memory(doc_id, summary, importance, weight)
        if ok:
            # 恢复 FTS5 compact_content（保护原始全文索引不被 summary 覆盖）
            if orig_fts_compact and orig_fts_compact[0]:
                self._conn.execute(
                    "UPDATE memory_fts SET compact_content = ? WHERE doc_id = ?",
                    (orig_fts_compact[0], doc_id)
                )
                self._conn.commit()
            # 验证实际更新了记录（C++ 不检查 affected rows）
            existing = self._cpp_storage.get_memory(doc_id)
            if not existing:
                return False
            # 更新 scope, category, scene, emotion（Python 层补充，C++ 不支持）
            if scope or category or scene or emotion:
                updates = []
                params = []
                if scope:
                    updates.append("scope = ?")
                    params.append(scope)
                if category:
                    updates.append("content_category = ?")
                    params.append(category)
                if scene:
                    updates.append("scene = ?")
                    params.append(scene)
                if emotion:
                    updates.append("emotion = ?")
                    params.append(emotion)
                if updates:
                    params.append(doc_id)
                    self._conn.execute(
                        f"UPDATE memory_classify SET {', '.join(updates)} WHERE doc_id = ?",
                        params
                    )
                    self._conn.commit()
            self._audit.log("update", doc_id, {
                "importance": importance, "weight": weight,
                "scope": scope, "category": category,
                "secrets_found": len(secrets) if secrets else 0,
            })

            # 自动同步到对应 MD 文件
            try:
                from .sync import MemorySync
                export_dir = Path(self._db_path).parent / "memory_export_all"
                sync = MemorySync(self._db_path, str(export_dir), conn=self._conn)
                sync.sync_one_to_md(doc_id)
            except Exception:
                pass  # MD 同步失败不应阻塞主流程
        return ok

    def append_to_memory(self, doc_id: int, content: str, source: str = "sdk") -> bool:
        """追加内容到已有记忆

        Args:
            doc_id: 要追加的文档 ID
            content: 要追加的内容
            source: 来源标记

        Returns:
            是否追加成功（doc_id不存在返回False）

        ⚠️ 安全：自动检测并脱敏 API 密钥
        ⚠️ 审计：所有追加操作记录到 audit_*.log
        """
        validate_utf8(content)

        secrets = detect_secrets(content)
        if secrets:
            content = redact_secrets(content)

        # 直接从数据库读取 compact_content
        row = self._conn.execute(
            "SELECT compact_content FROM memory_classify WHERE doc_id = ?",
            (doc_id,)
        ).fetchone()
        if not row or not row[0]:
            return False

        old_content = row[0]

        # 追加内容（带时间戳）
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        merged = f"{old_content}\n\n---\n\n## {timestamp}\n{content}"

        # 更新记忆
        ok = self.update_memory(doc_id, merged)
        if ok:
            self._audit.log("append", doc_id, {
                "source": source,
                "content_length": len(content),
                "secrets_found": len(secrets) if secrets else 0,
            })
        return ok

    def insert_cross_refs(self, doc_id: int, refs: list[CrossRefRefDict]) -> int:
        """批量写入交叉引用

        Args:
            doc_id: 源文档 ID
            refs: 关联列表，格式：
                [
                    {"related_doc_id": 12, "relation_type": "extend", "note": "都是打包前置步骤"},
                    {"related_doc_id": 3, "relation_type": "premise", "note": "语法验证是代码规范的应用"},
                ]
                relation_type 可选值：supplement | refute | extend | premise | example | related

        Returns:
            成功写入的条数
        """
        if not refs:
            return 0

        # 转换 related_doc_id 为 string（C++ map<string,string> 不接受 int）
        cpp_refs = [{k: str(v) for k, v in ref.items()} for ref in refs]
        return self._cpp_storage.insert_cross_refs(doc_id, cpp_refs)

    def scan_mentions(self, doc_id: int, min_name_len: int = 2,
                      top_entities: int = 100) -> list[dict[str, Any]]:
        """扫描 doc 的 compact_content，发现其中提到的其他记忆的 entity

        "未链接提及"检测——即使两条记忆没有显式共享 entity，
        如果一篇的正文中出现了另一篇的 entity_name，也算关联。

        Args:
            doc_id: 要扫描的源文档 ID
            min_name_len: 忽略长度小于此值的 entity（避免常见字误匹配，默认 2）
            top_entities: 最多取多少个候选 entity（按 weight 降序）

        Returns:
            命中列表，每条含：
            {"related_doc_id": int, "entity_name": str, "mention_count": int}
            按 mention_count 降序排列。无命中返回 []。
        """
        row = self._conn.execute(
            "SELECT compact_content FROM memory_classify WHERE doc_id=?",
            (doc_id,)
        ).fetchone()
        if not row or not row["compact_content"]:
            return []
        content = row["compact_content"]

        entities = self._conn.execute(
            """SELECT e.doc_id, e.entity_name
               FROM memory_entity e
               WHERE e.doc_id != ?
               ORDER BY e.weight DESC
               LIMIT ?""",
            (doc_id, top_entities),
        ).fetchall()
        if not entities:
            return []

        hits = []
        seen_pairs = set()
        for ent in entities:
            name = ent["entity_name"]
            if len(name) < min_name_len:
                continue
            key = (ent["doc_id"], name)
            if key in seen_pairs:
                continue
            seen_pairs.add(key)

            count = content.count(name)
            if count > 0:
                hits.append({
                    "related_doc_id": ent["doc_id"],
                    "entity_name": name,
                    "mention_count": count,
                })

        hits.sort(key=lambda h: h["mention_count"], reverse=True)
        return hits

    def auto_cross_ref(self, doc_id: int, candidates: list[CrossRefCandidateDict] | None = None,
                       relation_type: str = "related", top_k: int = 3,
                       scan_mentions: bool = True) -> int:
        """基于候选列表批量建双向交叉引用（Obsidian 风格）

        支持两路数据源（按优先级）：
        1. candidates 参数：上层 Agent 已搜好的结果（推荐）
        2. candidates 为 None：自动用 entity 共享 + 同 category 查找

        自动跳过自己关联自己。幂等（OR IGNORE）。

        Args:
            doc_id: 要关联的源文档 ID
            candidates: 候选结果列表，每项含 doc_id / summary（可选）。None=自动查找
            relation_type: 关联类型，默认 related
            top_k: 关联前几条，默认 3

        Returns:
            成功写入的双向边数
        """
        if candidates is None:
            candidates = self._find_cross_ref_candidates(doc_id, top_k)

        if not candidates:
            return 0

        # 排除自己
        targets = [c for c in candidates if c.get("doc_id") != doc_id][:top_k]
        if not targets:
            return 0

        # 收集单向 refs（get_linked 用 UNION 双向读取）
        all_refs = []
        for cand in targets:
            other_id = cand["doc_id"]
            note = (cand.get("summary", "") or "")[:100]
            all_refs.append({
                "related_doc_id": str(other_id),
                "relation_type": relation_type,
                "note": note,
            })

        # ── mention 扫描（不受 top_k 限制） ──
        if scan_mentions:
            mentions = self.scan_mentions(doc_id)
            for ment in mentions:
                other_id = ment["related_doc_id"]
                note = f"正文中提到了「{ment['entity_name']}」({ment['mention_count']}处)"
                all_refs.append({
                    "related_doc_id": str(other_id),
                    "relation_type": "mention",
                    "note": note,
                })

        # 批量写入（C++ 或 Python）
        if all_refs:
            return self.insert_cross_refs(doc_id, all_refs)
        return 0

    def crawl_cross_ref(self, top_k: int = 3, max_docs: int = 0,
                        incremental: bool = True,
                        scan_mentions: bool = True,
                        force: bool = False) -> CrawlStatsDict:
        """批量全量/增量扫描所有记忆，建 cross_ref

        与 rebuild_links 的区别：
        - crawl_cross_ref: 支持增量模式和 mention 扫描，适合定期维护
        - rebuild_links: 专注于孤立记忆，适合修复断链

        对每条有 compact_content 的记忆调用 auto_cross_ref()。
        支持增量模式：只处理上次 crawl 之后新增的记忆。

        Args:
            top_k: 每条记忆关联前几条候选（默认 3）
            max_docs: 处理上限，0=全部（默认 0）
            incremental: True=增量模式（只扫新增），False=全量（默认 True）
            scan_mentions: 是否启用 mention 扫描（默认 True）
            force: True=强制全量重建（忽略增量检查，默认 False）

        Returns:
            统计字典：{processed, new_edges, skipped, total_edges}
        """
        ids = self._conn.execute(
            "SELECT doc_id FROM memory_classify WHERE compact_content != '' ORDER BY doc_id"
        ).fetchall()
        all_ids = [r["doc_id"] for r in ids]

        if incremental:
            row = self._conn.execute(
                "SELECT value FROM system_meta WHERE key='last_crawl_max_doc_id'"
            ).fetchone()
            if row:
                last_max_id = int(row["value"])
                known_ids = self._conn.execute(
                    "SELECT doc_id FROM memory_classify WHERE compact_content != '' "
                    "AND doc_id > ?",
                    (last_max_id,),
                ).fetchall()
                if known_ids:
                    all_ids = [r["doc_id"] for r in known_ids]
                else:
                    return {"processed": 0, "new_edges": 0, "skipped": 0, "total_edges": 0}

        if max_docs > 0:
            all_ids = all_ids[:max_docs]

        if not all_ids:
            return {"processed": 0, "new_edges": 0, "skipped": 0, "total_edges": 0}

        existing = self._conn.execute(
            "SELECT DISTINCT doc_id FROM memory_cross_ref"
        ).fetchall()
        existing_ids = {r["doc_id"] for r in existing}

        processed = 0
        skipped = 0
        new_edges = 0

        for did in all_ids:
            if did in existing_ids and incremental and not force:
                skipped += 1
                continue

            candidates = self._find_cross_ref_candidates(did, top_k)
            n = self.auto_cross_ref(did, candidates=candidates,
                                    top_k=top_k, scan_mentions=scan_mentions)
            if n > 0:
                new_edges += n
                processed += 1
            else:
                skipped += 1

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        max_id = all_ids[-1] if all_ids else 0
        self._conn.execute(
            "INSERT OR REPLACE INTO system_meta (key, value, updated_at) VALUES (?, ?, ?)",
            ("last_crawl_time", now, now),
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO system_meta (key, value, updated_at) VALUES (?, ?, ?)",
            ("last_crawl_max_doc_id", str(max_id), now),
        )
        self._conn.execute(
            """INSERT INTO system_meta (key, value, updated_at)
               VALUES ('total_crawl_count', '1', ?)
               ON CONFLICT(key) DO UPDATE
               SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT),
               updated_at = ?""",
            (now, now),
        )
        self._conn.commit()

        total_edges = self._conn.execute(
            "SELECT COUNT(*) FROM memory_cross_ref"
        ).fetchone()[0]

        return {
            "processed": processed,
            "new_edges": new_edges,
            "skipped": skipped,
            "total_edges": total_edges,
        }

    def _find_cross_ref_candidates(self, doc_id: int, top_k: int) -> list[CrossRefCandidateDict]:
        """自动查找关联候选：同 entity + 同 category"""
        cpp_results = self._cpp_storage.find_cross_ref_candidates(doc_id, top_k)
        return [{"doc_id": int(r["doc_id"]), "summary": r["summary"],
                 "score": float(r["score"])} for r in cpp_results]

    # ═══════════════════════════════════════════════════════════
    # 知识库整理方法
    # ═══════════════════════════════════════════════════════════

    def rebuild_links(self, full: bool = False, dry_run: bool = False) -> RebuildLinksDict:
        """重新建立知识图谱关联

        与 crawl_cross_ref 的区别：
        - rebuild_links: 处理孤立记忆（无 cross_ref 的）或全量重建
        - crawl_cross_ref: 全量/增量扫描所有记忆，支持 mention 扫描

        Args:
            full: True=全量重新扫描, False=只处理没有 cross_ref 的孤立记忆
            dry_run: True=只预览不写入

        Returns:
            统计字典: {processed, new_edges, skipped, total}
        """
        if full:
            rows = self._conn.execute(
                "SELECT doc_id FROM memory_classify WHERE compact_content != '' ORDER BY doc_id"
            ).fetchall()
            all_ids = [r["doc_id"] for r in rows]
        else:
            rows = self._conn.execute(
                """SELECT mc.doc_id FROM memory_classify mc
                   LEFT JOIN memory_cross_ref mcr ON mc.doc_id = mcr.doc_id
                   WHERE mc.compact_content != '' AND mcr.doc_id IS NULL
                   ORDER BY mc.doc_id"""
            ).fetchall()
            all_ids = [r["doc_id"] for r in rows]

        total = self._conn.execute(
            "SELECT COUNT(*) FROM memory_classify WHERE compact_content != ''"
        ).fetchone()[0]

        processed = 0
        new_edges = 0
        skipped = 0

        for doc_id in all_ids:
            candidates = self._find_cross_ref_candidates(doc_id, top_k=3)
            if not candidates:
                skipped += 1
                continue

            if not dry_run:
                n = self.auto_cross_ref(doc_id, candidates=candidates, top_k=3)
                new_edges += n
            processed += 1

        return {
            "processed": processed,
            "new_edges": new_edges,
            "skipped": skipped,
            "total": total,
        }

    def auto_cleanup(self, min_weight: int = 10, max_age_days: int = 90,
                     dry_run: bool = False) -> dict[str, Any]:
        """自动清理低权重、长期未访问的记忆

        Args:
            min_weight: 权重低于此值的记忆被清理
            max_age_days: 超过此天数未访问的记忆被清理
            dry_run: 仅预览，不实际删除

        Returns:
            清理统计: {eligible, deleted, dry_run}
        """
        cutoff_date = (datetime.now(timezone.utc) - __import__('datetime').timedelta(days=max_age_days)).strftime("%Y-%m-%d")

        rows = self._conn.execute("""
            SELECT c.doc_id, c.weight, d.create_time
            FROM memory_classify c
            JOIN document_files d ON c.doc_id = d.id
            WHERE d.is_deleted = 0
              AND c.weight < ?
              AND (d.create_time < ? OR d.create_time IS NULL)
        """, (min_weight, cutoff_date)).fetchall()

        eligible = len(rows)
        deleted = 0

        if not dry_run and eligible > 0:
            for row in rows:
                self._conn.execute(
                    "UPDATE document_files SET is_deleted = 1 WHERE id = ?",
                    (row["doc_id"],)
                )
                deleted += 1
            self._conn.commit()

        return {"eligible": eligible, "deleted": deleted, "dry_run": dry_run}

    def cleanup_memories(self, mode: str = "test", hard: bool = False,
                         dry_run: bool = False) -> CleanupStatsDict:
        """清理测试数据和无效记忆"""
        result = self._cpp_storage.cleanup_memories(mode, hard, dry_run)
        result["mode"] = mode
        return result


    # ═══════════════════════════════════════════════════════════
    # 读取方法
    # ═══════════════════════════════════════════════════════════

    def get_memory(self, doc_id: int) -> MemoryDetailDict | None:
        """读单条完整内容

        Args:
            doc_id: 文档 ID

        Returns:
            完整记忆字典，包含 file_path, summary, category, importance, weight, entities 等
        """
        rec = self._cpp_storage.get_memory(doc_id)
        if not rec:
            return None
        # 读取实体（C++ 暂无 get_entities 接口，走 Python）
        entities = self._conn.execute(
            "SELECT entity_name, entity_type FROM memory_entity WHERE doc_id=?",
            (doc_id,),
        ).fetchall() if self._conn else []
        return {
            "doc_id": rec.doc_id,
            "file_path": "",
            "summary": rec.summary or "",
            "label": rec.label,
            "importance": rec.importance,
            "weight": rec.weight,
            "category": rec.category or "",
            "sub_category": rec.sub_category or "",
            "depth": "",
            "entities": [{"name": e[0], "type": e[1]} for e in entities],
        }

    def get_linked(self, doc_id: int, relation_type: Optional[str] = None) -> list[LinkedDict]:
        """读交叉引用 + 顺藤摸瓜"""
        # C++ 引擎路径：读写同路
        if self._use_cpp and self._cpp_storage:
            linked_results = self._cpp_storage.get_linked(doc_id)
            results = []
            for linked in linked_results:
                # 按 relation_type 过滤（如果指定）
                if relation_type and linked.relation_type != relation_type:
                    continue
                mem = self._cpp_storage.get_memory(linked.doc_id)
                # note 可能包含损坏的非 UTF-8 数据
                try:
                    raw = linked.note
                except UnicodeDecodeError:
                    raw = None
                if raw:
                    if isinstance(raw, bytes):
                        try:
                            note = raw.decode('utf-8', 'replace')
                        except Exception:
                            note = ""
                    else:
                        note = str(raw)
                    # 检查 replacement char (U+FFFD) 和 lone surrogates (U+D800–U+DFFF)
                    if '\ufffd' in note or any('\ud800' <= c <= '\udfff' for c in note):
                        note = ""
                else:
                    note = ""
                results.append({
                    "doc_id": linked.doc_id,
                    "relation_type": linked.relation_type,
                    "note": note,
                    "weight": getattr(linked, "weight", 1.0),
                    "summary": mem.summary if mem else "",
                    "category": mem.category if mem else "",
                    "importance": mem.importance if mem else "P2",
                })
            return results

        return []

    def vector_search(self, query: str, top_k: int = 10) -> list[SearchResultDict]:
        """向量搜索 — 通过 C++ HNSW 索引"""
        if not self._cpp_search.has_vector_index():
            return []
        query_vec = _cpp_core.storage_embed_text(self._cpp_storage, query)
        if not query_vec:
            return []
        vec_results = _cpp_core.search_engine_vector_search(
            self._cpp_search, query_vec, top_k)
        results = []
        for doc_id, score in vec_results:
            mem = self.get_memory(doc_id)
            if mem:
                results.append({
                    "doc_id": doc_id,
                    "summary": mem.get("summary", ""),
                    "category": mem.get("category", ""),
                    "importance": mem.get("importance", "P2"),
                    "weight": mem.get("weight", 50),
                    "score": score,
                    "signals": {"vector": score},
                    "explain": None,
                })
        if results:
            self._record_access_batch([r["doc_id"] for r in results])
        return results

    def _auto_tier_transition(self) -> int:
        """自动 tier 过渡：在每次访问/写入/衰减后检查 weight 阈值

        Rules:
            weight >= 80 → hot（frozen 跳过）
            weight <= 20 + 90d 无访问 → cold（frozen 跳过）
        Returns:
            发生过渡的条数
        """
        if not self._cpp_storage:
            return 0
        count = 0
        try:
            # Promote to hot
            rows = self._conn.execute(
                "SELECT doc_id FROM memory_classify "
                "WHERE weight >= 80 AND tier != 'hot' AND tier != 'frozen' AND compact_content != ''"
            ).fetchall()
            for (doc_id,) in rows:
                self._cpp_storage.set_tier(doc_id, "hot", "自动升级: weight >= 80")
                count += 1

            # Demote to cold
            rows = self._conn.execute(
                "SELECT c.doc_id FROM memory_classify c "
                "WHERE c.weight <= 20 AND c.tier != 'cold' AND c.tier != 'frozen' AND c.compact_content != '' "
                "AND c.doc_id NOT IN ("
                "  SELECT doc_id FROM memory_access_record "
                "  WHERE access_time > datetime('now', '-90 days')"
                ")"
            ).fetchall()
            for (doc_id,) in rows:
                self._cpp_storage.set_tier(doc_id, "cold", "自动降级: 低 weight + 90d 无访问")
                count += 1
        except Exception:
            pass
        return count

    # ═══════════════════════════════════════════════════
    # v0.21.0: 项目连续上下文
    # ═══════════════════════════════════════════════════

    def _project_status_path(self, project_name: str) -> str:
        return f"__project_status__/{project_name}"

    def _update_project_status(self, project_name: str, update: dict,
                                trigger_doc_id: int, trigger_message: str) -> None:
        """写入/更新项目状况快照（在每次 scope=project 写入后自动调用）

        数据存储在 document_files 表中（避免 memory_classify schema 跨版本不一致）。

        Args:
            project_name: 项目名称
            update: Agent 提供的 project_update 字典（phase/completed/blocker/current_goal）
            trigger_doc_id: 触发这次更新的记忆 doc_id
            trigger_message: 触发消息摘要
        """
        file_path = self._project_status_path(project_name)

        # INSERT OR IGNORE + UPDATE 二步：先确保行存在，再更新内容
        self._conn.execute(
            "INSERT OR IGNORE INTO document_files "
            "(file_path, file_hash, file_size, create_time, modify_time, origin_source) "
            "VALUES (?, 'project_status', 0, datetime('now'), datetime('now'), 'sdk')",
            (file_path,)
        )

        row = self._conn.execute(
            "SELECT raw_text_snippet, modify_time FROM document_files WHERE file_path=?",
            (file_path,)
        ).fetchone()
        if not row:
            return

        existing = json.loads(row["raw_text_snippet"] or "{}")

        # 合并 Agent 提供的字段
        if update.get("phase"):
            existing["phase"] = update["phase"]
        if update.get("current_goal"):
            existing["current_goal"] = update["current_goal"]
        if update.get("completed"):
            stages = existing.get("completed_stages", [])
            stage_names = {s["name"] for s in stages}
            if update["completed"] not in stage_names:
                from datetime import datetime as _dt
                stages.insert(0, {
                    "name": update["completed"],
                    "time": _dt.now().strftime("%Y-%m-%d %H:%M"),
                    "done": True,
                })
            existing["completed_stages"] = stages
        if "blocker" in update:
            existing["blockers"] = [update["blocker"]] if update["blocker"] else []

        # 元信息
        existing["last_trigger_doc_id"] = trigger_doc_id
        existing["last_trigger_message"] = trigger_message
        from datetime import datetime as _dt
        existing["last_updated_at"] = _dt.now().strftime("%Y-%m-%d %H:%M")
        existing["project"] = project_name

        # 查询项目下近期 P0/P1 决策（排除触发记忆本身）
        decisions = self._conn.execute(
            "SELECT doc_id, summary, importance, create_time FROM memory_classify "
            "WHERE project=? AND scope='project' AND doc_id!=? "
            "AND importance IN ('P0','P1') AND summary!='' "
            "ORDER BY create_time DESC LIMIT 10",
            (project_name, trigger_doc_id)
        ).fetchall()
        existing["active_decisions"] = [
            {"summary": r["summary"], "doc_id": r["doc_id"],
             "importance": r["importance"], "time": r["create_time"] or ""}
            for r in decisions
        ]

        # 近期踩坑（label=caveat）
        pitfalls = self._conn.execute(
            "SELECT doc_id, summary, importance, create_time FROM memory_classify "
            "WHERE project=? AND scope='project' AND doc_id!=? "
            "AND label='caveat' ORDER BY create_time DESC LIMIT 10",
            (project_name, trigger_doc_id)
        ).fetchall()
        existing["key_pitfalls"] = [
            {"summary": r["summary"], "doc_id": r["doc_id"],
             "importance": r["importance"], "time": r["create_time"] or ""}
            for r in pitfalls
        ]

        # 近期 doc_ids（排除项目状况自己）
        recent = self._conn.execute(
            "SELECT doc_id FROM memory_classify "
            "WHERE project=? AND scope='project' AND doc_id!=? "
            "ORDER BY create_time DESC LIMIT 20",
            (project_name, trigger_doc_id)
        ).fetchall()
        existing["recent_doc_ids"] = [r[0] for r in recent]

        # 活跃实体
        entities = self._conn.execute(
            "SELECT DISTINCT e.entity_name FROM memory_entity e "
            "JOIN memory_classify c ON e.doc_id=c.doc_id "
            "WHERE c.project=? AND c.scope='project' AND c.doc_id!=? "
            "ORDER BY e.entity_name LIMIT 50",
            (project_name, trigger_doc_id)
        ).fetchall()
        existing["active_entities"] = [r[0] for r in entities]

        # 记忆总量
        existing["memory_count"] = self._conn.execute(
            "SELECT COUNT(*) FROM memory_classify "
            "WHERE project=? AND scope='project'",
            (project_name,)
        ).fetchone()[0]

        # 写回 document_files
        compact_json = json.dumps(existing, ensure_ascii=False)
        self._conn.execute(
            "UPDATE document_files SET raw_text_snippet=?, modify_time=datetime('now') "
            "WHERE file_path=?",
            (compact_json, file_path)
        )
        self._conn.commit()

    def get_project_status(self, project_name: str) -> ProjectStatusDict | None:
        """获取指定项目的连续上下文状况快照

        返回项目当前阶段、已完成阶段、活跃决策、踩坑记录等，
        并标注 staleness（项目概况是否落后于最新记忆）。

        Args:
            project_name: 项目名称

        Returns:
            项目状况快照字典，项目不存在返回 None
        """
        file_path = self._project_status_path(project_name)
        row = self._conn.execute(
            "SELECT raw_text_snippet, modify_time FROM document_files WHERE file_path=?",
            (file_path,)
        ).fetchone()
        if not row:
            return None

        status = json.loads(row["raw_text_snippet"] or "{}")
        last_updated = row["modify_time"] or ""

        # stale 检测：是否有 doc_id 比上次触发更新的记忆更新
        # 使用 doc_id（AUTOINCREMENT 单调递增）而非时间戳，避免同秒精度问题
        last_trigger = status.get("last_trigger_doc_id", 0)
        newer = self._conn.execute(
            "SELECT COUNT(*) FROM memory_classify "
            "WHERE project=? AND scope='project' AND doc_id > ?",
            (project_name, last_trigger)
        ).fetchone()[0]
        status["stale"] = newer > 0

        status["last_updated_at"] = last_updated
        return status

    def embed_text(self, text: str) -> Optional[list[float]]:
        """文本向量化 — 通过 C++ ONNX 引擎"""
        if not self._cpp_storage:
            return None
        from ._core import mw_core
        return mw_core.storage_embed_text(self._cpp_storage, text)

    def _save_vector_index(self) -> None:
        """将当前 HNSW 索引保存到磁盘（增量更新后调用）"""
        if not self._cpp_search or not self._cpp_search.has_vector_index():
            return
        from ._core import mw_core
        data = mw_core.search_engine_save_vector_index(self._cpp_search)
        index_path = Path(self._db_path).parent / "vector_index.hnsw"
        with open(index_path, "wb") as f:
            if isinstance(data, list):
                f.write("".join(data).encode("latin-1"))
            elif isinstance(data, str):
                f.write(data.encode("latin-1"))
            elif isinstance(data, (bytes, bytearray)):
                f.write(bytes(data))
            else:
                f.write(bytes(data))

    @property
    def vector_available(self) -> bool:
        """向量搜索是否可用（C++ HNSW 索引已构建）"""
        return self._cpp_search.has_vector_index()

    def preload_vector_model(self, callback=None) -> bool:
        """加载向量模型 — 已改为通过 embed.py 在 Python 侧初始化"""
        return True

    def build_vector_index(self, callback=None) -> VectorBuildDict:
        """从 memory_vector 表读取已有 embedding 构建 HNSW 索引

        Args:
            callback: 进度回调函数，接收 (current, total, message)

        Returns:
            构建统计信息
        """
        from ._core import mw_core

        all_embeddings = mw_core.storage_get_all_embeddings(self._cpp_storage)
        if not all_embeddings:
            return {"built": 0, "skipped": 0, "errors": 0, "note": "memory_vector 表无数据"}

        dims = {}
        for _doc_id, vec in all_embeddings:
            d = len(vec)
            dims[d] = dims.get(d, 0) + 1
        target_dim = max(dims, key=dims.get)

        mw_core.search_engine_build_vector_index(self._cpp_search, target_dim)
        errors = 0
        for i, (doc_id, vec) in enumerate(all_embeddings):
            if len(vec) == target_dim:
                try:
                    mw_core.search_engine_add_vector(self._cpp_search, doc_id, vec)
                except Exception:
                    errors += 1
            if callback and (i + 1) % 100 == 0:
                callback(i + 1, len(all_embeddings), f"已添加 {i+1}/{len(all_embeddings)}")

        self._save_vector_index()

        return {"built": len(all_embeddings) - errors, "skipped": 0, "errors": errors, "note": "done"}

    def get_vector_stats(self) -> VectorStatsDict:
        """获取向量索引统计信息"""
        from ._core import mw_core
        has_index = self._cpp_search.has_vector_index() if self._cpp_search else False
        all_emb = mw_core.storage_get_all_embeddings(self._cpp_storage) if self._cpp_storage else []
        return {"indexed": len(all_emb), "available": has_index}

    # ═══════════════════════════════════════════════════════════
    # 导出方法（V3 重构：废弃 SQL INSERT，改为 Obsidian MD + JSONL）
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def _format_body_by_type(content: str, label: str, category: str) -> list[str]:
        """按记忆类型统一格式化导出内容

        规则：
        - 标题统一用 ## （H2），不混用 # 和 ##
        - 清理重复：如果第一行是 ## 标题，第二行是相同内容，去掉标题行
        - 清理「待补充」占位符
        """
        if not content:
            return ["（无内容）", ""]

        lines = content.strip().split("\n")
        body = []

        # 统一：所有行的 # 标题降级为 ##
        for line in lines:
            if line.startswith("# ") and not line.startswith("## "):
                body.append("#" + line)  # # → ##
            else:
                body.append(line)

        # 清理重复：## 标题行 + 下面有相同内容 → 去掉标题行
        if len(body) >= 2 and body[0].strip().startswith("## "):
            heading_text = body[0].strip()[3:].strip()
            h_clean = re.sub(r'^(规则|决策|配置|踩坑|经验|bug-fix)[：:]\s*', '', heading_text)
            h_clean = re.sub(r'[：:|｜\s/\\]', '', h_clean)
            # 跳过空行找第一个非空行
            for idx in range(1, len(body)):
                if body[idx].strip():
                    second = body[idx].strip()
                    s_clean = re.sub(r'[：:|｜\s/\\]', '', second)
                    if h_clean and s_clean and len(s_clean) > 10:
                        if h_clean in s_clean or s_clean in h_clean:
                            body = body[idx:]  # 去掉标题行，保留内容
                    break

        # 清理「待补充」占位符
        cleaned = []
        for line in body:
            if "（待补充）" in line or "(待补充)" in line:
                # 跳过整行是「待补充」的，保留有实际内容的
                stripped = line.replace("（待补充）", "").replace("(待补充)", "").strip()
                if stripped and stripped not in ("-", "**原因**：", "**例外**："):
                    cleaned.append(line.replace("（待补充）", "（暂无）").replace("(待补充)", "(暂无)"))
            else:
                cleaned.append(line)
        body = cleaned

        # 如果内容只有一行纯文本，不加额外格式
        if len(body) <= 1 and not body[0].startswith("#"):
            return [body[0], ""]

        return body + [""]

    def export_md(self, output_dir: str) -> int:
        """导出标准 Obsidian Vault（含 frontmatter + [[文件名]] 双链 + MOC 导航）

        输出结构：
        memory_export_all/
        ├── .obsidian/                        ← Obsidian 配置（图谱+外观）
        ├── INDEX.md                          ← 总路由表（Map of Content）
        ├── 行为规则/
        │   ├── _moc.md                       ← 分类导航
        │   ├── 打包部署规则.md               ← 分类文件（含 frontmatter + 双链）
        │   └── ...
        └── 项目上下文/
            ├── _moc.md
            └── ...

        Args:
            output_dir: 输出目录路径

        Returns:
            导出的文件数

        ⚠️ 排查：
        - 三个Agent共用 meta_agents.sqlite，导出目录统一为 memory_export_all/
        - 不要混入 exe 的 memory_export/ 目录，那是桌面软件专用的
        - _sanitize_filename() 如果炸 OSError，一般是内容含 \\n 等非法文件名字符
        - 已存在同名文件跳过（不覆盖），通过计数后缀 _2 _3 避免重复
        - 2026-06-27 Bug：漏过滤 \\n 导致 claude #143 OSError，已修复
        """
        from datetime import datetime

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        # ── Pass 1: 构建 doc_id → 文件名的映射（双链接需要文件名而非数字） ──
        # 容错：stability/confidence 列可能在旧数据库中不存在
        try:
            rows = self._conn.execute(
                """SELECT c.doc_id, c.content_category, c.sub_category, c.label,
                          c.importance, c.weight, c.compact_content, c.title,
                          c.depth, c.tags, c.scope, c.keywords, c.summary,
                          c.memory_tier, c.stability, c.confidence, c.memory_type,
                          c.project,
                          c.scene, c.emotion, c.tier, c.valid_from, c.valid_until,
                          d.file_path, d.create_time
                   FROM memory_classify c
                   JOIN document_files d ON c.doc_id = d.id
                   WHERE d.is_deleted = 0 AND c.compact_content != ''
                   ORDER BY c.content_category, c.sub_category, c.weight DESC"""
            ).fetchall()
        except sqlite3.OperationalError:
            rows = self._conn.execute(
                """SELECT c.doc_id, c.content_category, c.sub_category, c.label,
                          c.importance, c.weight, c.compact_content, c.title,
                          c.depth, c.tags, c.scope, c.keywords, c.summary,
                          c.memory_tier, '' as stability, '' as confidence, c.memory_type,
                          c.project,
                          c.scene, c.emotion, c.tier, c.valid_from, c.valid_until,
                          d.file_path, d.create_time
                   FROM memory_classify c
                   JOIN document_files d ON c.doc_id = d.id
                   WHERE d.is_deleted = 0 AND c.compact_content != ''
                   ORDER BY c.content_category, c.sub_category, c.weight DESC"""
            ).fetchall()

        def _sanitize_filename(text: str) -> str:
            """生成安全的 Obsidian 文件名

            ⚠️ 排查：
            - 2026-06-27 Bug：漏了过滤 \\n，导致 record #143 因内容含 \\n\\n 触发 OSError
            - 已修复：在 re.sub 中加了 \\n\\r
            - 入参是 compact_content 的第一句（split("。")[0]），最长 50 字
            - 重复文件名自动加 _2, _3 后缀，不会覆盖
            """
            if not text:
                return "未命名"
            # 取第一句（最长 50 字）
            first = safe_truncate(text.split("。")[0].split(".")[0], 50)
            first = re.sub(r'[\n\r\\/:*?"<>|#^\[\]{}]', '', first).strip()
            return first or "未命名"

        # doc_id → filename（不含扩展名，用于 [[wikilink]]）
        doc_to_name: dict[int, str] = {}
        name_counts: dict[str, int] = {}
        for r in rows:
            raw = _sanitize_filename(r["title"] or r["compact_content"] or r["label"] or "记忆")
            # 确保文件名唯一
            if raw in name_counts:
                name_counts[raw] += 1
                raw = f"{raw}_{name_counts[raw]}"
            else:
                name_counts[raw] = 1
            doc_to_name[r["doc_id"]] = raw

        # ── 查询交叉引用 ──
        cross_refs = self._conn.execute(
            "SELECT doc_id, related_doc_id, relation_type, note FROM memory_cross_ref"
        ).fetchall()
        ref_map: dict[int, list[dict]] = {}
        for cr in cross_refs:
            ref_map.setdefault(cr["doc_id"], []).append({
                "related_doc_id": cr["related_doc_id"],
                "relation_type": cr["relation_type"],
                "note": cr["note"] or "",
                "related_name": doc_to_name.get(cr["related_doc_id"], f"doc_{cr['related_doc_id']}"),
            })

        # ── 按 content_category 分组（双轴分类体系的轴1）──
        groups: dict[str, list] = {}
        for r in rows:
            category = r["content_category"] or "未分类"
            groups.setdefault(category, []).append(r)

        # ── 写 .obsidian 配置 ──
        obsidian_dir = output_path / ".obsidian"
        obsidian_dir.mkdir(exist_ok=True)
        (obsidian_dir / "app.json").write_text(
            '{"showLineNumber":true,"alwaysUpdateLinks":true,"newLinkFormat":"shortest"}',
            encoding="utf-8",
        )
        (obsidian_dir / "appearance.json").write_text(
            '{"baseTheme":"moonstone","accentColor":"#e74c3c","showViewHeader":true}',
            encoding="utf-8",
        )
        (obsidian_dir / "core-plugins.json").write_text(
            '{"file-explorer":true,"graph":true,"backlink":true,"tag-pane":true,"page-preview":true,"templates":true,"search":true}',
            encoding="utf-8",
        )

        # ── 计算 centrality（度中心性），带 5s 超时 ──
        centrality = {}
        try:
            import networkx as nx
            from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FuturesTimeout

            def _compute_centrality(rows, cross_refs):
                G = nx.DiGraph()
                ids = {r["doc_id"] for r in rows}
                for r in rows:
                    G.add_node(r["doc_id"])
                for cr in cross_refs:
                    if cr["doc_id"] in ids and cr["related_doc_id"] in ids:
                        G.add_edge(cr["doc_id"], cr["related_doc_id"])
                if G.number_of_nodes() > 0:
                    return nx.degree_centrality(G)
                return {}

            with ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(_compute_centrality, rows, cross_refs)
                centrality = fut.result(timeout=5)
        except _FuturesTimeout:
            pass
        except Exception:
            pass

        # ── Pass 2: 写文件 ──
        count = 0
        for cat, items in groups.items():
            cat_dir = output_path / cat
            cat_dir.mkdir(exist_ok=True)

            for item in items:
                doc_id = item["doc_id"]
                summary = item["compact_content"] or ""
                name = doc_to_name.get(doc_id, f"doc_{doc_id}")
                md_path = cat_dir / f"{name}.md"

                # Frontmatter
                try:
                    tags_raw = json.loads(item["tags"]) if item["tags"] else []
                except (json.JSONDecodeError, TypeError):
                    tags_raw = []
                refs = ref_map.get(doc_id, [])

                sub_cat = item["sub_category"] if item["sub_category"] else ""
                alt_source = item["file_path"] if item["file_path"] else ""
                depth_val = item["depth"] if item["depth"] else "概述"
                create_raw = item["create_time"] if item["create_time"] else ""
                create_date = create_raw[:10] if len(create_raw) >= 10 else ""

                scope_val = item["scope"] if item["scope"] else "session"
                scene_val = item["scene"] if item["scene"] else ""
                emotion_val = item["emotion"] if item["emotion"] else ""
                tier_val = item["tier"] if item["tier"] else "warm"
                valid_from_val = item["valid_from"] if item["valid_from"] else ""
                valid_until_val = item["valid_until"] if item["valid_until"] else ""
                fm_lines = [
                    "---",
                    f"doc_id: {doc_id}",
                    f"title: {item['title'] or ''}",
                    f"label: {item['label']}",
                    f"importance: {item['importance']}",
                    f"category: {item['content_category']}",
                    f"sub_category: {sub_cat}",
                    f"weight: {item['weight']}",
                    f"depth: {depth_val}",
                    f"scope: {scope_val}",
                    f"tier: {tier_val}",
                    f"memory_tier: {item['memory_tier'] or 'warm'}",
                    f"memory_type: {item['memory_type'] or 'session'}",
                    f"stability: {item['stability'] or '半静态'}",
                    f"confidence: {item['confidence'] or '推测'}",
                ]
                if item['keywords']:
                    fm_lines.append(f"keywords: {item['keywords']}")
                if item['summary']:
                    fm_lines.append(f"summary: {item['summary'][:200]}")
                if item['project']:
                    fm_lines.append(f"project: {item['project']}")
                if scene_val:
                    fm_lines.append(f"scene: {scene_val}")
                if emotion_val:
                    fm_lines.append(f"emotion: {emotion_val}")
                if valid_from_val:
                    fm_lines.append(f"valid_from: {valid_from_val}")
                if valid_until_val:
                    fm_lines.append(f"valid_until: {valid_until_val}")
                fm_lines.extend([
                    f"centrality: {centrality.get(doc_id, 0):.4f}",
                    f"source: {alt_source}",
                    f"created: {create_date}",
                    f"updated: {datetime.now().strftime('%Y-%m-%d')}",
                ])
                if tags_raw:
                    fm_lines.append(f"tags: [{', '.join(tags_raw)}]")
                if refs:
                    link_titles = ", ".join(
                        f"[[{r['related_name']}]]" for r in refs
                    )
                    fm_lines.append(f"links: [{link_titles}]")
                fm_lines.extend(["---", ""])

                # Body — 按记忆类型格式化
                body = self._format_body_by_type(summary, item['label'] or '', item['content_category'] or '')
                if refs:
                    body.append("---")
                    body.append("## 关联记忆")
                    for r in refs:
                        label_tag = f" ({r['relation_type']})" if r['relation_type'] != 'related' else ""
                        note_part = f" — {r['note']}" if r['note'] else ""
                        body.append(f"- [[{r['related_name']}]]{label_tag}{note_part}")
                    body.append("")

                md_path.write_text("\n".join(fm_lines + body), encoding="utf-8")
                count += 1

            # ── 分类 MOC 导航页 ──
            moc_lines = [
                f"# {cat}",
                "",
                f"> 共 {len(items)} 条 | 最后更新：{datetime.now().strftime('%Y-%m-%d')}",
                "",
                "## 记忆列表",
                "",
                "| # | 文件 | 重要性 | 权重 | 简述 |",
                "|---|------|--------|------|------|",
            ]
            for i, item in enumerate(items, 1):
                nm = doc_to_name.get(item["doc_id"], f"doc_{item['doc_id']}")
                imp = item["importance"]
                wt = item["weight"]
                blurb = safe_truncate(item["compact_content"] or "", 60).replace("\n", " ")
                moc_lines.append(f"| {i} | [[{nm}]] | {imp} | {wt} | {blurb} |")
            moc_lines.append("")
            (cat_dir / "_moc.md").write_text("\n".join(moc_lines), encoding="utf-8")

        # ── 总 INDEX.md（Map of Content） ──
        index_lines = [
            "# Memory Workstation — 知识库索引",
            "",
            f"> 最后更新：{datetime.now().strftime('%Y-%m-%d %H:%M')} | 共 {count} 条记忆",
            "",
            "## 分类概览",
            "",
            "| 分类 | 数量 | 导航 |",
            "|------|------|------|",
        ]
        for cat, items in sorted(groups.items()):
            index_lines.append(f"| {cat} | {len(items)} | [[{cat}/_moc]] |")
        index_lines.extend(["", "---", "", "## 最近更新", ""])
        for r in sorted(rows, key=lambda x: x["create_time"] or "", reverse=True)[:10]:
            nm = doc_to_name.get(r["doc_id"], f"doc_{r['doc_id']}")
            cat = r["content_category"] or "未分类"
            index_lines.append(f"- [[{nm}]] — {r['importance']} ({cat})")

        (output_path / "INDEX.md").write_text("\n".join(index_lines), encoding="utf-8")

        return count

    def import_md(self, folder: str, dry_run: bool = False) -> int:
        """从 Markdown 文件夹导入记忆

        导入 export_md 导出的 Obsidian Vault 格式：
        - 读取所有 .md 文件（跳过 _moc.md 和 INDEX.md）
        - 解析 frontmatter 获取元数据
        - 提取 body 作为内容
        - 写入 memory_classify 表

        Args:
            folder: Markdown 文件夹路径
            dry_run: 预览模式（不写入数据库）

        Returns:
            导入的文件数
        """
        import yaml
        from pathlib import Path

        folder_path = Path(folder)
        if not folder_path.exists():
            print(f"❌ 文件夹不存在: {folder}")
            return 0

        # 查找所有 .md 文件（递归）
        md_files = list(folder_path.rglob("*.md"))
        # 跳过 _moc.md 和 INDEX.md
        md_files = [f for f in md_files if f.name not in ("_moc.md", "INDEX.md")]

        if not md_files:
            print(f"❌ 未找到 .md 文件: {folder}")
            return 0

        print(f"📁 找到 {len(md_files)} 个 .md 文件")

        count = 0
        for md_file in md_files:
            try:
                content = md_file.read_text(encoding="utf-8")
            except Exception as e:
                print(f"⚠️ 读取失败: {md_file.name} - {e}")
                continue

            # 解析 frontmatter
            frontmatter = {}
            body = content
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    try:
                        frontmatter = yaml.safe_load(parts[1]) or {}
                    except Exception:
                        pass
                    body = parts[2].strip()

            if not body:
                continue

            # 提取元数据
            doc_id = frontmatter.get("doc_id")
            label = frontmatter.get("label", "经验")
            importance = frontmatter.get("importance", "P1")
            category = frontmatter.get("category", "未分类")
            sub_category = frontmatter.get("sub_category", "")
            weight = frontmatter.get("weight", 50)
            depth = frontmatter.get("depth", "概述")
            tags = frontmatter.get("tags", [])
            scene = frontmatter.get("scene", "")
            emotion = frontmatter.get("emotion", "")
            tier = frontmatter.get("tier", "warm")
            valid_from = frontmatter.get("valid_from", "")
            valid_until = frontmatter.get("valid_until", "")

            if dry_run:
                print(f"  [预览] {md_file.name}: label={label}, category={category}, importance={importance}")
                count += 1
                continue

            # 检查是否已存在（通过 doc_id）
            if doc_id:
                existing = self._conn.execute(
                    "SELECT doc_id FROM memory_classify WHERE doc_id = ?",
                    (doc_id,)
                ).fetchone()
                if existing:
                    # 更新现有记录
                    self._conn.execute(
                        "UPDATE memory_classify SET compact_content = ?, weight = MAX(weight, ?) WHERE doc_id = ?",
                        (body, weight, doc_id)
                    )
                    count += 1
                    continue

            # 构建分类字典
            classification = {
                "label": label,
                "importance": importance,
                "category": category,
                "sub_category": sub_category,
                "depth": depth,
                "weight": weight,
                "summary": body[:200],
                "tags": tags,
                "scene": scene,
                "emotion": emotion,
                "tier": tier,
                "valid_from": valid_from,
                "valid_until": valid_until,
            }

            # 使用 _insert_classified 正确生成 doc_id
            try:
                doc_id = self._insert_classified(body, classification, source="import")
                if doc_id and doc_id > 0:
                    count += 1
                    print(f"  ✅ {md_file.name} → #{doc_id}")
                else:
                    print(f"  ⚠️ {md_file.name}: 插入失败")
            except Exception as e:
                print(f"  ⚠️ {md_file.name}: {e}")

        if not dry_run:
            self._conn.commit()
            print(f"✅ 已导入 {count} 条记忆")

        return count

    def export_jsonl(self, output_file: str) -> int:
        """导出 JSONL 逐行格式（跨 Agent 交换用）

        输出格式（每行一条 JSON）：
        {"type": "memory", "doc_id": 42, "label": "meta_rule", "summary": "打包部署规则...", "weight": 95, "relations": [{"target": 12, "type": "extend"}]}

        Args:
            output_file: 输出文件路径（如 "D:/exports/memories.jsonl"）

        Returns:
            导出的条数
        """
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # 查询所有记忆
        rows = self._conn.execute(
            """SELECT c.doc_id, c.label, c.importance, c.weight,
                      c.compact_content, c.content_category, c.sub_category,
                      c.scene, c.emotion, c.tier, c.valid_from, c.valid_until
               FROM memory_classify c
               JOIN document_files d ON c.doc_id = d.id
               WHERE d.is_deleted = 0 AND c.compact_content != ''"""
        ).fetchall()

        # 查询所有交叉引用
        cross_refs = self._conn.execute(
            "SELECT doc_id, related_doc_id, relation_type FROM memory_cross_ref"
        ).fetchall()
        ref_map: dict[int, list] = {}
        for cr in cross_refs:
            if cr["doc_id"] not in ref_map:
                ref_map[cr["doc_id"]] = []
            ref_map[cr["doc_id"]].append({
                "target": cr["related_doc_id"],
                "type": cr["relation_type"],
            })

        # 写入 JSONL
        count = 0
        with open(output_file, "w", encoding="utf-8") as f:
            for r in rows:
                doc_id = r["doc_id"]
                record = {
                    "type": "memory",
                    "doc_id": doc_id,
                    "label": r["label"],
                    "importance": r["importance"],
                    "weight": r["weight"],
                    "summary": r["compact_content"] or "",
                    "category": r["content_category"] or "",
                    "sub_category": r["sub_category"] or "",
                    "scene": r["scene"] or "",
                    "emotion": r["emotion"] or "",
                    "tier": r["tier"] or "warm",
                    "valid_from": r["valid_from"] or "",
                    "valid_until": r["valid_until"] or "",
                    "relations": ref_map.get(doc_id, []),
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1

        return count

    # ═══════════════════════════════════════════════════════════
    # 备份方法
    # ═══════════════════════════════════════════════════════════

    def backup(self, backup_dir: str) -> bool:
        """备份当前数据库

        Args:
            backup_dir: 备份目录路径

        Returns:
            是否备份成功
        """
        try:
            backup_path = Path(backup_dir)
            backup_path.mkdir(parents=True, exist_ok=True)
            # 用文件名 + 时间戳作为备份文件名
            db_name = Path(self._db_path).name
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            dst = backup_path / f"{db_name}.{ts}.bak"
            shutil.copy2(self._db_path, str(dst))
            return True
        except Exception as e:
            logger.error("backup 失败: %s", e)
            return False

    # ═══════════════════════════════════════════════════════════
    # 内部方法
    # ═══════════════════════════════════════════════════════════

    def get_conn(self) -> sqlite3.Connection:
        """获取底层数据库连接（供 cli_ingest 等内部模块直接操作）"""
        return self._conn

    def close(self):
        """关闭数据库连接和文件监听（确保所有连接都被关闭，不因某个步骤异常而泄漏）"""
        # 引用计数降为 0 时停止 watcher
        rc = MemoryClient._watcher_refcount
        if self._db_path in rc:
            rc[self._db_path] -= 1
            if rc[self._db_path] <= 0:
                del rc[self._db_path]
                MemoryClient.stop_watcher(self._db_path)

        if not self._write_lock.acquire(timeout=5):
            logger.warning("等待写锁超时，强制关闭连接")
        try:
            err = None
            if self._cpp_storage:
                try:
                    self._cpp_storage.close()
                except Exception as e:
                    err = e
            try:
                self._conn.close()
            except Exception as e:
                err = err or e
            if self._pool_conn:
                try:
                    self._pool_conn.close()
                except Exception as e:
                    err = err or e
                self._pool_conn = None
            if err:
                raise RuntimeError("close() 部分操作失败") from err
        finally:
            self._write_lock.release()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    @staticmethod
    def _tier(label: str) -> str:
        """标签 → 存储层级映射"""
        mapping = {
            "meta_rule": "long", "planning_doc": "work",
            "self_improve_learn": "work", "config_inventory": "work",
            "memory_layer": "long", "compact_archive": "archive",
            "chat_log": "short",
        }
        return mapping.get(label, "short")

    def _ensure_fts5(self):
        """确保 FTS5 索引表存在，使用 trigram tokenizer（支持中文子串匹配）"""
        try:
            # 先检查现有表
            row = self._conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='virtual' AND name='memory_fts'"
            ).fetchone()

            if row:
                sql = row[0] or ""
                if "trigram" in sql and "keywords" in sql:
                    # 已经是 trigram 且有 keywords 列，无需操作
                    self._check_fts5_health()
                    return
                if "unicode61" in sql:
                    # unicode61 不支持中文，需要重建
                    logger.info("FTS5 使用 unicode61，重建为 trigram")
                    self._conn.execute("DROP TABLE IF EXISTS memory_fts")
                    self._conn.commit()
                elif "keywords" not in sql:
                    # 有 trigram 但缺少 keywords 列，需要重建
                    logger.info("FTS5 缺少 keywords 列，重建中")
                    self._conn.execute("DROP TABLE IF EXISTS memory_fts")
                    self._conn.commit()
            # 表不存在或需要重建
            needs_repopulate = row is not None  # 如果是重建，需要重新填充
            self._conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                    doc_id UNINDEXED, title, summary, content_category, sub_category, compact_content, keywords,
                    tokenize='trigram'
                )
            """)
            self._conn.commit()
            logger.info("FTS5 表已创建（trigram tokenizer）")
            # 重建后重新填充索引
            if needs_repopulate:
                rows = self._conn.execute(
                    "SELECT c.doc_id, c.label, c.summary, c.content_category, c.sub_category, c.compact_content, "
                    "COALESCE(c.keywords, '') FROM memory_classify c "
                    "LEFT JOIN document_files d ON c.doc_id = d.id "
                    "WHERE c.compact_content != '' AND COALESCE(d.is_deleted, 0) = 0"
                ).fetchall()
                for r in rows:
                    self._conn.execute(
                        "INSERT INTO memory_fts(doc_id, title, summary, content_category, sub_category, compact_content, keywords) "
                        "VALUES(?, ?, ?, ?, ?, ?, ?)",
                        [r["doc_id"], r["label"], r["summary"], r["content_category"],
                         r["sub_category"], r["compact_content"], r[6]],
                    )
                self._conn.commit()
                logger.info("FTS5 索引已重建: %d 条", len(rows))
        except Exception as e:
            logger.warning("FTS5 creation failed: %s", e)
            return
        self._check_fts5_health()

    def _check_fts5_health(self):
        """检查 FTS5 索引是否落后于 classify 表 + pending 队列"""
        try:
            classify_count = self._conn.execute(
                "SELECT COUNT(*) FROM memory_classify c JOIN document_files d ON c.doc_id = d.id WHERE d.is_deleted = 0"
            ).fetchone()[0]
            fts_count = self._conn.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0]
            pending_count = self._conn.execute("SELECT COUNT(*) FROM fts_pending_rebuild").fetchone()[0]

            if pending_count > 0:
                logger.warning("FTS5 pending 队列 %d 条待重试，执行 `mw rebuild-fts5` 修复", pending_count)
            elif fts_count < classify_count:
                missing = classify_count - fts_count
                logger.warning(
                    "FTS5 索引落后 %d 条（classify=%d, fts=%d）。"
                    " 执行 `mw rebuild-fts5` 重建索引", missing, classify_count, fts_count
                )
            else:
                logger.info("FTS5 健康: %d 条", fts_count)
        except Exception as e:
            logger.debug("FTS5 health check skipped: %s", e)

    def _rebuild_fts5(self):
        """FTS5 重建"""
        if self._use_cpp and self._cpp_storage:
            return self._cpp_storage.rebuild_fts5()
        return False

    def _ensure_synonyms(self):
        """确保同义词表存在并写入默认种子数据"""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS memory_synonyms (
                word TEXT PRIMARY KEY,
                synonyms TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_synonyms_word ON memory_synonyms(word);
        """)
        # 仅在空表时写入默认同义词
        cnt = self._conn.execute("SELECT COUNT(*) FROM memory_synonyms").fetchone()[0]
        if cnt == 0:
            defaults = [
                ("网页", "前端 设计 UI 网站"),
                ("网站", "网页 前端 设计"),
                ("个人网页", "个人网站 portfolio 作品集"),
                ("修复", "bug 错误 排错 调试"),
                ("bug", "修复 错误 排错"),
                ("优化", "改进 提升 性能"),
                ("重构", "重写 改造 优化"),
                ("规则", "要求 规范 标准"),
                ("配置", "设置 环境 参数"),
                ("搜索", "查找 检索 查询"),
                ("记忆", "知识 存储 记录"),
                ("关键词", "keywords 标签 标记"),
            ]
            self._conn.executemany(
                "INSERT INTO memory_synonyms (word, synonyms) VALUES (?, ?)", defaults
            )
            self._conn.commit()
            logger.info("同义词表已初始化（%d 条）", len(defaults))

    # === FTS5 索引重建 ===

    def rebuild_fts5_index(self) -> int:
        """强制重建 FTS5 索引（DROP + re-create + populate）

        Returns:
            重建后索引的条目数
        """
        logger.info("开始强制重建 FTS5 索引")
        self._conn.execute("DROP TABLE IF EXISTS memory_fts")
        self._conn.commit()
        self._ensure_fts5()
        rows = self._conn.execute(
            "SELECT c.doc_id, c.label, c.summary, c.content_category, c.sub_category, c.compact_content, "
            "COALESCE(c.keywords, '') FROM memory_classify c "
            "LEFT JOIN document_files d ON c.doc_id = d.id "
            "WHERE c.compact_content != '' AND COALESCE(d.is_deleted, 0) = 0"
        ).fetchall()
        for row in rows:
            self._conn.execute(
                "INSERT INTO memory_fts(doc_id, title, summary, content_category, sub_category, compact_content, keywords) "
                "VALUES(?, ?, ?, ?, ?, ?, ?)",
                [row["doc_id"], row["label"], row["summary"], row["content_category"],
                 row["sub_category"], row["compact_content"], row[6]],
            )
        self._conn.commit()
        count = len(rows)
        logger.info("FTS5 索引重建完成: %d 条", count)
        return count

    # === 分类字段更新 ===

    _UPDATEABLE_FIELDS = frozenset({
        "scope", "content_category", "sub_category", "keywords",
        "importance", "label", "weight", "scene", "tier",
    })

    def update_classify_field(self, doc_id: int, field: str, value: str) -> bool:
        """更新 memory_classify 表的指定字段

        Args:
            doc_id: 文档 ID
            field: 字段名（必须在白名单内）
            value: 新值

        Returns:
            True if updated, False if field not allowed or doc not found
        """
        if field not in self._UPDATEABLE_FIELDS:
            logger.warning("不允许更新字段: %s", field)
            return False
        self._conn.execute(
            f"UPDATE memory_classify SET {field} = ? WHERE doc_id = ?",
            (value, doc_id)
        )
        self._conn.commit()
        return True

    # === 统计查询 ===

    def get_category_stats(self, category_filter: str | None = None) -> list[dict]:
        """获取分类统计

        Args:
            category_filter: 可选的 LIKE 过滤条件

        Returns:
            [{"category": str, "count": int}, ...]
        """
        if category_filter:
            rows = self._conn.execute(
                """SELECT content_category as category, COUNT(*) as cnt
                   FROM memory_classify
                   WHERE content_category LIKE ? AND compact_content != ''
                   GROUP BY content_category""",
                (f"%{category_filter}%",)
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT content_category as category, COUNT(*) as cnt
                   FROM memory_classify
                   WHERE compact_content != ''
                   GROUP BY content_category"""
            ).fetchall()
        return [{"category": r["category"] or "未分类", "count": r["cnt"]} for r in rows]

    def get_category_items(self, category: str, limit: int = 5) -> list[dict]:
        """获取指定分类下的前几条记忆

        Args:
            category: 分类名称
            limit: 返回条数

        Returns:
            [{"doc_id": int, "label": str, "importance": str, "weight": int}, ...]
        """
        rows = self._conn.execute(
            """SELECT doc_id, label, importance, weight
               FROM memory_classify
               WHERE content_category = ? AND compact_content != ''
               ORDER BY weight DESC LIMIT ?""",
            (category, limit)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_importance_stats(self) -> list[dict]:
        """获取重要性分布统计

        Returns:
            [{"importance": str, "count": int}, ...]
        """
        rows = self._conn.execute(
            """SELECT importance, COUNT(*) as cnt
               FROM memory_classify
               WHERE compact_content != ''
               GROUP BY importance
               ORDER BY importance"""
        ).fetchall()
        return [{"importance": r["importance"], "count": r["cnt"]} for r in rows]

    def get_total_count(self) -> int:
        """获取总记忆数（含空内容）"""
        return self._conn.execute("SELECT COUNT(*) FROM memory_classify").fetchone()[0]

    def get_content_count(self) -> int:
        """获取有内容的记忆数"""
        return self._conn.execute(
            "SELECT COUNT(*) FROM memory_classify WHERE compact_content != ''"
        ).fetchone()[0]
