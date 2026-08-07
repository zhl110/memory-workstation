"""MW SDK 数据库 Schema 定义

所有 DDL 和辅助文件模板集中管理。
client.py 通过 `from .schema import SCHEMA_SQL, INDEX_TEMPLATE, ...` 引用。
"""

# ═══════════════════════════════════════════════════════════
# 完整 Schema（供 init_schema() 使用）
# ═══════════════════════════════════════════════════════════

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
    title TEXT DEFAULT '',
    memory_tier TEXT NOT NULL,
    importance TEXT DEFAULT 'P2',
    weight INTEGER DEFAULT 50,
    workspace_id TEXT DEFAULT 'default',
    memory_type TEXT DEFAULT 'session',
    create_time TEXT,
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
    evolution_tier TEXT DEFAULT 'warm',
    meta TEXT DEFAULT '{}',
    UNIQUE(doc_id)
);
CREATE INDEX IF NOT EXISTS idx_label ON memory_classify(label);
CREATE INDEX IF NOT EXISTS idx_tier ON memory_classify(memory_tier);
CREATE INDEX IF NOT EXISTS idx_workspace_id ON memory_classify(workspace_id);
CREATE INDEX IF NOT EXISTS idx_memory_type ON memory_classify(memory_type);
CREATE INDEX IF NOT EXISTS idx_importance ON memory_classify(importance);
CREATE INDEX IF NOT EXISTS idx_category ON memory_classify(content_category);

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

CREATE TABLE IF NOT EXISTS memory_cross_ref (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL,
    related_doc_id INTEGER NOT NULL,
    relation_type TEXT NOT NULL DEFAULT 'related',
    note TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (doc_id) REFERENCES memory_classify(doc_id),
    FOREIGN KEY (related_doc_id) REFERENCES memory_classify(doc_id),
    UNIQUE(doc_id, related_doc_id, relation_type)
);

CREATE TABLE IF NOT EXISTS lint_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    check_time TEXT DEFAULT (datetime('now')),
    check_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    doc_ids TEXT,
    description TEXT,
    suggestion TEXT,
    resolved INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS global_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_text TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'rule',
    sub_category TEXT NOT NULL DEFAULT 'behavior',
    scope TEXT NOT NULL DEFAULT 'global',
    priority TEXT DEFAULT 'normal',
    ttl TEXT DEFAULT 'M',
    tags TEXT DEFAULT '[]',
    reference_count INTEGER DEFAULT 0,
    last_used TEXT,
    index_hint TEXT DEFAULT '{}',
    source_file TEXT,
    source_doc_id INTEGER,
    confidence REAL DEFAULT 0.8,
    conflict_with TEXT DEFAULT '[]',
    complements TEXT DEFAULT '[]',
    max_tokens_budget INTEGER DEFAULT 50,
    status TEXT DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(rule_text)
);
CREATE INDEX IF NOT EXISTS idx_gr_scope ON global_rules(scope);

CREATE TABLE IF NOT EXISTS fts_pending_rebuild (
    doc_id INTEGER PRIMARY KEY,
    classify_label TEXT,
    failed_at TEXT DEFAULT (datetime('now')),
    retry_count INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_gr_active ON global_rules(status);
CREATE INDEX IF NOT EXISTS idx_gr_category ON global_rules(category);

-- V8: 访问记录表（进化候选查询需要）
CREATE TABLE IF NOT EXISTS memory_access_record (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL REFERENCES document_files(id),
    access_time TEXT NOT NULL,
    client_type TEXT,
    task_context TEXT
);
CREATE INDEX IF NOT EXISTS idx_acc_doc_id ON memory_access_record(doc_id);
CREATE INDEX IF NOT EXISTS idx_acc_time ON memory_access_record(access_time);


-- V8: 行为进化系统
CREATE TABLE IF NOT EXISTS evolution_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    trigger TEXT NOT NULL,
    target_doc_id INTEGER,
    detail TEXT,
    certainty REAL DEFAULT 0.0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS correction_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT NOT NULL,
    summary TEXT NOT NULL,
    context TEXT,
    occurred_at TEXT DEFAULT (datetime('now')),
    count INTEGER DEFAULT 1,
    last_occurred_at TEXT DEFAULT (datetime('now')),
    promoted INTEGER DEFAULT 0,
    suppressed_at TEXT
);

CREATE TABLE IF NOT EXISTS tier_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL,
    from_tier TEXT NOT NULL,
    to_tier TEXT NOT NULL,
    reason TEXT,
    applied_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (doc_id) REFERENCES memory_classify(doc_id)
);

-- V9: 系统元数据（上次 crawl 时间等）
CREATE TABLE IF NOT EXISTS system_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);
"""

# ── 辅助文件模板 ────────────────────────────────────────

INDEX_TEMPLATE = """# Memory Index（路由表）

> 自动维护，每次 ingest 后更新
> 最后更新：{date}

暂无记忆，等待首次 /mw-ingest。
"""

LOG_TEMPLATE = """# 操作日志

> 每次 ingest / evolve / lint 等操作自动追加
> 最后更新：{date}

暂无操作记录。
"""

LINT_TEMPLATE = """# 健康度报告

> 由 /mw-lint 生成
> 最后更新：{date}

暂无检查记录。
"""
