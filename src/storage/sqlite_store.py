from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from datetime import datetime, timezone


def _extract_rule_keywords(text: str) -> set[str]:
    """从规则文本中提取关键词用于语义去重"""
    if not text:
        return set()
    # 去掉常见填充词
    stopwords = {'的', '了', '在', '是', '和', '与', '或', '及', '等',
                 'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be',
                 'been', 'being', 'have', 'has', 'had', 'do', 'does',
                 'did', 'will', 'would', 'could', 'should', 'may',
                 'might', 'can', 'shall', 'to', 'of', 'in', 'for',
                 'on', 'with', 'at', 'by', 'from', 'as', 'into',
                 'through', 'during', 'before', 'after', 'this', 'that',
                 '这些', '那些', '所有', '任何', '每个', '一些', '需要',
                 '可以', '进行', '使用', '通过', '确保', '包括', '包含',
                 '例如', '比如', '其中', '以及', '同时', '然后', '如果',
                 '那么', '因此', '所以', '但是', '然而', '不过', '只是'}
    # 提取中文词（2-6字）和英文词（3+字母）
    cn_words = set(re.findall(r'[\u4e00-\u9fff]{2,6}', text))
    en_words = set(w.lower() for w in re.findall(r'[a-zA-Z]{3,}', text))
    # 提取规则信号词作为强特征
    rule_signals = set()
    for sig in ['禁止', '必须', '不能', '不要', '应该', '永远', '只允许',
                '不得', '严禁', '务必', '记住', '规则', '红线', '优先',
                'never', 'always', 'must', 'shall', 'prohibited']:
        if sig in text.lower():
            rule_signals.add(sig)
    return (cn_words | en_words | rule_signals) - stopwords


def _keyword_overlap(kw1: set[str], kw2: set[str]) -> float:
    """计算两组关键词的Jaccard相似度"""
    if not kw1 or not kw2:
        return 0.0
    intersection = kw1 & kw2
    union = kw1 | kw2
    return len(intersection) / len(union) if union else 0.0

from ..core.enums import DocumentLabel, MemoryTier

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS document_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL UNIQUE,
    file_hash TEXT NOT NULL,
    file_size INTEGER,
    create_time TEXT NOT NULL,
    modify_time TEXT NOT NULL,
    update_ts TEXT,
    last_scan_time TEXT,
    last_classify_time TEXT,
    origin_source TEXT,
    source_folder TEXT,
    is_alive INTEGER DEFAULT 1,
    is_deleted INTEGER DEFAULT 0,
    version INTEGER DEFAULT 1,
    raw_text_snippet TEXT
);
CREATE INDEX IF NOT EXISTS idx_file_path ON document_files(file_path);
CREATE INDEX IF NOT EXISTS idx_file_hash ON document_files(file_hash);

CREATE TABLE IF NOT EXISTS memory_classify (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL REFERENCES document_files(id),
    label TEXT NOT NULL,
    memory_tier TEXT NOT NULL,
    importance TEXT DEFAULT 'P2',
    weight INTEGER DEFAULT 50,
    namespace TEXT DEFAULT 'default',
    relate_id TEXT,
    keywords TEXT,
    key_points TEXT,
    summary TEXT,
    compact_content TEXT,
    extra_tags TEXT,
    classify_record TEXT,
    content_category TEXT DEFAULT '',
    sub_category TEXT DEFAULT '',
    ai_type TEXT DEFAULT '',
    daily_type TEXT DEFAULT '',
    depth TEXT DEFAULT '概述',
    stability TEXT DEFAULT '半静态',
    confidence TEXT DEFAULT '推测',
    source TEXT DEFAULT '自己',
    cross_ref TEXT DEFAULT '[]',
    tags TEXT DEFAULT '[]',
    evolution_tier TEXT DEFAULT 'warm',  -- hot/warm/cold/archive，与 importance 正交
    UNIQUE(doc_id)
);
CREATE INDEX IF NOT EXISTS idx_label ON memory_classify(label);
CREATE INDEX IF NOT EXISTS idx_tier ON memory_classify(memory_tier);
CREATE INDEX IF NOT EXISTS idx_namespace ON memory_classify(namespace);
CREATE INDEX IF NOT EXISTS idx_importance ON memory_classify(importance);
CREATE INDEX IF NOT EXISTS idx_category ON memory_classify(content_category);
CREATE INDEX IF NOT EXISTS idx_ai_type ON memory_classify(ai_type);

CREATE TABLE IF NOT EXISTS memory_access_record (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL REFERENCES document_files(id),
    access_time TEXT NOT NULL,
    client_type TEXT,
    task_context TEXT
);
CREATE INDEX IF NOT EXISTS idx_access_doc_id ON memory_access_record(doc_id);

CREATE TABLE IF NOT EXISTS processing_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL UNIQUE,
    file_hash TEXT NOT NULL,
    last_processed TEXT NOT NULL,
    doc_id INTEGER,
    version INTEGER DEFAULT 1,
    status TEXT DEFAULT 'done'
);
CREATE INDEX IF NOT EXISTS idx_processing_path ON processing_log(file_path);

CREATE TABLE IF NOT EXISTS system_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_name TEXT NOT NULL UNIQUE,
    create_time TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    file_size INTEGER
);

CREATE TABLE IF NOT EXISTS knowledge_domains (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    namespace TEXT DEFAULT 'default',
    doc_count INTEGER DEFAULT 0,
    embedding BLOB,
    created_at TEXT NOT NULL,
    UNIQUE(name, namespace)
);
CREATE INDEX IF NOT EXISTS idx_domain_ns ON knowledge_domains(namespace);

CREATE TABLE IF NOT EXISTS global_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_text TEXT NOT NULL,
    
    -- 分类字段
    category TEXT NOT NULL DEFAULT 'rule',
    sub_category TEXT NOT NULL DEFAULT 'behavior',
    scope TEXT NOT NULL DEFAULT 'global',
    priority TEXT DEFAULT 'normal',
    ttl TEXT DEFAULT 'M',
    
    -- 内容字段
    tags TEXT DEFAULT '[]',
    
    -- 使用统计
    reference_count INTEGER DEFAULT 0,
    last_used TEXT,
    
    -- 索引提示（JSON格式）
    index_hint TEXT DEFAULT '{}',
    
    -- 来源
    source_file TEXT,
    source_doc_id INTEGER,
    confidence REAL DEFAULT 0.8,
    
    -- 冲突和互补
    conflict_with TEXT DEFAULT '[]',
    complements TEXT DEFAULT '[]',
    
    -- token预算
    max_tokens_budget INTEGER DEFAULT 50,
    
    -- 状态
    status TEXT DEFAULT 'active',
    
    -- 时间
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    
    UNIQUE(rule_text)
);
CREATE INDEX IF NOT EXISTS idx_gr_scope ON global_rules(scope);
CREATE INDEX IF NOT EXISTS idx_gr_active ON global_rules(status);
CREATE INDEX IF NOT EXISTS idx_gr_category ON global_rules(category);
CREATE INDEX IF NOT EXISTS idx_gr_priority ON global_rules(priority);

CREATE TABLE IF NOT EXISTS memory_entity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL REFERENCES document_files(id),
    entity_name TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    weight REAL DEFAULT 1.0,
    created_at TEXT NOT NULL,
    UNIQUE(doc_id, entity_name, entity_type)
);
CREATE INDEX IF NOT EXISTS idx_entity_doc ON memory_entity(doc_id);
CREATE INDEX IF NOT EXISTS idx_entity_name ON memory_entity(entity_name);
CREATE INDEX IF NOT EXISTS idx_entity_type ON memory_entity(entity_type);

