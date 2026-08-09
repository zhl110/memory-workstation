#include "mw_core.h"
#include "embedding_engine.h"
#include <stdexcept>

namespace mw {

// ── Helpers ──────────────────────────────────────────────────

static void exec_raw(sqlite3* db, const std::string& sql) {
    char* err = nullptr;
    int rc = sqlite3_exec(db, sql.c_str(), nullptr, nullptr, &err);
    if (rc != SQLITE_OK) {
        std::string msg = err ? err : "unknown error";
        sqlite3_free(err);
        throw std::runtime_error("SQL error: " + msg);
    }
}

static void check_rc(int rc, sqlite3* db) {
    if (rc != SQLITE_OK && rc != SQLITE_DONE && rc != SQLITE_ROW) {
        const char* msg = sqlite3_errmsg(db);
        throw std::runtime_error(std::string("SQLite error: ") + (msg ? msg : "unknown"));
    }
}

// ── Lifecycle ────────────────────────────────────────────────

Storage::Storage(const std::string& db_path) : db_path_(db_path) {
    // 使用 NOMUTEX 避免同一线程连续调用时死锁
    // WAL 模式已启用，足以保证并发安全
    int rc = sqlite3_open_v2(db_path.c_str(), &conn_,
                              SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_NOMUTEX,
                              nullptr);
    if (rc != SQLITE_OK) {
        const char* msg = conn_ ? sqlite3_errmsg(conn_) : "cannot open";
        throw std::runtime_error("Cannot open database: " + std::string(msg));
    }
    // Enable WAL mode for better concurrency
    exec_sql("PRAGMA journal_mode=WAL");
    // WAL auto-checkpoint: 1000 pages (~4MB) 后自动 checkpoint，避免 WAL 文件无限增长
    exec_sql("PRAGMA wal_autocheckpoint=1000");
}

Storage::~Storage() {
    close();
}

void Storage::close() {
    if (conn_) {
        sqlite3_close(conn_);
        conn_ = nullptr;
    }
}

bool Storage::is_open() const {
    return conn_ != nullptr;
}

void Storage::begin_transaction() {
    if (!conn_) return;
    char* err = nullptr;
    int rc = sqlite3_exec(conn_, "BEGIN", nullptr, nullptr, &err);
    if (rc != SQLITE_OK) {
        std::string msg = err ? err : "unknown error";
        sqlite3_free(err);
        throw std::runtime_error("BEGIN failed: " + msg);
    }
}

void Storage::commit_transaction() {
    if (!conn_) return;
    char* err = nullptr;
    int rc = sqlite3_exec(conn_, "COMMIT", nullptr, nullptr, &err);
    if (rc != SQLITE_OK) {
        std::string msg = err ? err : "unknown error";
        sqlite3_free(err);
        throw std::runtime_error("COMMIT failed: " + msg);
    }
}

void Storage::rollback_transaction() {
    if (!conn_) return;
    char* err = nullptr;
    int rc = sqlite3_exec(conn_, "ROLLBACK", nullptr, nullptr, &err);
    if (rc != SQLITE_OK) {
        std::string msg = err ? err : "unknown error";
        sqlite3_free(err);
        throw std::runtime_error("ROLLBACK failed: " + msg);
    }
}

void Storage::checkpoint(int mode) {
    if (!conn_) return;
    // mode: 0=PASSIVE, 1=FULL, 2=RESTART, 3=TRUNCATE
    std::string sql = "PRAGMA wal_checkpoint(" +
        std::string(mode == 0 ? "PASSIVE" : mode == 1 ? "FULL" :
                    mode == 2 ? "RESTART" : "TRUNCATE") + ")";
    exec_sql(sql);
}

void Storage::exec_sql(const std::string& sql) {
    if (!conn_) throw std::runtime_error("Database not open");
    exec_raw(conn_, sql);
}

// ── Schema ───────────────────────────────────────────────────

void Storage::ensure_fts5() {
    if (!conn_) return;
    // Check if FTS5 table exists
    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "SELECT name FROM sqlite_master WHERE type='virtual' AND name='memory_fts'",
        -1, &stmt, nullptr);
    if (rc == SQLITE_OK) {
        rc = sqlite3_step(stmt);
        if (rc != SQLITE_ROW) {
            // FTS5 table doesn't exist, create it
            exec_sql(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5("
                "doc_id UNINDEXED, title, summary, content_category, sub_category, compact_content, keywords, "
                "tokenize='trigram')"
            );
        }
    }
    if (stmt) sqlite3_finalize(stmt);
}

void Storage::ensure_weight_column() {
    if (!conn_) return;
    // Check if weight column exists in memory_cross_ref
    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "SELECT name FROM pragma_table_info('memory_cross_ref') WHERE name='weight'",
        -1, &stmt, nullptr);
    if (rc == SQLITE_OK) {
        rc = sqlite3_step(stmt);
        if (rc != SQLITE_ROW) {
            // weight column doesn't exist, add it
            exec_sql("ALTER TABLE memory_cross_ref ADD COLUMN weight REAL DEFAULT 1.0");
        }
    }
    if (stmt) sqlite3_finalize(stmt);
}