CREATE TABLE IF NOT EXISTS memory_task (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type TEXT NOT NULL,
    source_rule_id INTEGER,
    source_doc_id INTEGER,
    description TEXT NOT NULL,
    evidence_doc_ids TEXT DEFAULT '[]',
    min_evidence INTEGER DEFAULT 3,
    confidence REAL DEFAULT 0.5,
    status TEXT DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_status ON memory_task(status);
CREATE INDEX IF NOT EXISTS idx_task_type ON memory_task(task_type);

-- ═══════════════════════════════════════════════════════════
-- V7: 交叉引用表 + 健康检查日志表（MW LLM Wiki 重构）
-- 用途：支持 /mw-ingest 写入关联、/mw-lint 读取检测结果
-- ═══════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS memory_cross_ref (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL,
    related_doc_id INTEGER NOT NULL,
    relation_type TEXT NOT NULL DEFAULT 'related',
    -- supplement: 补充说明 | refute: 反驳/矛盾 | extend: 扩展 | premise: 前提 | example: 示例
    note TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (doc_id) REFERENCES memory_classify(doc_id),
    FOREIGN KEY (related_doc_id) REFERENCES memory_classify(doc_id),
    UNIQUE(doc_id, related_doc_id, relation_type)
);

CREATE TABLE IF NOT EXISTS lint_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    check_time TEXT DEFAULT (datetime('now')),
    check_type TEXT NOT NULL,       -- orphan | duplicate | conflict | stale | broken_link
    severity TEXT NOT NULL,         -- info | warning | error
    doc_ids TEXT,                   -- 涉及 doc_id（JSON array）
    description TEXT,
    suggestion TEXT,
    resolved INTEGER DEFAULT 0     -- 0=未处理, 1=已处理
);

-- ═══════════════════════════════════════════════════════════
-- V8: 行为进化系统（MW Evolution Plan）
-- 用途：/mw-reflect 自我反思 + /mw-evolve 语义升降级 + /mw-log 查看历史
-- 约束：永不修改 memory_classify.importance（降级=杀死导出管道）
-- 约束：MemoryOptimizer 只做候选发现，不做用户交互
-- ═══════════════════════════════════════════════════════════

-- 进化记录：一条/次进化事件
-- evolution_log 只记录已发生的事件，不记录"待决策"状态。
-- "待用户确认"的状态由 correction_log.promoted=0 承载。
CREATE TABLE IF NOT EXISTS evolution_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,         -- reflection | correction | tier_change | confirm
    trigger TEXT NOT NULL,            -- 触发源：user_correction | scheduled | user_request | agent_initiative
    target_doc_id INTEGER,            -- 涉及的记忆 doc_id（如果有）
    detail TEXT,                      -- 事件描述
    certainty REAL DEFAULT 0.0,       -- 该进化的置信度 0.0-1.0
    created_at TEXT DEFAULT (datetime('now'))
);

-- 纠正历史：用户每次纠正的记录（用于 3 次自动晋升检测）
-- pattern 字段存 Agent 提炼的标准化短语（如 "prefer_tabs_over_spaces"），
-- 不是原文也不是哈希。写入时 Agent 保持短语一致，以便同类归并。
-- suppressed_at — 用户说不问了，本模式24h内不重复触发确认
CREATE TABLE IF NOT EXISTS correction_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT NOT NULL,            -- Agent 提炼的标准化短语，如 "prefer_tabs_over_spaces"
    summary TEXT NOT NULL,            -- "用户说用 tab 不是空格"
    context TEXT,                     -- 当时在讨论什么
    occurred_at TEXT DEFAULT (datetime('now')),
    count INTEGER DEFAULT 1,         -- 同类纠正累计次数
    last_occurred_at TEXT DEFAULT (datetime('now')),
    promoted INTEGER DEFAULT 0,       -- 0=未晋升, 1=已晋升为规则
    suppressed_at TEXT                -- 用户拒绝后标记，24h内不再问
);

-- 记忆层级变更历史
-- evolution_tier 是新增字段（ALTER TABLE memory_classify 追加），与 importance 正交。
-- 降级 evolution_tier 不影响导出/搜索/global_rules 管道。
CREATE TABLE IF NOT EXISTS tier_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL,
    from_tier TEXT NOT NULL,          -- hot | warm | cold | archive
    to_tier TEXT NOT NULL,
    reason TEXT,                      -- promoted_3x_corrections | deprecated_30d | archived_90d | user_request
    applied_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (doc_id) REFERENCES memory_classify(doc_id)
);

-- ═══════════════════════════════════════════════════════════
-- V10 Phase 2: 审核队列 + 排除规则
-- 用途：审核页独立存储待审核记录，用户标记无效后自动积累排除规则
-- ═══════════════════════════════════════════════════════════

-- 审核队列：weight=20 的文档自动入队，审核后更新 memory_classify
CREATE TABLE IF NOT EXISTS review_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL UNIQUE,
    status TEXT DEFAULT 'pending',   -- pending | reviewed | excluded
    enqueue_reason TEXT,             -- 入队原因：keyword_miss / manual / exclusion_miss
    reviewer TEXT DEFAULT 'manual',  -- 审核人：manual / auto
    reviewed_at TEXT,
    review_result TEXT,              -- 审核结果 JSON：{"label":"...","importance":"P2","category":"..."}
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (doc_id) REFERENCES document_files(id)
);
CREATE INDEX IF NOT EXISTS idx_rq_status ON review_queue(status);

-- 排除规则：用户标记"无效"后自动积累，后续扫描命中时直接跳过
CREATE TABLE IF NOT EXISTS classification_exclusions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_type TEXT NOT NULL,         -- path_pattern / content_pattern / extension / name_pattern
    rule_value TEXT NOT NULL,        -- 排除规则值（正则或通配符）
    description TEXT,                -- 规则说明
    hit_count INTEGER DEFAULT 0,     -- 命中次数
    created_at TEXT DEFAULT (datetime('now')),
    is_active INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_ce_type ON classification_exclusions(rule_type);
"""

MIGRATION_SQL = """
-- V2 migration: add columns if missing (safe for existing V1 databases)
ALTER TABLE document_files ADD COLUMN update_ts TEXT;
ALTER TABLE document_files ADD COLUMN source_folder TEXT;
ALTER TABLE document_files ADD COLUMN is_alive INTEGER DEFAULT 1;
ALTER TABLE document_files ADD COLUMN is_deleted INTEGER DEFAULT 0;
ALTER TABLE document_files ADD COLUMN version INTEGER DEFAULT 1;

ALTER TABLE memory_classify ADD COLUMN importance TEXT DEFAULT 'P2';
ALTER TABLE memory_classify ADD COLUMN namespace TEXT DEFAULT 'default';
ALTER TABLE memory_classify ADD COLUMN relate_id TEXT;
ALTER TABLE memory_classify ADD COLUMN keywords TEXT;
ALTER TABLE memory_classify ADD COLUMN key_points TEXT;
ALTER TABLE memory_classify ADD COLUMN summary TEXT;
ALTER TABLE memory_classify ADD COLUMN compact_content TEXT;
ALTER TABLE memory_classify ADD COLUMN extra_tags TEXT;

-- V3: 多维度分类字段
ALTER TABLE memory_classify ADD COLUMN content_category TEXT DEFAULT '';
ALTER TABLE memory_classify ADD COLUMN sub_category TEXT DEFAULT '';
ALTER TABLE memory_classify ADD COLUMN ai_type TEXT DEFAULT '';
ALTER TABLE memory_classify ADD COLUMN daily_type TEXT DEFAULT '';
ALTER TABLE memory_classify ADD COLUMN depth TEXT DEFAULT '概述';
ALTER TABLE memory_classify ADD COLUMN stability TEXT DEFAULT '半静态';
ALTER TABLE memory_classify ADD COLUMN confidence TEXT DEFAULT '推测';
ALTER TABLE memory_classify ADD COLUMN source TEXT DEFAULT '自己';
ALTER TABLE memory_classify ADD COLUMN cross_ref TEXT DEFAULT '[]';
ALTER TABLE memory_classify ADD COLUMN tags TEXT DEFAULT '[]';

ALTER TABLE memory_access_record ADD COLUMN task_context TEXT;

-- V4: 全局规则支持
ALTER TABLE memory_classify ADD COLUMN scope TEXT DEFAULT 'session';
ALTER TABLE memory_classify ADD COLUMN rule_text TEXT DEFAULT '';

-- V5: 全局规则扩展字段
ALTER TABLE global_rules ADD COLUMN category TEXT DEFAULT 'rule';
ALTER TABLE global_rules ADD COLUMN sub_category TEXT DEFAULT 'behavior';
ALTER TABLE global_rules ADD COLUMN priority TEXT DEFAULT 'normal';
ALTER TABLE global_rules ADD COLUMN ttl TEXT DEFAULT 'M';
ALTER TABLE global_rules ADD COLUMN tags TEXT DEFAULT '[]';
ALTER TABLE global_rules ADD COLUMN reference_count INTEGER DEFAULT 0;
ALTER TABLE global_rules ADD COLUMN last_used TEXT;
ALTER TABLE global_rules ADD COLUMN index_hint TEXT DEFAULT '{}';
ALTER TABLE global_rules ADD COLUMN conflict_with TEXT DEFAULT '[]';
ALTER TABLE global_rules ADD COLUMN complements TEXT DEFAULT '[]';
ALTER TABLE global_rules ADD COLUMN max_tokens_budget INTEGER DEFAULT 50;
ALTER TABLE global_rules ADD COLUMN status TEXT DEFAULT 'active';

-- V6: 规则质量评分体系
ALTER TABLE global_rules ADD COLUMN rule_type TEXT DEFAULT 'knowledge';
ALTER TABLE global_rules ADD COLUMN score_universality INTEGER DEFAULT 3;
ALTER TABLE global_rules ADD COLUMN score_cost INTEGER DEFAULT 2;
ALTER TABLE global_rules ADD COLUMN score_actionable INTEGER DEFAULT 3;
ALTER TABLE global_rules ADD COLUMN score_timeliness INTEGER DEFAULT 3;
ALTER TABLE global_rules ADD COLUMN priority_weight INTEGER DEFAULT 36;
ALTER TABLE global_rules ADD COLUMN parent_rule_id INTEGER DEFAULT NULL;
ALTER TABLE global_rules ADD COLUMN gate_status TEXT DEFAULT 'pending';