void Storage::init_schema() {
    if (!conn_) return;

    // document_files
    exec_sql(
        "CREATE TABLE IF NOT EXISTS document_files ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "file_path TEXT NOT NULL UNIQUE,"
        "file_hash TEXT NOT NULL,"
        "file_size INTEGER DEFAULT 0,"
        "create_time TEXT NOT NULL,"
        "modify_time TEXT NOT NULL,"
        "update_ts TEXT,"
        "last_scan_time TEXT,"
        "last_classify_time TEXT,"
        "origin_source TEXT DEFAULT 'unknown',"
        "source_folder TEXT,"
        "is_alive INTEGER DEFAULT 1,"
        "is_deleted INTEGER DEFAULT 0,"
        "version INTEGER DEFAULT 1,"
        "raw_text_snippet TEXT"
        ")"
    );

    // memory_classify
    exec_sql(
        "CREATE TABLE IF NOT EXISTS memory_classify ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "doc_id INTEGER NOT NULL REFERENCES document_files(id),"
        "label TEXT NOT NULL,"
        "title TEXT DEFAULT '',"
        "memory_tier TEXT DEFAULT 'warm',"
        "weight INTEGER DEFAULT 50,"
        "importance TEXT DEFAULT 'P2',"
        "workspace_id TEXT DEFAULT 'default',"
        "memory_type TEXT DEFAULT 'session',"
        "create_time TEXT,"
        "compact_content TEXT DEFAULT '',"
        "summary TEXT DEFAULT '',"
        "content_category TEXT DEFAULT '',"
        "sub_category TEXT DEFAULT '',"
        "depth TEXT DEFAULT '概述',"
        "keywords TEXT DEFAULT '',"
        "tags TEXT DEFAULT '[]',"
        "evolution_tier TEXT DEFAULT 'warm',"
        "meta TEXT DEFAULT '{}',"
        "scope TEXT DEFAULT '',"
        "project TEXT DEFAULT '',"
        "scene TEXT DEFAULT '',"
        "emotion TEXT DEFAULT '',"
        "tier TEXT DEFAULT 'warm',"
        "tier_updated_at TEXT,"
        "valid_from TEXT,"
        "valid_until TEXT,"
        "UNIQUE(doc_id)"
        ")"
    );

    // memory_entity
    exec_sql(
        "CREATE TABLE IF NOT EXISTS memory_entity ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "doc_id INTEGER NOT NULL REFERENCES document_files(id),"
        "entity_name TEXT,"
        "entity_type TEXT,"
        "weight REAL DEFAULT 1.0,"
        "created_at TEXT,"
        "UNIQUE(doc_id, entity_name, entity_type)"
        ")"
    );

    // memory_cross_ref
    exec_sql(
        "CREATE TABLE IF NOT EXISTS memory_cross_ref ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "doc_id INTEGER NOT NULL,"
        "related_doc_id INTEGER NOT NULL,"
        "relation_type TEXT NOT NULL DEFAULT 'related',"
        "note TEXT DEFAULT '',"
        "created_at TEXT DEFAULT (datetime('now')),"
        "UNIQUE(doc_id, related_doc_id, relation_type)"
        ")"
    );

    // memory_access_record
    exec_sql(
        "CREATE TABLE IF NOT EXISTS memory_access_record ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "doc_id INTEGER REFERENCES document_files(id),"
        "access_time TEXT,"
        "client_type TEXT,"
        "task_context TEXT"
        ")"
    );

    // memory_vector
    exec_sql(
        "CREATE TABLE IF NOT EXISTS memory_vector ("
        "doc_id INTEGER PRIMARY KEY,"
        "embedding BLOB,"
        "content_hash TEXT NOT NULL,"
        "created_at TEXT DEFAULT (datetime('now'))"
        ")"
    );

    // global_rules
    exec_sql(
        "CREATE TABLE IF NOT EXISTS global_rules ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "rule_text TEXT NOT NULL,"
        "category TEXT DEFAULT '',"
        "sub_category TEXT DEFAULT '',"
        "scope TEXT NOT NULL DEFAULT 'global',"
        "priority TEXT DEFAULT 'normal',"
        "ttl TEXT DEFAULT 'M',"
        "tags TEXT DEFAULT '[]',"
        "reference_count INTEGER DEFAULT 0,"
        "last_used TEXT,"
        "index_hint TEXT DEFAULT '{}',"
        "source_file TEXT,"
        "source_doc_id INTEGER,"
        "confidence REAL DEFAULT 0.8,"
        "conflict_with TEXT DEFAULT '[]',"
        "complements TEXT DEFAULT '[]',"
        "max_tokens_budget INTEGER DEFAULT 50,"
        "status TEXT DEFAULT 'active',"
        "created_at TEXT DEFAULT (datetime('now')),"
        "updated_at TEXT DEFAULT (datetime('now')),"
        "UNIQUE(rule_text)"
        ")"
    );

    // lint_log
    exec_sql(
        "CREATE TABLE IF NOT EXISTS lint_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "check_type TEXT,"
        "doc_id INTEGER,"
        "severity TEXT,"
        "message TEXT,"
        "create_time TEXT"
        ")"
    );

    // evolution_log
    exec_sql(
        "CREATE TABLE IF NOT EXISTS evolution_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "event_type TEXT,"
        "trigger TEXT,"
        "target_doc_id INTEGER,"
        "detail TEXT DEFAULT '',"
        "certainty REAL DEFAULT 0.0,"
        "created_at TEXT DEFAULT (datetime('now'))"
        ")"
    );

    // correction_log
    exec_sql(
        "CREATE TABLE IF NOT EXISTS correction_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "pattern TEXT NOT NULL,"
        "summary TEXT DEFAULT '',"
        "context TEXT DEFAULT '',"
        "count INTEGER DEFAULT 1,"
        "promoted INTEGER DEFAULT 0,"
        "suppressed_at TEXT,"
        "occurred_at TEXT DEFAULT (datetime('now')),"
        "last_occurred_at TEXT DEFAULT (datetime('now'))"
        ")"
    );

    // tier_history
    exec_sql(
        "CREATE TABLE IF NOT EXISTS tier_history ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "doc_id INTEGER,"
        "from_tier TEXT,"
        "to_tier TEXT,"
        "reason TEXT DEFAULT '',"
        "applied_at TEXT DEFAULT (datetime('now'))"
        ")"
    );

    // system_meta
    exec_sql(
        "CREATE TABLE IF NOT EXISTS system_meta ("
        "key TEXT PRIMARY KEY,"
        "value TEXT,"
        "updated_at TEXT DEFAULT (datetime('now'))"
        ")"
    );

    // ── v0.19.0: 场景/情绪/对话状态 ──────────────────────────

    // memory_scene — 场景定义
    exec_sql(
        "CREATE TABLE IF NOT EXISTS memory_scene ("
        "scene_id TEXT PRIMARY KEY,"
        "name TEXT NOT NULL,"
        "parent_scene TEXT,"
        "description TEXT,"
        "create_time TEXT DEFAULT (datetime('now'))"
        ")"
    );

    // memory_scene_rule — 场景规则
    exec_sql(
        "CREATE TABLE IF NOT EXISTS memory_scene_rule ("
        "rule_id TEXT PRIMARY KEY,"
        "scene_id TEXT NOT NULL,"
        "rule_type TEXT NOT NULL CHECK(rule_type IN ('must', 'should', 'prefer')),"
        "rule_text TEXT NOT NULL,"
        "priority INTEGER DEFAULT 0,"
        "create_time TEXT DEFAULT (datetime('now')),"
        "FOREIGN KEY (scene_id) REFERENCES memory_scene(scene_id)"
        ")"
    );

    // memory_emotion — 情绪记录
    exec_sql(
        "CREATE TABLE IF NOT EXISTS memory_emotion ("
        "emotion_id TEXT PRIMARY KEY,"
        "doc_id INTEGER NOT NULL,"
        "emotion_type TEXT NOT NULL CHECK(emotion_type IN ('positive', 'neutral', 'negative')),"
        "emotion_detail TEXT,"
        "intensity REAL DEFAULT 0.5,"
        "create_time TEXT DEFAULT (datetime('now')),"
        "FOREIGN KEY (doc_id) REFERENCES memory_classify(doc_id)"
        ")"
    );

    // memory_session_state — 对话状态
    exec_sql(
        "CREATE TABLE IF NOT EXISTS memory_session_state ("
        "state_id TEXT PRIMARY KEY,"
        "agent_name TEXT NOT NULL,"
        "session_id TEXT,"
        "last_topic TEXT,"
        "unfinished_tasks TEXT,"
        "emotion_state TEXT,"
        "update_time TEXT DEFAULT (datetime('now'))"
        ")"
    );

    // v0.19.0: memory_classify 新增 scene / emotion 字段（幂等迁移）
    {
        sqlite3_stmt* pinfo = nullptr;
        int rc = sqlite3_prepare_v2(conn_,
            "PRAGMA table_info(memory_classify)", -1, &pinfo, nullptr);
        if (rc == SQLITE_OK) {
            std::set<std::string> cols;
            while (sqlite3_step(pinfo) == SQLITE_ROW) {
                const char* cn = reinterpret_cast<const char*>(sqlite3_column_text(pinfo, 1));
                if (cn) cols.insert(cn);
            }
            sqlite3_finalize(pinfo);
            if (cols.find("scene") == cols.end()) {
                exec_sql("ALTER TABLE memory_classify ADD COLUMN scene TEXT DEFAULT ''");
            }
            if (cols.find("emotion") == cols.end()) {
                exec_sql("ALTER TABLE memory_classify ADD COLUMN emotion TEXT DEFAULT ''");
            }
            // v0.20.0: 时序管理字段
            if (cols.find("valid_from") == cols.end()) {
                exec_sql("ALTER TABLE memory_classify ADD COLUMN valid_from TEXT");
            }
            if (cols.find("valid_until") == cols.end()) {
                exec_sql("ALTER TABLE memory_classify ADD COLUMN valid_until TEXT");
            }
            if (cols.find("invalidated_by") == cols.end()) {
                exec_sql("ALTER TABLE memory_classify ADD COLUMN invalidated_by INTEGER DEFAULT 0");
            }
            // v0.20.0: 记忆分层字段
            if (cols.find("tier") == cols.end()) {
                exec_sql("ALTER TABLE memory_classify ADD COLUMN tier TEXT DEFAULT 'warm'");
            }
            if (cols.find("tier_updated_at") == cols.end()) {
                exec_sql("ALTER TABLE memory_classify ADD COLUMN tier_updated_at TEXT");
            }
            // v0.21.0: title 字段（从 compact_content 智能提取）
            if (cols.find("title") == cols.end()) {
                exec_sql("ALTER TABLE memory_classify ADD COLUMN title TEXT DEFAULT ''");
                // 为已有记录填充 title：取 compact_content 第一句，最长 80 字
                exec_sql(
                    "UPDATE memory_classify SET title = "
                    "CASE "
                    "  WHEN compact_content = '' OR compact_content IS NULL THEN '' "
                    "  ELSE substr(compact_content, 1, 80) "
                    "END "
                    "WHERE title = '' OR title IS NULL"
                );
            }
            // 补齐 Python schema 有但 C++ CREATE TABLE 缺少的字段（幂等迁移）
            if (cols.find("relate_id") == cols.end()) {
                exec_sql("ALTER TABLE memory_classify ADD COLUMN relate_id TEXT DEFAULT ''");
            }
            if (cols.find("key_points") == cols.end()) {
                exec_sql("ALTER TABLE memory_classify ADD COLUMN key_points TEXT DEFAULT ''");
            }
            if (cols.find("extra_tags") == cols.end()) {
                exec_sql("ALTER TABLE memory_classify ADD COLUMN extra_tags TEXT DEFAULT ''");
            }
            if (cols.find("classify_record") == cols.end()) {
                exec_sql("ALTER TABLE memory_classify ADD COLUMN classify_record TEXT DEFAULT ''");
            }
            if (cols.find("ai_type") == cols.end()) {
                exec_sql("ALTER TABLE memory_classify ADD COLUMN ai_type TEXT DEFAULT ''");
            }
            if (cols.find("daily_type") == cols.end()) {
                exec_sql("ALTER TABLE memory_classify ADD COLUMN daily_type TEXT DEFAULT ''");
            }
            if (cols.find("stability") == cols.end()) {
                exec_sql("ALTER TABLE memory_classify ADD COLUMN stability TEXT DEFAULT '半静态'");
            }
            if (cols.find("confidence") == cols.end()) {
                exec_sql("ALTER TABLE memory_classify ADD COLUMN confidence TEXT DEFAULT '推测'");
            }
            if (cols.find("source") == cols.end()) {
                exec_sql("ALTER TABLE memory_classify ADD COLUMN source TEXT DEFAULT '自己'");
            }
        }
    }

    // ── v0.20.0: 记忆分层日志 ────────────────────────────────

    // memory_tier_log — 分层变更日志
    exec_sql(
        "CREATE TABLE IF NOT EXISTS memory_tier_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "doc_id INTEGER NOT NULL,"
        "from_tier TEXT,"
        "to_tier TEXT NOT NULL,"
        "reason TEXT DEFAULT '',"
        "created_at TEXT DEFAULT (datetime('now'))"
        ")"
    );

    // memory_entity_mention — 实体提及记录
    exec_sql(
        "CREATE TABLE IF NOT EXISTS memory_entity_mention ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "entity_id INTEGER NOT NULL,"
        "memory_id INTEGER NOT NULL,"
        "context TEXT,"
        "created_at TEXT DEFAULT (datetime('now'))"
        ")"
    );

    // v0.20.0: memory_entity 新增字段（幂等迁移）
    {
        sqlite3_stmt* pinfo2 = nullptr;
        int rc2 = sqlite3_prepare_v2(conn_,
            "PRAGMA table_info(memory_entity)", -1, &pinfo2, nullptr);
        if (rc2 == SQLITE_OK) {
            std::set<std::string> ecols;
            while (sqlite3_step(pinfo2) == SQLITE_ROW) {
                const char* cn = reinterpret_cast<const char*>(sqlite3_column_text(pinfo2, 1));
                if (cn) ecols.insert(cn);
            }
            sqlite3_finalize(pinfo2);
            if (ecols.find("alias") == ecols.end()) {
                exec_sql("ALTER TABLE memory_entity ADD COLUMN alias TEXT DEFAULT ''");
            }
            if (ecols.find("summary") == ecols.end()) {
                exec_sql("ALTER TABLE memory_entity ADD COLUMN summary TEXT DEFAULT ''");
            }
            if (ecols.find("embedding") == ecols.end()) {
                exec_sql("ALTER TABLE memory_entity ADD COLUMN embedding BLOB");
            }
            if (ecols.find("first_seen_at") == ecols.end()) {
                exec_sql("ALTER TABLE memory_entity ADD COLUMN first_seen_at TEXT");
            }
            if (ecols.find("last_seen_at") == ecols.end()) {
                exec_sql("ALTER TABLE memory_entity ADD COLUMN last_seen_at TEXT");
            }
            if (ecols.find("mention_count") == ecols.end()) {
                exec_sql("ALTER TABLE memory_entity ADD COLUMN mention_count INTEGER DEFAULT 1");
            }
        }
    }

    // v0.20.0: memory_classify 新增字段（幂等迁移）
    {
        sqlite3_stmt* pinfo3 = nullptr;
        int rc3 = sqlite3_prepare_v2(conn_,
            "PRAGMA table_info(memory_classify)", -1, &pinfo3, nullptr);
        if (rc3 == SQLITE_OK) {
            std::set<std::string> ccols;
            while (sqlite3_step(pinfo3) == SQLITE_ROW) {
                const char* cn = reinterpret_cast<const char*>(sqlite3_column_text(pinfo3, 1));
                if (cn) ccols.insert(cn);
            }
            sqlite3_finalize(pinfo3);
            auto add_col = [this](const std::string& name, const std::string& def) {
                std::string sql = "ALTER TABLE memory_classify ADD COLUMN " + name + " " + def;
                exec_sql(sql);
            };
            if (ccols.find("scope") == ccols.end()) add_col("scope", "TEXT DEFAULT ''");
            if (ccols.find("project") == ccols.end()) add_col("project", "TEXT DEFAULT ''");
            if (ccols.find("scene") == ccols.end()) add_col("scene", "TEXT DEFAULT ''");
            if (ccols.find("emotion") == ccols.end()) add_col("emotion", "TEXT DEFAULT ''");
            if (ccols.find("tier") == ccols.end()) add_col("tier", "TEXT DEFAULT 'warm'");
            if (ccols.find("tier_updated_at") == ccols.end()) add_col("tier_updated_at", "TEXT");
            if (ccols.find("valid_from") == ccols.end()) add_col("valid_from", "TEXT");
            if (ccols.find("valid_until") == ccols.end()) add_col("valid_until", "TEXT");
        }
    }

    // FTS5 virtual table
    ensure_fts5();

    // Indices
    exec_sql("CREATE INDEX IF NOT EXISTS idx_classify_label ON memory_classify(label)");
    exec_sql("CREATE INDEX IF NOT EXISTS idx_classify_importance ON memory_classify(importance)");
    exec_sql("CREATE INDEX IF NOT EXISTS idx_classify_weight ON memory_classify(weight)");
    exec_sql("CREATE INDEX IF NOT EXISTS idx_entity_name ON memory_entity(entity_name)");
    exec_sql("CREATE INDEX IF NOT EXISTS idx_cross_ref_doc ON memory_cross_ref(doc_id)");
    exec_sql("CREATE INDEX IF NOT EXISTS idx_access_doc ON memory_access_record(doc_id)");
    exec_sql("CREATE INDEX IF NOT EXISTS idx_access_time ON memory_access_record(access_time)");
    exec_sql("CREATE INDEX IF NOT EXISTS idx_classify_tier ON memory_classify(tier)");
    exec_sql("CREATE INDEX IF NOT EXISTS idx_entity_mention_entity ON memory_entity_mention(entity_id)");
    exec_sql("CREATE INDEX IF NOT EXISTS idx_entity_mention_memory ON memory_entity_mention(memory_id)");
}