-- V8: 行为进化系统
ALTER TABLE memory_classify ADD COLUMN evolution_tier TEXT DEFAULT 'warm';
"""


RULE_TYPES = {
    "meta": "持久层 — Agent行为元规则，不评分始终加载",
    "domain": "领域层 — 思维框架/专业视角，场景命中带出",
    "standard": "标准层 — 领域内工程规范，场景命中加载",
    "knowledge": "知识层 — 可复用探索结论，评分过滤加载",
    "index": "索引层 — 项目/配置事实，精确匹配查询",
}

DOOR_GATE_RULES = {
    "delete": "通用度≤1或(通用度≤2且可操作性≤2) 直接删除不入库",
    "project_only": "通用度≤2且可操作性≥3 标记项目专有，不进跨项目查询",
    "gold": "通用度≥3且推导成本≥3 金牌候选规则",
    "expired": "时效性=1 标记过期，不参与查询",
}


def calc_priority_weight(uni: int, cost: int, actionable: int, timeliness: int) -> int:
    """计算规则权重 = 通用度 × 推导成本 × 可操作性 × 时效性"""
    return uni * cost * actionable * timeliness


def run_door_gate(uni: int, actionable: int, timeliness: int) -> str:
    """门禁安检：判断规则能否入库"""
    if uni <= 1 or (uni <= 2 and actionable <= 2):
        return "delete"
    if uni <= 2 and actionable >= 3:
        return "project_only"
    if timeliness <= 1:
        return "expired"
    return "pass"


class SQLiteStore:
    def __init__(self, db_path: str, enable_wal: bool = True):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._enable_wal = enable_wal
        self._write_lock = threading.Lock()
        self._on_delete_cb: Optional[callable] = None  # 文档删除回调，用于同步清理向量索引

    def set_delete_callback(self, cb: callable):
        """设置文档删除回调（供 vector_store.delete_by_doc_id 使用）"""
        self._on_delete_cb = cb

    def connect(self):
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            timeout=10,
        )
        self._conn.row_factory = sqlite3.Row
        if self._enable_wal:
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_schema()
        logger.info("SQLite connected: %s", self.db_path)

    def _init_schema(self):
        self._migrate_v2()
        self._conn.executescript(SCHEMA_SQL)
        self._conn.commit()

    def _migrate_v2(self):
        for line in MIGRATION_SQL.strip().split(";"):
            line = line.strip()
            if not line:
                continue
            try:
                self._conn.execute(line)
            except sqlite3.OperationalError:
                pass
        self._conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def integrity_check(self) -> bool:
        if self._conn is None:
            return False
        result = self._conn.execute("PRAGMA integrity_check").fetchone()
        return result[0] == "ok"

    def upsert_document(
        self,
        file_path: str,
        file_hash: str,
        file_size: int,
        create_time: str,
        modify_time: str,
        origin_source: str = "manual",
        raw_text_snippet: str = "",
    ) -> int:
        with self._write_lock:
            now = _utc_now()
            self._conn.execute(
                """INSERT INTO document_files
                   (file_path, file_hash, file_size, create_time, modify_time,
                    last_scan_time, origin_source, raw_text_snippet)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(file_path) DO UPDATE SET
                       file_hash=excluded.file_hash,
                       file_size=excluded.file_size,
                       modify_time=excluded.modify_time,
                       last_scan_time=excluded.last_scan_time,
                       raw_text_snippet=excluded.raw_text_snippet""",
                (file_path, file_hash, file_size, create_time, modify_time,
                 now, origin_source, raw_text_snippet),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT id FROM document_files WHERE file_path=?", (file_path,)
            ).fetchone()
            return row[0] if row else self._conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def set_classification(
        self,
        doc_id: int,
        label: DocumentLabel,
        tier: MemoryTier,
        weight: int = 50,
        importance: str = "P2",
        namespace: str = "default",
        relate_id: Optional[str] = None,
        compact_content: Optional[str] = None,
        keywords: Optional[str] = None,
        key_points: Optional[str] = None,
        summary: Optional[str] = None,
        extra_tags: Optional[str] = None,
        content_category: str = "",
        sub_category: str = "",
        ai_type: str = "",
        daily_type: str = "",
        depth: str = "概述",
        stability: str = "半静态",
        confidence: str = "推测",
        source: str = "自己",
        cross_ref: str = "[]",
        tags: str = "[]",
    ):
        with self._write_lock:
            self._conn.execute(
                """INSERT INTO memory_classify
                   (doc_id, label, memory_tier, weight, importance, namespace,
                    relate_id, compact_content, keywords, key_points, summary,
                    extra_tags, classify_record, content_category, sub_category,
                    ai_type, daily_type, depth, stability, confidence, source,
                    cross_ref, tags)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(doc_id) DO UPDATE SET
                       label=excluded.label,
                       memory_tier=excluded.memory_tier,
                       weight=excluded.weight,
                       importance=excluded.importance,
                       namespace=excluded.namespace,
                       relate_id=COALESCE(excluded.relate_id, memory_classify.relate_id),
                       compact_content=COALESCE(excluded.compact_content, memory_classify.compact_content),
                       keywords=COALESCE(excluded.keywords, memory_classify.keywords),
                       key_points=COALESCE(excluded.key_points, memory_classify.key_points),
                       summary=COALESCE(excluded.summary, memory_classify.summary),
                       extra_tags=COALESCE(excluded.extra_tags, memory_classify.extra_tags),
                       classify_record=excluded.classify_record,
                       content_category=excluded.content_category,
                       sub_category=excluded.sub_category,
                       ai_type=excluded.ai_type,
                       daily_type=excluded.daily_type,
                       depth=excluded.depth,
                       stability=excluded.stability,
                       confidence=excluded.confidence,
                       source=excluded.source,
                       cross_ref=excluded.cross_ref,
                       tags=excluded.tags""",
                (doc_id, label.value, tier.value, weight, importance, namespace,
                 relate_id, compact_content, keywords, key_points, summary,
                 extra_tags, json.dumps({"ts": _utc_now()}),
                 content_category, sub_category, ai_type, daily_type,
                 depth, stability, confidence, source, cross_ref, tags),
            )
            self._conn.commit()

    def save_cross_refs(self, doc_id: int, refs: list[dict]) -> int:
        """批量写入交叉引用，幂等"""
        if not refs:
            return 0
        count = 0
        with self._write_lock:
            for ref in refs:
                try:
                    self._conn.execute(
                        """INSERT OR IGNORE INTO memory_cross_ref
                           (doc_id, related_doc_id, relation_type, note)
                           VALUES (?, ?, ?, ?)""",
                        (doc_id, ref.get("related_doc_id", 0),
                         ref.get("relation_type", "related"),
                         ref.get("note", "")),
                    )
                    count += 1
                except Exception:
                    pass
            self._conn.commit()
        return count

    def auto_cross_ref(self, doc_id: int, category: str = "",
                       entity_names: list[str] | None = None,
                       top_k: int = 3) -> int:
        """入库后自动建关联：同分类 + 同 entity 的记忆互相关联

        策略（按优先级）：
        1. 同 category 且有共享实体 → 双向关联
        2. 同 category → 单向关联（新到旧）
        3. 自己是自己的 → 跳过

        Args:
            doc_id: 新入库的文档 ID
            category: 分类名
            entity_names: 实体名列表
            top_k: 最多关联几条
        """
        if not category and not entity_names:
            return 0

        candidates = set()
        try:
            if category:
                rows = self._conn.execute(
                    """SELECT doc_id FROM memory_classify
                       WHERE content_category = ? AND doc_id != ?
                       ORDER BY weight DESC LIMIT ?""",
                    (category, doc_id, top_k * 2),
                ).fetchall()
                for r in rows:
                    candidates.add(r["doc_id"])

            if entity_names:
                placeholders = ",".join("?" for _ in entity_names)
                rows = self._conn.execute(
                    f"""SELECT DISTINCT e.doc_id FROM memory_entity e
                        WHERE e.entity_name IN ({placeholders})
                          AND e.doc_id != ?
                        ORDER BY e.weight DESC LIMIT ?""",
                    (*entity_names, doc_id, top_k),
                ).fetchall()
                for r in rows:
                    candidates.add(r["doc_id"])
        except Exception:
            return 0

        if not candidates:
            return 0

        count = 0
        with self._write_lock:
            for cid in list(candidates)[:top_k]:
                try:
                    self._conn.execute(
                        """INSERT OR IGNORE INTO memory_cross_ref
                           (doc_id, related_doc_id, relation_type, note)
                           VALUES (?, ?, 'related', ?)""",
                        (doc_id, cid, "auto: same category/entity"),
                    )
                    self._conn.execute(
                        """INSERT OR IGNORE INTO memory_cross_ref
                           (doc_id, related_doc_id, relation_type, note)
                           VALUES (?, ?, 'related', ?)""",
                        (cid, doc_id, "auto: same category/entity"),
                    )
                    count += 1
                except Exception:
                    pass
            self._conn.commit()
        return count

    def save_entities(self, doc_id: int, entities: list[dict]) -> None:
        if not entities:
            return
        now = _utc_now()
        with self._write_lock:
            for ent in entities:
                name = ent.get("name", "").strip()
                etype = ent.get("type", "").strip()
                if not name or not etype:
                    continue
                self._conn.execute(
                    """INSERT INTO memory_entity (doc_id, entity_name, entity_type, weight, created_at)
                       VALUES (?, ?, ?, 1.0, ?)
                       ON CONFLICT(doc_id, entity_name, entity_type)
                       DO UPDATE SET weight = weight + 1""",
                    (doc_id, name, etype, now),
                )
            self._conn.commit()

    # ═══════════════════════════════════════════════════════════
    # V10 Phase 2: 审核队列
    # ═══════════════════════════════════════════════════════════

    def enqueue_for_review(self, doc_id: int, reason: str = "keyword_miss") -> None:
        """将文档加入审核队列（weight=20 时调用）"""
        now = _utc_now()
        self._conn.execute(
            """INSERT OR IGNORE INTO review_queue (doc_id, enqueue_reason, created_at)
               VALUES (?, ?, ?)""",
            (doc_id, reason, now),
        )
        self._conn.commit()

    def get_pending_reviews(self, limit: int = 100) -> list[dict]:
        """获取待审核列表"""
        rows = self._conn.execute("""
            SELECT rq.id, rq.doc_id, rq.enqueue_reason, rq.created_at,
                   d.file_path, c.label, c.importance, c.weight,
                   c.content_category, c.compact_content
            FROM review_queue rq
            JOIN document_files d ON rq.doc_id = d.id
            LEFT JOIN memory_classify c ON rq.doc_id = c.doc_id
            WHERE rq.status = 'pending' AND d.is_deleted = 0
            ORDER BY rq.created_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def apply_review(self, doc_id: int, label: str, importance: str,
                     category: str = "", sub_category: str = "") -> bool:
        """应用审核结果：更新 memory_classify + 标记 review_queue 已审核"""
        now = _utc_now()
        with self._write_lock:
            # 更新分类
            self._conn.execute(
                """UPDATE memory_classify
                   SET label=?, importance=?, weight=50,
                       content_category=?, sub_category=?
                   WHERE doc_id=?""",
                (label, importance, category, sub_category, doc_id),
            )
            # 标记审核完成
            result = json.dumps({"label": label, "importance": importance,
                                 "category": category, "sub_category": sub_category})
            self._conn.execute(
                """UPDATE review_queue
                   SET status='reviewed', reviewer='manual',
                       reviewed_at=?, review_result=?
                   WHERE doc_id=? AND status='pending'""",
                (now, result, doc_id),
            )
            # 同步 FTS（如果存在）
            try:
                self._conn.execute(
                    "DELETE FROM memory_fts WHERE doc_id=?", (doc_id,)
                )
                row = self._conn.execute(
                    "SELECT compact_content FROM memory_classify WHERE doc_id=?",
                    (doc_id,),
                ).fetchone()
                if row:
                    self._conn.execute(
                        "INSERT INTO memory_fts(doc_id, title, summary, content_category, sub_category) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (doc_id, (row[0] or "")[:200], row[0] or "", category, sub_category),
                    )
            except Exception:
                pass
            self._conn.commit()
            return True

    def mark_review_excluded(self, doc_id: int, exclusion_rule: str = "") -> bool:
        """标记审核为排除（用户认为文档无效）"""
        now = _utc_now()
        with self._write_lock:
            self._conn.execute(
                """UPDATE review_queue SET status='excluded', reviewed_at=?,
                   review_result=? WHERE doc_id=?""",
                (now, json.dumps({"action": "exclude", "rule": exclusion_rule}), doc_id),
            )
            self._conn.commit()
            return True

    # ═══════════════════════════════════════════════════════════
    # V10 Phase 2: 排除规则
    # ═══════════════════════════════════════════════════════════

    def add_exclusion(self, rule_type: str, rule_value: str,
                      description: str = "") -> int:
        """添加排除规则"""
        now = _utc_now()
        self._conn.execute(
            """INSERT INTO classification_exclusions (rule_type, rule_value, description, created_at)
               VALUES (?, ?, ?, ?)""",
            (rule_type, rule_value, description, now),
        )
        self._conn.commit()
        return self._conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def get_exclusions(self, active_only: bool = True) -> list[dict]:
        """获取排除规则列表"""
        query = "SELECT * FROM classification_exclusions"
        if active_only:
            query += " WHERE is_active = 1"
        query += " ORDER BY created_at DESC"
        return [dict(r) for r in self._conn.execute(query).fetchall()]

    def check_exclusion(self, filepath: str, content: str = "") -> dict | None:
        """检查文档是否命中排除规则，返回命中的规则或 None"""
        exclusions = self.get_exclusions()
        fname = filepath.split("/")[-1].split("\\")[-1].lower()
        fpath_norm = filepath.replace("\\", "/").lower()
        for ex in exclusions:
            rt = ex["rule_type"]
            rv = ex["rule_value"]
            if rt == "path_pattern" and re.search(rv, fpath_norm):
                self._increment_exclusion_hit(ex["id"])
                return ex
            elif rt == "extension":
                ext = "." + fname.rsplit(".", 1)[-1] if "." in fname else ""
                if ext == rv or fname.endswith(rv):
                    self._increment_exclusion_hit(ex["id"])
                    return ex
            elif rt == "name_pattern" and re.search(rv, fname):
                self._increment_exclusion_hit(ex["id"])
                return ex
            elif rt == "content_pattern" and content and re.search(rv, content[:2000]):
                self._increment_exclusion_hit(ex["id"])
                return ex
        return None

    def _increment_exclusion_hit(self, exclusion_id: int) -> None:
        """增加排除规则命中计数"""
        self._conn.execute(
            "UPDATE classification_exclusions SET hit_count = hit_count + 1 WHERE id = ?",
            (exclusion_id,),
        )
        self._conn.commit()

    def create_task(self, task_type: str, source_rule_id: int = None,
                    source_doc_id: int = None, description: str = "",
                    min_evidence: int = 3) -> int:
        now = _utc_now()
        self._conn.execute(
            "INSERT INTO memory_task (task_type, source_rule_id, source_doc_id, "
            "description, min_evidence, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
            (task_type, source_rule_id, source_doc_id, description, min_evidence, now, now),
        )
        self._conn.commit()
        return self._conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def get_pending_tasks(self, task_type: str = None, limit: int = 20) -> list[dict]:
        query = "SELECT * FROM memory_task WHERE status='pending'"
        params = []
        if task_type:
            query += " AND task_type=?"
            params.append(task_type)
        query += f" ORDER BY created_at DESC LIMIT {limit}"
        return [dict(r) for r in self._conn.execute(query, params).fetchall()]

    def update_task_evidence(self, task_id: int, doc_id: int) -> dict:
        task = self._conn.execute("SELECT * FROM memory_task WHERE id=?", (task_id,)).fetchone()
        if not task or task["status"] != "pending":
            return {"upgraded": False}

        evidence = json.loads(task["evidence_doc_ids"] or "[]")
        if doc_id not in evidence:
            evidence.append(doc_id)

        new_confidence = min(0.5 + len(evidence) * 0.15, 1.0)
        upgraded = False

        if len(evidence) >= task["min_evidence"]:
            self._conn.execute(
                "UPDATE memory_task SET status='confirmed', confidence=?, "
                "evidence_doc_ids=?, updated_at=? WHERE id=?",
                (new_confidence, json.dumps(evidence), _utc_now(), task_id),
            )
            if task["source_rule_id"]:
                self._conn.execute(
                    "UPDATE global_rules SET confidence=?, updated_at=? WHERE id=?",
                    (new_confidence, _utc_now(), task["source_rule_id"]),
                )
            upgraded = True
        else:
            self._conn.execute(
                "UPDATE memory_task SET confidence=?, evidence_doc_ids=?, updated_at=? WHERE id=?",
                (new_confidence, json.dumps(evidence), _utc_now(), task_id),
            )

        self._conn.commit()
        return {"upgraded": upgraded, "confidence": new_confidence, "evidence_count": len(evidence)}

    def ensure_fts5(self):
        self._conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                doc_id UNINDEXED,
                title,
                summary,
                content_category,
                sub_category
            )
        """)
        self._conn.commit()

    def rebuild_fts(self):
        self._conn.execute("DELETE FROM memory_fts")
        rows = self._conn.execute("""
            SELECT doc_id, compact_content, summary, content_category, sub_category
            FROM memory_classify
        """).fetchall()
        for r in rows:
            self._conn.execute(
                "INSERT INTO memory_fts(doc_id, title, summary, content_category, sub_category) VALUES (?, ?, ?, ?, ?)",
                (r[0], (r[1] or "")[:200], r[2] or "", r[3] or "", r[4] or ""),
            )
        self._conn.commit()
        logger.info("FTS5 rebuilt: %d rows", len(rows))

    def search_fts(self, query: str, limit: int = 20) -> list[dict]:
        rows = self._conn.execute("""
            SELECT doc_id, bm25(memory_fts, 1.0, 5.0, 3.0, 2.0) AS score
            FROM memory_fts
            WHERE memory_fts MATCH ?
            ORDER BY score
            LIMIT ?
        """, [query, limit]).fetchall()
        return [{"doc_id": r[0], "score": -r[1]} for r in rows]

    def get_document_by_hash(self, file_hash: str) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM document_files WHERE file_hash=?", (file_hash,)
        ).fetchone()

    def get_document_by_path(self, file_path: str) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM document_files WHERE file_path=?", (file_path,)
        ).fetchone()

    def search_memory(
        self,
        keyword: str = "",
        tier: Optional[str] = None,
        label: Optional[str] = None,
        namespace: Optional[str] = None,
        importance: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict]:
        query = """
            SELECT d.file_path, d.raw_text_snippet, d.file_hash, d.id as doc_id,
                   c.label, c.memory_tier, c.weight, c.importance, c.namespace,
                   c.compact_content, c.relate_id, c.extra_tags
            FROM document_files d
            JOIN memory_classify c ON d.id = c.doc_id
            WHERE d.is_deleted = 0
        """
        params: list = []
        if keyword:
            query += " AND (d.raw_text_snippet LIKE ? OR c.compact_content LIKE ?)"
            params.extend([f"%{keyword}%", f"%{keyword}%"])
        if tier:
            query += " AND c.memory_tier = ?"
            params.append(tier)
        if label:
            query += " AND c.label = ?"
            params.append(label)
        if namespace:
            query += " AND c.namespace = ?"
            params.append(namespace)
        if importance:
            query += " AND c.importance = ?"
            params.append(importance)
        query += " ORDER BY c.weight DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_memories_by_doc_ids(self, doc_ids: list[int]) -> list[dict]:
        """按 doc_id 列表批量查询记忆（用于向量搜索后的元数据补充）"""
        if not doc_ids:
            return []
        placeholders = ",".join("?" * len(doc_ids))
        query = f"""
            SELECT d.file_path, d.raw_text_snippet, d.file_hash, d.id as doc_id,
                   c.label, c.memory_tier, c.weight, c.importance, c.namespace,
                   c.compact_content, c.relate_id, c.extra_tags
            FROM document_files d
            JOIN memory_classify c ON d.id = c.doc_id
            WHERE d.is_deleted = 0 AND d.id IN ({placeholders})
        """
        rows = self._conn.execute(query, doc_ids).fetchall()
        return [dict(r) for r in rows]

    def get_unknown_docs(self) -> list[dict]:
        rows = self._conn.execute(
            """SELECT d.id, d.file_path, d.raw_text_snippet
               FROM document_files d
               JOIN memory_classify c ON d.id = c.doc_id
               WHERE c.label = 'unknown'"""
        ).fetchall()
        return [dict(r) for r in rows]

    def update_classification(self, doc_id: int, label: str, tier: str, weight: int = 50):
        with self._write_lock:
            self._conn.execute(
                "UPDATE memory_classify SET label=?, memory_tier=?, weight=? WHERE doc_id=?",
                (label, tier, weight, doc_id),
            )
            self._conn.commit()

    def add_domain(self, name: str, namespace: str = "default", embedding: Optional[list[float]] = None):
        from ..core.domain_normalizer import DomainNormalizer
        emb_bytes = DomainNormalizer._serialize_embedding(embedding) if embedding else None
        from datetime import datetime, timezone
        with self._write_lock:
            self._conn.execute(
                """INSERT OR IGNORE INTO knowledge_domains (name, namespace, doc_count, embedding, created_at)
                   VALUES (?, ?, 0, ?, ?)""",
                (name, namespace, emb_bytes, datetime.now(timezone.utc).isoformat()),
            )
            self._conn.commit()

    def increment_domain_count(self, name: str, namespace: str = "default"):
        with self._write_lock:
            self._conn.execute(
                "UPDATE knowledge_domains SET doc_count = doc_count + 1 WHERE name=? AND namespace=?",
                (name, namespace),
            )
            self._conn.commit()

    def list_domains(self, namespace: str = "default") -> list[dict]:
        rows = self._conn.execute(
            """SELECT id, name, doc_count, created_at
               FROM knowledge_domains
               WHERE namespace = ?
               ORDER BY doc_count DESC""",
            (namespace,),
        ).fetchall()
        return [dict(r) for r in rows]

    def rename_domain(self, old_name: str, new_name: str, namespace: str = "default") -> int:
        with self._write_lock:
            self._conn.execute(
                "UPDATE memory_classify SET content_category=? WHERE content_category=? AND namespace=?",
                (new_name, old_name, namespace),
            )
            self._conn.execute(
                "UPDATE knowledge_domains SET name=? WHERE name=? AND namespace=?",
                (new_name, old_name, namespace),
            )
            self._conn.commit()
            return self._conn.execute(
                "SELECT changes()"
            ).fetchone()[0]

    def delete_domain(self, name: str, namespace: str = "default"):
        with self._write_lock:
            self._conn.execute(
                "DELETE FROM knowledge_domains WHERE name=? AND namespace=?",
                (name, namespace),
            )
            self._conn.commit()

    def get_domains_with_docs(self, namespace: str = "default") -> list[dict]:
        rows = self._conn.execute(
            """SELECT kd.name, kd.doc_count, kd.created_at,
                      COUNT(c.doc_id) as actual_count
               FROM knowledge_domains kd
               LEFT JOIN memory_classify c ON c.content_category = kd.name
                   AND c.doc_id IN (SELECT id FROM document_files WHERE is_deleted = 0)
               WHERE kd.namespace = ?
               GROUP BY kd.name
               ORDER BY actual_count DESC""",
            (namespace,),
        ).fetchall()
        return [dict(r) for r in rows]

    def record_access(self, doc_id: int, client_type: str):
        with self._write_lock:
            self._conn.execute(
                "INSERT INTO memory_access_record (doc_id, access_time, client_type) VALUES (?, ?, ?)",
                (doc_id, _utc_now(), client_type),
            )
            self._conn.execute(
                "UPDATE memory_classify SET weight = MIN(weight + 5, 100) WHERE doc_id = ?",
                (doc_id,),
            )
            self._conn.commit()

    def count_by_label(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT label, COUNT(*) as cnt FROM memory_classify GROUP BY label"
        ).fetchall()
        return {r["label"]: r["cnt"] for r in rows}

    def total_documents(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM document_files").fetchone()[0]

    def get_snippets_by_ids(self, doc_ids: list[int]) -> list[str]:
        snippets = []
        for did in doc_ids:
            row = self._conn.execute(
                "SELECT raw_text_snippet FROM document_files WHERE id=?", (did,)
            ).fetchone()
            if row:
                snippets.append(row[0])
        return snippets

    def get_file_path_and_time(self, doc_id: int):
        return self._conn.execute(
            "SELECT file_path, create_time FROM document_files WHERE id=?", (doc_id,)
        ).fetchone()

    def cleanup_expired(self, chat_log_days: int = 30, short_days: int = 7) -> int:
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        deleted = 0

        with self._write_lock:
            chat_cutoff = (now - timedelta(days=chat_log_days)).isoformat()
            rows = self._conn.execute(
                """SELECT d.id FROM document_files d
                   JOIN memory_classify c ON d.id = c.doc_id
                   WHERE c.label = 'chat_log' AND d.create_time < ?""",
                (chat_cutoff,)
            ).fetchall()
            for r in rows:
                self._conn.execute("DELETE FROM memory_classify WHERE doc_id=?", (r["id"],))
                self._conn.execute("DELETE FROM memory_access_record WHERE doc_id=?", (r["id"],))
                self._conn.execute("DELETE FROM document_files WHERE id=?", (r["id"],))
                deleted += 1

            short_cutoff = (now - timedelta(days=short_days)).isoformat()
            self._conn.execute(
                """UPDATE memory_classify SET memory_tier = 'long'
                   WHERE memory_tier = 'short' AND doc_id IN (
                       SELECT id FROM document_files WHERE create_time < ?
                   )""",
                (short_cutoff,)
            )

            self._conn.commit()

        logger.info("Expired cleanup: %d chat_logs deleted, short->long tier promoted", deleted)
        return deleted

    def count_expiring(self, chat_log_days: int = 30, short_days: int = 7) -> dict:
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        chat_cutoff = (now - timedelta(days=chat_log_days)).isoformat()
        short_cutoff = (now - timedelta(days=short_days)).isoformat()

        chat_count = self._conn.execute(
            """SELECT COUNT(*) FROM document_files d
               JOIN memory_classify c ON d.id = c.doc_id
               WHERE c.label = 'chat_log' AND d.create_time < ?""",
            (chat_cutoff,)
        ).fetchone()[0]

        short_count = self._conn.execute(
            """SELECT COUNT(*) FROM document_files d
               JOIN memory_classify c ON d.id = c.doc_id
               WHERE c.memory_tier = 'short' AND d.create_time < ?""",
            (short_cutoff,)
        ).fetchone()[0]

        return {"expiring_chat_logs": chat_count, "short_to_promote": short_count}

    def merge_memories(self, doc_ids: list[int], merged_content: str,
                       label: str = "compact_archive") -> int:
        with self._write_lock:
            import hashlib
            new_hash = hashlib.sha256(merged_content.encode()).hexdigest()
            new_path = f"_merged_{'_'.join(str(d) for d in doc_ids[:3])}.md"
            now = _utc_now()

            self._conn.execute(
                """INSERT INTO document_files
                   (file_path, file_hash, file_size, create_time, modify_time,
                    last_scan_time, origin_source, raw_text_snippet)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (new_path, new_hash, len(merged_content), now, now, now, "merge",
                 merged_content[:500]),
            )
            new_id = self._conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            self._conn.execute(
                """INSERT INTO memory_classify (doc_id, label, memory_tier, weight, classify_record)
                   VALUES (?, ?, 'archive', 90, ?)""",
                (new_id, label, json.dumps({"merged_from": doc_ids, "ts": now})),
            )

            for did in doc_ids:
                self._conn.execute("DELETE FROM memory_classify WHERE doc_id=?", (did,))
                self._conn.execute("DELETE FROM memory_access_record WHERE doc_id=?", (did,))
                self._conn.execute("DELETE FROM document_files WHERE id=?", (did,))

            self._conn.commit()
            logger.info("Merged %d docs into doc_id=%d", len(doc_ids), new_id)
            return new_id

    def decay_weights(self, decay_rate: float = 0.9, min_weight: int = 10,
                      inactive_days: int = 90) -> int:
        from datetime import datetime, timezone, timedelta
        with self._write_lock:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=inactive_days)).isoformat()
            rows = self._conn.execute(
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
                    self._conn.execute(
                        "UPDATE memory_classify SET weight=? WHERE doc_id=?",
                        (new_weight, r["doc_id"]),
                    )
                    updated += 1

            self._conn.commit()
            if updated:
                logger.info("Decayed weights for %d inactive documents", updated)
            return updated

    def find_merge_candidates(self, similarity_threshold: float = 0.85) -> list[dict]:
        rows = self._conn.execute(
            """SELECT c.doc_id, c.label, c.weight, d.file_path, d.raw_text_snippet
               FROM memory_classify c
               JOIN document_files d ON c.doc_id = d.id
               WHERE c.label != 'unknown'
               ORDER BY c.weight DESC"""
        ).fetchall()
        return [dict(r) for r in rows]

    def compress_summary(self, doc_id: int, summary: str):
        with self._write_lock:
            self._conn.execute(
                "UPDATE document_files SET raw_text_snippet=? WHERE id=?",
                (summary[:500], doc_id),
            )
            self._conn.commit()
            logger.info("Compressed summary for doc_id=%d", doc_id)

    def soft_delete(self, doc_id: int) -> bool:
        with self._write_lock:
            self._conn.execute(
                "UPDATE document_files SET is_deleted=1, update_ts=? WHERE id=?",
                (_utc_now(), doc_id),
            )
            self._conn.commit()
            logger.info("Soft deleted doc_id=%d", doc_id)
            if self._on_delete_cb:
                try:
                    self._on_delete_cb(doc_id)
                except Exception as e:
                    logger.error("Delete callback failed for doc_id=%d: %s", doc_id, e)
            return True

    def recover(self, doc_id: int) -> bool:
        with self._write_lock:
            self._conn.execute(
                "UPDATE document_files SET is_deleted=0, update_ts=? WHERE id=?",
                (_utc_now(), doc_id),
            )
            self._conn.commit()
            logger.info("Recovered doc_id=%d", doc_id)
            return True

    def cleanup_soft_deleted(self, days: int = 30) -> int:
        from datetime import datetime, timezone, timedelta
        with self._write_lock:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            rows = self._conn.execute(
                """SELECT id FROM document_files
                   WHERE is_deleted=1 AND update_ts < ?""",
                (cutoff,),
            ).fetchall()
            deleted = 0
            for r in rows:
                did = r["id"]
                self._conn.execute("DELETE FROM memory_classify WHERE doc_id=?", (did,))
                self._conn.execute("DELETE FROM memory_access_record WHERE doc_id=?", (did,))
                self._conn.execute("DELETE FROM document_files WHERE id=?", (did,))
                if self._on_delete_cb:
                    try:
                        self._on_delete_cb(did)
                    except Exception as e:
                        logger.error("Cleanup delete callback failed for doc_id=%d: %s", did, e)
                deleted += 1
            self._conn.commit()
        if deleted:
            logger.info("Cleaned up %d soft-deleted documents older than %d days", deleted, days)
        return deleted

    def upsert_processing_log(
        self, file_path: str, file_hash: str, doc_id: Optional[int] = None
    ):
        with self._write_lock:
            self._conn.execute(
                """INSERT INTO processing_log (file_path, file_hash, last_processed, doc_id)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(file_path) DO UPDATE SET
                       file_hash=excluded.file_hash,
                       last_processed=excluded.last_processed,
                       doc_id=excluded.doc_id""",
                (file_path, file_hash, _utc_now(), doc_id),
            )
            self._conn.commit()

    def get_processing_log(self, file_path: str) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM processing_log WHERE file_path=?", (file_path,)
        ).fetchone()

    def count_by_namespace(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT namespace, COUNT(*) as cnt FROM memory_classify GROUP BY namespace"
        ).fetchall()
        return {r["namespace"]: r["cnt"] for r in rows}

    def count_by_importance(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT importance, COUNT(*) as cnt FROM memory_classify GROUP BY importance"
        ).fetchall()
        return {r["importance"]: r["cnt"] for r in rows}

    def get_domains_raw(self, namespace: str = "default") -> list[dict]:
        """返回知识领域的原始数据（含 embedding），供 DomainNormalizer 使用"""
        rows = self._conn.execute(
            """SELECT id, name, doc_count, embedding
               FROM knowledge_domains
               WHERE namespace = ?
               ORDER BY doc_count DESC""",
            (namespace,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ==================== global_rules 方法 ====================

    def get_active_rules_by_category(self, category: str, limit: int = 50) -> list[dict]:
        if not category:
            return []
        rows = self._conn.execute(
            "SELECT id, rule_text, category, priority, confidence FROM global_rules "
            "WHERE status='active' AND category LIKE ? ORDER BY confidence DESC LIMIT ?",
            (f"%{category}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def add_global_rule(
        self,
        rule_text: str,
        category: str = "rule",
        sub_category: str = "behavior",
        scope: str = "global",
        priority: str = "normal",
        ttl: str = "M",
        tags: str = "[]",
        index_hint: str = "{}",
        source_file: str = "",
        source_doc_id: Optional[int] = None,
        confidence: float = 0.8,
        conflict_with: str = "[]",
        complements: str = "[]",
        max_tokens_budget: int = 50,
        rule_type: str = "knowledge",
        score_universality: int = 3,
        score_cost: int = 2,
        score_actionable: int = 3,
        score_timeliness: int = 3,
        parent_rule_id: Optional[int] = None,
        skip_gate: bool = False,
    ) -> int:
        """添加全局规则，自动计算权重+门禁检查+语义去重"""
        if not skip_gate and rule_type in ("knowledge", "standard"):
            gate = run_door_gate(score_universality, score_actionable, score_timeliness)
            if gate == "delete":
                logger.info("Door gate blocked (delete): %s", rule_text[:60])
                return -2
            if gate == "project_only":
                scope = "project"

        # ── 语义去重：关键词重叠度 > 60% 视为同一条规则 ──
        new_keywords = _extract_rule_keywords(rule_text)
        if new_keywords:
            existing_rows = self._conn.execute(
                "SELECT id, rule_text FROM global_rules WHERE status='active'"
            ).fetchall()
            for row in existing_rows:
                exist_kw = _extract_rule_keywords(row["rule_text"])
                overlap = _keyword_overlap(new_keywords, exist_kw)
                if overlap >= 0.6:
                    # 语义重复：更新已有规则的时间戳和引用来源
                    logger.info("Semantic dedup: skipped (overlap=%.0f%%), existing id=%d: %s",
                                overlap * 100, row["id"], row["rule_text"][:60])
                    self._conn.execute(
                        "UPDATE global_rules SET updated_at=?, source_doc_id=? WHERE id=?",
                        (_utc_now(), source_doc_id, row["id"]),
                    )
                    self._conn.commit()
                    return row["id"]

        priority_weight = calc_priority_weight(
            score_universality, score_cost, score_actionable, score_timeliness
        )
        now = _utc_now()
        with self._write_lock:
            try:
                self._conn.execute(
                    """INSERT INTO global_rules
                       (rule_text, category, sub_category, scope, priority, ttl, tags,
                        index_hint, source_file, source_doc_id, confidence,
                        conflict_with, complements, max_tokens_budget,
                        rule_type, score_universality, score_cost, score_actionable,
                        score_timeliness, priority_weight, parent_rule_id, gate_status,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                               ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (rule_text, category, sub_category, scope, priority, ttl, tags,
                     index_hint, source_file, source_doc_id, confidence,
                     conflict_with, complements, max_tokens_budget,
                     rule_type, score_universality, score_cost, score_actionable,
                     score_timeliness, priority_weight, parent_rule_id, "passed",
                     now, now),
                )
                self._conn.commit()
                return self._conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            except sqlite3.IntegrityError:
                self._conn.execute(
                    "UPDATE global_rules SET updated_at=? WHERE rule_text=?",
                    (now, rule_text),
                )
                self._conn.commit()
                row = self._conn.execute(
                    "SELECT id FROM global_rules WHERE rule_text=?", (rule_text,)
                ).fetchone()
                return row["id"] if row else -1

    def get_global_rules(
        self,
        scope: Optional[str] = None,
        category: Optional[str] = None,
        priority: Optional[str] = None,
        status: str = "active",
        limit: int = 100,
    ) -> list[dict]:
        """获取全局规则列表"""
        query = "SELECT * FROM global_rules WHERE status=?"
        params: list = [status]
        if scope:
            query += " AND scope=?"
            params.append(scope)
        if category:
            query += " AND category=?"
            params.append(category)
        if priority:
            query += " AND priority=?"
            params.append(priority)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_global_rules_by_type(
        self,
        rule_type: str,
        limit: int = 100,
        min_weight: int = 0,
    ) -> list[dict]:
        """按规则类型获取规则，支持最小权重过滤"""
        query = """SELECT * FROM global_rules
                   WHERE status='active' AND rule_type=?"""
        params: list = [rule_type]
        if min_weight > 0:
            query += " AND priority_weight >= ?"
            params.append(min_weight)
        query += " ORDER BY priority_weight DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_global_rules_by_scenario(
        self,
        keywords: list[str],
        limit: int = 20,
    ) -> list[dict]:
        """按场景关键词查询规则，返回评分权重排序的结果

        加载策略：
        - meta/domain 类型：权重×1.2 上浮（场景相关时优先出）
        - 精确匹配：权重≥144（金牌）无条件出
        - 模糊匹配：权重≥36 且规则类型为 knowledge/standard 才出
        """
        if not keywords:
            return []

        clauses = []
        params: list = []
        for kw in keywords:
            clauses.append("rule_text LIKE ?")
            params.append(f"%{kw}%")

        like_clause = " OR ".join(clauses) if clauses else "1=1"
        rows = self._conn.execute(
            f"""SELECT * FROM global_rules
               WHERE status='active' AND ({like_clause})
               ORDER BY priority_weight DESC, last_used DESC
               LIMIT ?""",
            (*params, limit),
        ).fetchall()

        # 后处理：按类型和权重过滤
        results = []
        for r in rows:
            r = dict(r)
            pw = r["priority_weight"] or 0
            rt = r.get("rule_type", "knowledge")
            if rt in ("meta", "domain"):
                pw = int(pw * 1.2)
            if pw >= 144:
                results.append(r)
            elif pw >= 36 and rt in ("knowledge", "standard"):
                results.append(r)
            elif rt in ("meta", "domain"):
                results.append(r)

        return sorted(results, key=lambda x: x.get("priority_weight", 0), reverse=True)[:limit]

    def get_child_rules(self, parent_rule_id: int) -> list[dict]:
        """获取父规则下的所有子规则"""
        rows = self._conn.execute(
            """SELECT * FROM global_rules
               WHERE status='active' AND parent_rule_id=?
               ORDER BY priority_weight DESC""",
            (parent_rule_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def deactivate_global_rule(self, rule_id: int) -> bool:
        """停用全局规则"""
        with self._write_lock:
            self._conn.execute(
                "UPDATE global_rules SET status='inactive', updated_at=? WHERE id=?",
                (_utc_now(), rule_id),
            )
            self._conn.commit()
            return self._conn.total_changes > 0

    def delete_global_rule(self, rule_id: int) -> bool:
        """删除全局规则"""
        with self._write_lock:
            self._conn.execute("DELETE FROM global_rules WHERE id=?", (rule_id,))
            self._conn.commit()
            return self._conn.total_changes > 0

    def search_global_rules(self, keyword: str, limit: int = 20) -> list[dict]:
        """搜索全局规则"""
        rows = self._conn.execute(
            """SELECT * FROM global_rules
               WHERE status='active' AND rule_text LIKE ?
               ORDER BY priority DESC, confidence DESC
               LIMIT ?""",
            (f"%{keyword}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def search_global_rules_with_tracking(self, keyword: str, limit: int = 3) -> list[dict]:
        """搜索全局规则 + 自动更新引用计数，给搜索入口使用"""
        if not keyword:
            return []
        rules = self.search_global_rules(keyword, limit)
        for r in rules:
            self.update_reference_count(r["id"])
        return rules

    def count_rules_by_priority(self) -> dict[str, int]:
        rows = self._conn.execute(
            """SELECT priority, COUNT(*) as cnt FROM global_rules WHERE status='active' GROUP BY priority"""
        ).fetchall()
        return {r["priority"]: r["cnt"] for r in rows}

    def update_reference_count(self, rule_id: int) -> None:
        """更新规则引用计数"""
        with self._write_lock:
            self._conn.execute(
                """UPDATE global_rules 
                   SET reference_count = reference_count + 1,
                       last_used = ?,
                       updated_at = ?
                   WHERE id = ?""",
                (_utc_now(), _utc_now(), rule_id),
            )
            self._conn.commit()

    def get_rules_by_load_strategy(self, load_strategy: str) -> list[dict]:
        """按加载策略获取规则"""
        rows = self._conn.execute(
            """SELECT * FROM global_rules 
               WHERE status='active' 
               AND json_extract(index_hint, '$.load_strategy') = ?
               ORDER BY priority DESC""",
            (load_strategy,),
        ).fetchall()
        return [dict(r) for r in rows]

    def upgrade_hot_rules(self, min_references: int = 3, min_scenarios: int = 3) -> int:
        """
        热度评估：连续3次不同场景被加载 → 提升 priority
        
        Args:
            min_references: 最小引用次数
            min_scenarios: 最小场景数（简化：用reference_count近似）
        
        Returns:
            升级的规则数量
        """
        upgraded = 0
        with self._write_lock:
            # 查找引用次数足够且当前不是critical的规则
            rows = self._conn.execute(
                """SELECT id, priority, reference_count FROM global_rules 
                   WHERE status='active' 
                   AND reference_count >= ?
                   AND priority != 'critical'""",
                (min_references,),
            ).fetchall()
            
            for row in rows:
                # 升级优先级
                new_priority = self._calc_new_priority(row["priority"])
                if new_priority != row["priority"]:
                    self._conn.execute(
                        """UPDATE global_rules 
                           SET priority = ?, updated_at = ?
                           WHERE id = ?""",
                        (new_priority, _utc_now(), row["id"]),
                    )
                    upgraded += 1
            
            self._conn.commit()
        
        if upgraded:
            logger.info("Upgraded %d hot rules", upgraded)
        return upgraded

    def _calc_new_priority(self, current: str) -> str:
        """计算新优先级"""
        priority_order = ["low", "normal", "high", "critical"]
        idx = priority_order.index(current) if current in priority_order else 1
        if idx < len(priority_order) - 1:
            return priority_order[idx + 1]
        return current

    def expire_cold_rules(self, days: int = 30) -> int:
        """
        冷ness检测：超过N天未引用 → 降低 priority 或标记过期
        
        Args:
            days: 未引用天数阈值
        
        Returns:
            处理的规则数量
        """
        from datetime import datetime, timezone, timedelta
        
        processed = 0
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        
        with self._write_lock:
            # 查找超过N天未引用的规则
            rows = self._conn.execute(
                """SELECT id, priority, last_used FROM global_rules 
                   WHERE status='active' 
                   AND (last_used IS NULL OR last_used < ?)""",
                (cutoff,),
            ).fetchall()
            
            for row in rows:
                if row["priority"] == "critical":
                    # critical规则不降级，只记录日志
                    logger.info("Cold critical rule detected: id=%d", row["id"])
                    continue
                
                # 降低优先级
                new_priority = self._downgrade_priority(row["priority"])
                if new_priority != row["priority"]:
                    self._conn.execute(
                        """UPDATE global_rules 
                           SET priority = ?, updated_at = ?
                           WHERE id = ?""",
                        (new_priority, _utc_now(), row["id"]),
                    )
                    processed += 1
                else:
                    # 已经是最低优先级，标记为inactive
                    self._conn.execute(
                        """UPDATE global_rules 
                           SET status = 'inactive', updated_at = ?
                           WHERE id = ?""",
                        (_utc_now(), row["id"]),
                    )
                    processed += 1
            
            self._conn.commit()
        
        if processed:
            logger.info("Processed %d cold rules", processed)
        return processed

    def _downgrade_priority(self, current: str) -> str:
        """降级优先级"""
        priority_order = ["critical", "high", "normal", "low"]
        idx = priority_order.index(current) if current in priority_order else 2
        if idx > 0:
            return priority_order[idx - 1]
        return current

    def get_hot_rules(self, limit: int = 10) -> list[dict]:
        """获取热门规则（引用次数最多的）"""
        rows = self._conn.execute(
            """SELECT * FROM global_rules 
               WHERE status='active' 
               ORDER BY reference_count DESC, priority DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_cold_rules(self, days: int = 30) -> list[dict]:
        """获取冷门规则（超过N天未引用）"""
        from datetime import datetime, timezone, timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        
        rows = self._conn.execute(
            """SELECT * FROM global_rules 
               WHERE status='active' 
               AND (last_used IS NULL OR last_used < ?)
               ORDER BY last_used ASC""",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]

    def set_memory_scope(self, doc_id: int, scope: str, rule_text: str = ""):
        """设置记忆的 scope 和 rule_text"""
        with self._write_lock:
            self._conn.execute(
                "UPDATE memory_classify SET scope=?, rule_text=? WHERE doc_id=?",
                (scope, rule_text, doc_id),
            )
            self._conn.commit()


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