// ── Stats ─────────────────────────────────────────────────────

int Storage::count_memories() {
    if (!conn_) return 0;
    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_, "SELECT COUNT(*) FROM memory_classify", -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return 0;
    int count = 0;
    if (sqlite3_step(stmt) == SQLITE_ROW) count = sqlite3_column_int(stmt, 0);
    sqlite3_finalize(stmt);
    return count;
}

int Storage::count_entities() {
    if (!conn_) return 0;
    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_, "SELECT COUNT(*) FROM memory_entity", -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return 0;
    int count = 0;
    if (sqlite3_step(stmt) == SQLITE_ROW) count = sqlite3_column_int(stmt, 0);
    sqlite3_finalize(stmt);
    return count;
}

int Storage::count_cross_refs() {
    if (!conn_) return 0;
    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_, "SELECT COUNT(*) FROM memory_cross_ref", -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return 0;
    int count = 0;
    if (sqlite3_step(stmt) == SQLITE_ROW) count = sqlite3_column_int(stmt, 0);
    sqlite3_finalize(stmt);
    return count;
}

// ── Vector Embedding (engine wrappers) ────────────────────────

std::vector<float> Storage::get_query_embedding(const std::string& query) {
    auto& eng = EmbeddingEngine::instance();
    if (!eng.is_loaded()) {
        return {};
    }
    return eng.embed(query);
}

bool Storage::load_embedding(const std::string& model_dir) {
    return EmbeddingEngine::instance().load(model_dir);
}

bool Storage::has_embedding() const {
    return EmbeddingEngine::instance().is_loaded();
}

// ── Health Check ─────────────────────────────────────────────

HealthCheckResult Storage::health_check() {
    HealthCheckResult r = {};
    if (!conn_) { r.error = "Database not open"; return r; }

    // DB alive
    sqlite3_stmt* stmt = nullptr;
    if (sqlite3_prepare_v2(conn_, "SELECT 1", -1, &stmt, nullptr) == SQLITE_OK) {
        if (sqlite3_step(stmt) == SQLITE_ROW) r.db_ok = true;
        sqlite3_finalize(stmt);
    }

    // FTS5 count
    if (sqlite3_prepare_v2(conn_, "SELECT COUNT(*) FROM memory_fts", -1, &stmt, nullptr) == SQLITE_OK) {
        if (sqlite3_step(stmt) == SQLITE_ROW) r.fts5_entries = sqlite3_column_int(stmt, 0);
        sqlite3_finalize(stmt);
    }

    // Classify count
    int cls_count = 0;
    if (sqlite3_prepare_v2(conn_,
        "SELECT COUNT(*) FROM memory_classify c JOIN document_files d ON c.doc_id = d.id WHERE d.is_deleted = 0",
        -1, &stmt, nullptr) == SQLITE_OK) {
        if (sqlite3_step(stmt) == SQLITE_ROW) cls_count = sqlite3_column_int(stmt, 0);
        sqlite3_finalize(stmt);
    }
    r.fts5_behind = cls_count - r.fts5_entries;

    return r;
}

} // namespace mw
