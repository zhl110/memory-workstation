#include "mw_core.h"
#include <algorithm>

namespace mw {

// ═══════════════════════════════════════════════════════════════
// Evolution System
// ═══════════════════════════════════════════════════════════════

std::pair<int, bool> Storage::increment_correction(const std::string& pattern, const std::string& summary, const std::string& context) {
    if (!conn_) return {0, false};

    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "SELECT id, count FROM correction_log WHERE pattern=?", -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return {0, false};

    sqlite3_bind_text(stmt, 1, pattern.c_str(), -1, SQLITE_TRANSIENT);
    int id = 0, count = 0;
    bool exists = false;
    if (sqlite3_step(stmt) == SQLITE_ROW) {
        id = sqlite3_column_int(stmt, 0);
        count = sqlite3_column_int(stmt, 1);
        exists = true;
    }
    sqlite3_finalize(stmt);

    if (exists) {
        int new_count = count + 1;
        rc = sqlite3_prepare_v2(conn_,
            "UPDATE correction_log SET count=?, last_occurred_at=datetime('now'), summary=?, context=? WHERE id=?",
            -1, &stmt, nullptr);
        if (rc == SQLITE_OK) {
            sqlite3_bind_int(stmt, 1, new_count);
            sqlite3_bind_text(stmt, 2, summary.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_text(stmt, 3, context.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_int(stmt, 4, id);
            sqlite3_step(stmt);
            sqlite3_finalize(stmt);
        }
        return {new_count, false};
    } else {
        rc = sqlite3_prepare_v2(conn_,
            "INSERT INTO correction_log (pattern, summary, context, count) VALUES (?, ?, ?, 1)",
            -1, &stmt, nullptr);
        if (rc == SQLITE_OK) {
            sqlite3_bind_text(stmt, 1, pattern.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_text(stmt, 2, summary.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_text(stmt, 3, context.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_step(stmt);
            sqlite3_finalize(stmt);
        }
        return {1, true};
    }
}

static void read_correction(sqlite3_stmt* stmt, CorrectionRecord& r) {
    r.id = sqlite3_column_int(stmt, 0);
    const char* p;
    p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1)); r.pattern = p ? p : "";
    p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 2)); r.summary = p ? p : "";
    p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 3)); r.context = p ? p : "";
    r.count = sqlite3_column_int(stmt, 4);
    r.promoted = sqlite3_column_int(stmt, 5) != 0;
    p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 6)); r.suppressed_at = p ? p : "";
    p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 7)); r.occurred_at = p ? p : "";
    p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 8)); r.last_occurred_at = p ? p : "";
}

std::vector<CorrectionRecord> Storage::get_correction_pending(int min_count) {
    std::vector<CorrectionRecord> results;
    if (!conn_) return results;

    // 24h cutoff for suppression
    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "SELECT id, pattern, summary, context, count, promoted, suppressed_at, occurred_at, last_occurred_at "
        "FROM correction_log "
        "WHERE count >= ? AND promoted = 0 "
        "AND (suppressed_at IS NULL OR suppressed_at < datetime('now', '-1 days')) "
        "ORDER BY count DESC, last_occurred_at DESC",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return results;

    sqlite3_bind_int(stmt, 1, min_count);
    while (sqlite3_step(stmt) == SQLITE_ROW) {
        CorrectionRecord r;
        read_correction(stmt, r);
        results.push_back(std::move(r));
    }
    sqlite3_finalize(stmt);
    return results;
}

bool Storage::suppress_correction(const std::string& pattern) {
    if (!conn_) return false;
    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "UPDATE correction_log SET suppressed_at=datetime('now') WHERE pattern=?",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return false;
    sqlite3_bind_text(stmt, 1, pattern.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_step(stmt);
    sqlite3_finalize(stmt);
    return true;
}

bool Storage::promote_correction(const std::string& pattern) {
    if (!conn_) return false;
    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "UPDATE correction_log SET promoted=1 WHERE pattern=?",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return false;
    sqlite3_bind_text(stmt, 1, pattern.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_step(stmt);
    sqlite3_finalize(stmt);
    return true;
}

std::vector<CorrectionRecord> Storage::list_corrections(int limit) {
    std::vector<CorrectionRecord> results;
    if (!conn_) return results;

    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "SELECT id, pattern, summary, context, count, promoted, suppressed_at, occurred_at, last_occurred_at "
        "FROM correction_log ORDER BY occurred_at DESC LIMIT ?",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return results;

    sqlite3_bind_int(stmt, 1, limit);
    while (sqlite3_step(stmt) == SQLITE_ROW) {
        CorrectionRecord r;
        read_correction(stmt, r);
        results.push_back(std::move(r));
    }
    sqlite3_finalize(stmt);
    return results;
}

int Storage::log_event(const std::string& event_type, const std::string& trigger, int target_doc_id, const std::string& detail, double certainty) {
    if (!conn_) return 0;

    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "INSERT INTO evolution_log (event_type, trigger, target_doc_id, detail, certainty) "
        "VALUES (?, ?, ?, ?, ?)",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return 0;

    sqlite3_bind_text(stmt, 1, event_type.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 2, trigger.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int(stmt, 3, target_doc_id);
    sqlite3_bind_text(stmt, 4, detail.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_double(stmt, 5, certainty);
    sqlite3_step(stmt);
    sqlite3_finalize(stmt);

    return (int)sqlite3_last_insert_rowid(conn_);
}

std::vector<EvolutionLogEntry> Storage::get_evolution_log(const std::string& event_type, int limit) {
    std::vector<EvolutionLogEntry> results;
    if (!conn_) return results;

    sqlite3_stmt* stmt = nullptr;
    int rc;
    if (!event_type.empty()) {
        rc = sqlite3_prepare_v2(conn_,
            "SELECT id, event_type, trigger, target_doc_id, detail, certainty, created_at "
            "FROM evolution_log WHERE event_type=? ORDER BY created_at DESC LIMIT ?",
            -1, &stmt, nullptr);
    } else {
        rc = sqlite3_prepare_v2(conn_,
            "SELECT id, event_type, trigger, target_doc_id, detail, certainty, created_at "
            "FROM evolution_log ORDER BY created_at DESC LIMIT ?",
            -1, &stmt, nullptr);
    }
    if (rc != SQLITE_OK) return results;

    int idx = 1;
    if (!event_type.empty()) sqlite3_bind_text(stmt, idx++, event_type.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int(stmt, idx, limit);

    while (sqlite3_step(stmt) == SQLITE_ROW) {
        EvolutionLogEntry e;
        e.id = sqlite3_column_int(stmt, 0);
        const char* p;
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1)); e.event_type = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 2)); e.trigger_name = p ? p : "";
        e.target_doc_id = sqlite3_column_int(stmt, 3);
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 4)); e.detail = p ? p : "";
        e.certainty = sqlite3_column_double(stmt, 5);
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 6)); e.created_at = p ? p : "";
        results.push_back(std::move(e));
    }
    sqlite3_finalize(stmt);
    return results;
}

bool Storage::apply_tier_change(int doc_id, const std::string& from_tier, const std::string& to_tier, const std::string& reason) {
    if (!conn_) return false;

    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "UPDATE memory_classify SET evolution_tier=? WHERE doc_id=?",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return false;
    sqlite3_bind_text(stmt, 1, to_tier.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int(stmt, 2, doc_id);
    sqlite3_step(stmt);
    sqlite3_finalize(stmt);

    rc = sqlite3_prepare_v2(conn_,
        "INSERT INTO tier_history (doc_id, from_tier, to_tier, reason) VALUES (?, ?, ?, ?)",
        -1, &stmt, nullptr);
    if (rc == SQLITE_OK) {
        sqlite3_bind_int(stmt, 1, doc_id);
        sqlite3_bind_text(stmt, 2, from_tier.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(stmt, 3, to_tier.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(stmt, 4, reason.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_step(stmt);
        sqlite3_finalize(stmt);
    }

    return true;
}

std::vector<TierHistoryEntry> Storage::get_tier_history(int doc_id, int limit) {
    std::vector<TierHistoryEntry> results;
    if (!conn_) return results;

    sqlite3_stmt* stmt = nullptr;
    int rc;
    if (doc_id > 0) {
        rc = sqlite3_prepare_v2(conn_,
            "SELECT t.id, t.doc_id, t.from_tier, t.to_tier, t.reason, t.applied_at, c.compact_content "
            "FROM tier_history t LEFT JOIN memory_classify c ON t.doc_id = c.doc_id "
            "WHERE t.doc_id = ? ORDER BY t.applied_at DESC LIMIT ?",
            -1, &stmt, nullptr);
    } else {
        rc = sqlite3_prepare_v2(conn_,
            "SELECT t.id, t.doc_id, t.from_tier, t.to_tier, t.reason, t.applied_at, c.compact_content "
            "FROM tier_history t LEFT JOIN memory_classify c ON t.doc_id = c.doc_id "
            "ORDER BY t.applied_at DESC LIMIT ?",
            -1, &stmt, nullptr);
    }
    if (rc != SQLITE_OK) return results;

    int idx = 1;
    if (doc_id > 0) sqlite3_bind_int(stmt, idx++, doc_id);
    sqlite3_bind_int(stmt, idx, limit);

    while (sqlite3_step(stmt) == SQLITE_ROW) {
        TierHistoryEntry t;
        t.id = sqlite3_column_int(stmt, 0);
        t.doc_id = sqlite3_column_int(stmt, 1);
        const char* p;
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 2)); t.from_tier = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 3)); t.to_tier = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 4)); t.reason = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 5)); t.applied_at = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 6)); t.summary = p ? p : "";
        results.push_back(std::move(t));
    }
    sqlite3_finalize(stmt);
    return results;
}

std::map<std::string, int> Storage::get_evolution_stats() {
    std::map<std::string, int> stats;
    if (!conn_) return stats;

    auto count_query = [this](const char* sql) -> int {
        sqlite3_stmt* stmt = nullptr;
        int rc = sqlite3_prepare_v2(conn_, sql, -1, &stmt, nullptr);
        if (rc != SQLITE_OK) return 0;
        int c = 0;
        if (sqlite3_step(stmt) == SQLITE_ROW) c = sqlite3_column_int(stmt, 0);
        sqlite3_finalize(stmt);
        return c;
    };

    stats["corrections_total"] = count_query("SELECT COUNT(*) FROM correction_log");
    stats["corrections_pending"] = count_query("SELECT COUNT(*) FROM correction_log WHERE promoted=0 AND count>=3");
    stats["corrections_promoted"] = count_query("SELECT COUNT(*) FROM correction_log WHERE promoted=1");
    stats["evolution_events"] = count_query("SELECT COUNT(*) FROM evolution_log");
    stats["tier_changes"] = count_query("SELECT COUNT(*) FROM tier_history");

    // Tier distribution
    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "SELECT evolution_tier, COUNT(*) FROM memory_classify GROUP BY evolution_tier",
        -1, &stmt, nullptr);
    if (rc == SQLITE_OK) {
        while (sqlite3_step(stmt) == SQLITE_ROW) {
            const char* p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 0));
            std::string tier = p ? p : "warm";
            stats["tier_" + tier] = sqlite3_column_int(stmt, 1);
        }
        sqlite3_finalize(stmt);
    }

    return stats;
}

// ═══════════════════════════════════════════════════════════════
// Always Load
// ═══════════════════════════════════════════════════════════════

// Direct string surgery: set or remove "always_load":true in a flat JSON.
// No parser needed — just find and replace the specific key-value.
static std::string meta_set_always_load(const std::string& meta, bool enabled) {
    std::string s = meta;
    if (s.empty() || s == "null") s = "{}";

    std::string needle = "\"always_load\":true";
    auto found = s.find(needle);

    if (enabled) {
        if (found != std::string::npos) return s;  // Already set
        // Insert before closing }
        auto close_pos = s.rfind('}');
        if (close_pos == std::string::npos) return s;
        bool has_other = (s.size() > 2);
        std::string entry = has_other ? ("," + needle) : needle;
        s.insert(close_pos, entry);
    } else {
        if (found == std::string::npos) return s;  // Not set
        // Erase from leading comma (if any) or from key start, through value end
        size_t erase_from = found;
        size_t erase_to = found + needle.size();

        // Consume one adjacent comma
        if (erase_from > 0 && s[erase_from - 1] == ',') {
            erase_from--;
        } else if (erase_to < s.size() && s[erase_to] == ',') {
            erase_to++;
        }
        s.erase(erase_from, erase_to - erase_from);
        if (s.empty() || s == "{") s = "{}";
    }
    return s;
}

bool Storage::set_always_load(int doc_id, bool enabled) {
    if (!conn_) return false;

    if (enabled) {
        // Check limit (5)
        sqlite3_stmt* stmt = nullptr;
        int rc = sqlite3_prepare_v2(conn_,
            "SELECT COUNT(*) FROM memory_classify WHERE meta LIKE '%\"always_load\":true%'",
            -1, &stmt, nullptr);
        if (rc == SQLITE_OK) {
            if (sqlite3_step(stmt) == SQLITE_ROW) {
                int cnt = sqlite3_column_int(stmt, 0);
                sqlite3_finalize(stmt);
                if (cnt >= 5) return false;
            } else {
                sqlite3_finalize(stmt);
            }
        }
    }

    // Get current meta
    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "SELECT meta FROM memory_classify WHERE doc_id=?", -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return false;

    sqlite3_bind_int(stmt, 1, doc_id);
    std::string meta;
    if (sqlite3_step(stmt) == SQLITE_ROW) {
        const char* p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 0));
        meta = p ? p : "";
    }
    sqlite3_finalize(stmt);

    if (meta.empty()) meta = "{}";

    // Robust JSON manipulation for "always_load" key
    if (enabled) {
        meta = meta_set_always_load(meta, true);
    } else {
        meta = meta_set_always_load(meta, false);
    }

    rc = sqlite3_prepare_v2(conn_,
        "UPDATE memory_classify SET meta=? WHERE doc_id=?",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return false;
    sqlite3_bind_text(stmt, 1, meta.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int(stmt, 2, doc_id);
    sqlite3_step(stmt);
    sqlite3_finalize(stmt);
    return true;
}

std::vector<CandidateRecord> Storage::get_always_load(int limit) {
    std::vector<CandidateRecord> results;
    if (!conn_) return results;

    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "SELECT c.doc_id, c.compact_content, c.importance, c.weight, c.content_category, c.label "
        "FROM memory_classify c JOIN document_files d ON c.doc_id = d.id "
        "WHERE d.is_deleted = 0 AND c.compact_content != '' "
        "AND c.meta LIKE '%\"always_load\":true%' "
        "ORDER BY c.weight DESC, c.importance ASC LIMIT ?",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return results;

    sqlite3_bind_int(stmt, 1, limit);
    while (sqlite3_step(stmt) == SQLITE_ROW) {
        CandidateRecord r;
        r.doc_id = sqlite3_column_int(stmt, 0);
        const char* p;
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1)); r.summary = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 2)); r.importance = p ? p : "P2";
        r.weight = sqlite3_column_int(stmt, 3);
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 4)); r.label = p ? p : "";
        results.push_back(std::move(r));
    }
    sqlite3_finalize(stmt);
    return results;
}

int Storage::clear_always_load(int doc_id) {
    if (!conn_) return 0;

    sqlite3_stmt* stmt = nullptr;
    int rc;
    if (doc_id > 0) {
        rc = sqlite3_prepare_v2(conn_,
            "SELECT doc_id, meta FROM memory_classify WHERE doc_id=?",
            -1, &stmt, nullptr);
    } else {
        rc = sqlite3_prepare_v2(conn_,
            "SELECT doc_id, meta FROM memory_classify WHERE meta LIKE '%\"always_load\":true%'",
            -1, &stmt, nullptr);
    }
    if (rc != SQLITE_OK) return 0;

    if (doc_id > 0) sqlite3_bind_int(stmt, 1, doc_id);

    struct IdMeta { int id; std::string meta; };
    std::vector<IdMeta> rows;
    while (sqlite3_step(stmt) == SQLITE_ROW) {
        IdMeta im;
        im.id = sqlite3_column_int(stmt, 0);
        const char* p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1));
        im.meta = p ? p : "";
        rows.push_back(std::move(im));
    }
    sqlite3_finalize(stmt);

    int count = 0;
    for (auto& [id, meta] : rows) {
        std::string cleaned = meta_set_always_load(meta, false);
        if (cleaned == meta) continue;  // No change

        rc = sqlite3_prepare_v2(conn_,
            "UPDATE memory_classify SET meta=? WHERE doc_id=?",
            -1, &stmt, nullptr);
        if (rc == SQLITE_OK) {
            sqlite3_bind_text(stmt, 1, cleaned.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_int(stmt, 2, id);
            sqlite3_step(stmt);
            sqlite3_finalize(stmt);
            count++;
        }
    }

    return count;
}

// ═══════════════════════════════════════════════════════════════
// Cleanup
// ═══════════════════════════════════════════════════════════════

static bool matches_test_pattern(const std::string& label) {
    // Simple pattern matching: label_x, label_y, *test*, old_*, test_*
    if (label == "label_x" || label == "label_y") return true;
    if (label.size() >= 4) {
        if (label.substr(0, 4) == "old_") return true;
        if (label.substr(0, 5) == "test_") return true;
    }
    // Case-insensitive "test" substring
    std::string lower = label;
    for (auto& c : lower) c = std::tolower(c);
    return lower.find("test") != std::string::npos;
}

std::map<std::string, int> Storage::cleanup_memories(const std::string& mode, bool hard, bool dry_run) {
    std::map<std::string, int> result;
    if (!conn_) return result;

    int test_count = 0, stale_count = 0, deleted = 0;

    if (mode == "test" || mode == "all") {
        sqlite3_stmt* stmt = nullptr;
        if (sqlite3_prepare_v2(conn_,
            "SELECT doc_id, label FROM memory_classify WHERE compact_content != ''",
            -1, &stmt, nullptr) == SQLITE_OK) {
            while (sqlite3_step(stmt) == SQLITE_ROW) {
                int doc_id = sqlite3_column_int(stmt, 0);
                const char* p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1));
                std::string label = p ? p : "";
                if (matches_test_pattern(label)) {
                    test_count++;
                    if (!dry_run) {
                        if (hard) {
                            sqlite3_stmt* del_stmt = nullptr;
                            if (sqlite3_prepare_v2(conn_, "DELETE FROM memory_classify WHERE doc_id=?", -1, &del_stmt, nullptr) == SQLITE_OK) {
                                sqlite3_bind_int(del_stmt, 1, doc_id);
                                sqlite3_step(del_stmt);
                                sqlite3_finalize(del_stmt);
                            }
                            if (sqlite3_prepare_v2(conn_, "DELETE FROM document_files WHERE id=?", -1, &del_stmt, nullptr) == SQLITE_OK) {
                                sqlite3_bind_int(del_stmt, 1, doc_id);
                                sqlite3_step(del_stmt);
                                sqlite3_finalize(del_stmt);
                            }
                        } else {
                            sqlite3_stmt* upd_stmt = nullptr;
                            if (sqlite3_prepare_v2(conn_, "UPDATE document_files SET is_deleted=1 WHERE id=?", -1, &upd_stmt, nullptr) == SQLITE_OK) {
                                sqlite3_bind_int(upd_stmt, 1, doc_id);
                                sqlite3_step(upd_stmt);
                                sqlite3_finalize(upd_stmt);
                            }
                        }
                        deleted++;
                    }
                }
            }
            sqlite3_finalize(stmt);
        }
    }

    if (mode == "stale" || mode == "all") {
        sqlite3_stmt* stmt = nullptr;
        if (sqlite3_prepare_v2(conn_,
            "SELECT doc_id FROM memory_classify "
            "WHERE compact_content != '' AND weight < 20 "
            "AND doc_id NOT IN (SELECT doc_id FROM memory_access_record WHERE access_time > datetime('now', '-180 days'))",
            -1, &stmt, nullptr) == SQLITE_OK) {
            while (sqlite3_step(stmt) == SQLITE_ROW) {
                int doc_id = sqlite3_column_int(stmt, 0);
                stale_count++;
                if (!dry_run) {
                    if (hard) {
                        sqlite3_stmt* del_stmt = nullptr;
                        if (sqlite3_prepare_v2(conn_, "DELETE FROM memory_classify WHERE doc_id=?", -1, &del_stmt, nullptr) == SQLITE_OK) {
                            sqlite3_bind_int(del_stmt, 1, doc_id);
                            sqlite3_step(del_stmt);
                            sqlite3_finalize(del_stmt);
                        }
                        if (sqlite3_prepare_v2(conn_, "DELETE FROM document_files WHERE id=?", -1, &del_stmt, nullptr) == SQLITE_OK) {
                            sqlite3_bind_int(del_stmt, 1, doc_id);
                            sqlite3_step(del_stmt);
                            sqlite3_finalize(del_stmt);
                        }
                    } else {
                        sqlite3_stmt* upd_stmt = nullptr;
                        if (sqlite3_prepare_v2(conn_, "UPDATE document_files SET is_deleted=1 WHERE id=?", -1, &upd_stmt, nullptr) == SQLITE_OK) {
                            sqlite3_bind_int(upd_stmt, 1, doc_id);
                            sqlite3_step(upd_stmt);
                            sqlite3_finalize(upd_stmt);
                        }
                    }
                    deleted++;
                }
            }
            sqlite3_finalize(stmt);
        }
    }

    result["test_count"] = test_count;
    result["stale_count"] = stale_count;
    result["deleted"] = deleted;
    return result;
}

// ═══════════════════════════════════════════════════════════════
// FTS5 Maintenance
// ═══════════════════════════════════════════════════════════════

bool Storage::rebuild_fts5() {
    if (!conn_) return false;

    // 旧实现仅执行 INSERT INTO memory_fts(memory_fts) VALUES('rebuild')，
    // 该命令只重建内部索引、不会清理已从内容表删除的孤儿行，清残留无效（bug3）。
    // 改为 DROP + re-create + 从 memory_classify 全量重灌，与 Python 侧 rebuild_fts5_index 一致。
    char* err = nullptr;
    if (sqlite3_exec(conn_, "DROP TABLE IF EXISTS memory_fts", nullptr, nullptr, &err) != SQLITE_OK) {
        if (err) sqlite3_free(err);
        return false;
    }
    ensure_fts5();

    // 从内容表重灌（过滤已软删除）
    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "SELECT c.doc_id, c.label, c.summary, c.content_category, c.sub_category, "
        "c.compact_content, COALESCE(c.keywords, '') "
        "FROM memory_classify c "
        "LEFT JOIN document_files d ON c.doc_id = d.id "
        "WHERE c.compact_content != '' AND COALESCE(d.is_deleted, 0) = 0",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return false;

    while (sqlite3_step(stmt) == SQLITE_ROW) {
        sqlite3_stmt* ins = nullptr;
        if (sqlite3_prepare_v2(conn_,
            "INSERT INTO memory_fts(doc_id, title, summary, content_category, sub_category, compact_content, keywords) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            -1, &ins, nullptr) != SQLITE_OK) {
            if (ins) sqlite3_finalize(ins);
            continue;
        }
        for (int i = 0; i < 7; i++) {
            const char* p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, i));
            sqlite3_bind_text(ins, i + 1, p ? p : "", -1, SQLITE_TRANSIENT);
        }
        sqlite3_step(ins);
        sqlite3_finalize(ins);
    }
    sqlite3_finalize(stmt);
    return true;
}

// ═══════════════════════════════════════════════════════════════
// Candidates
// ═══════════════════════════════════════════════════════════════

std::vector<CandidateRecord> Storage::get_own_candidates(int cold_days, int cold_max_weight) {
    std::vector<CandidateRecord> results;
    if (!conn_) return results;

    // Cold candidates: low weight + no recent access
    std::string sql =
        "SELECT c.doc_id, c.compact_content, c.label, c.importance, c.weight, c.evolution_tier "
        "FROM memory_classify c "
        "WHERE c.compact_content != '' "
        "AND c.weight <= ? "
        "AND c.doc_id NOT IN ("
        "  SELECT doc_id FROM memory_access_record WHERE access_time > datetime('now', '-' || ? || ' days')"
        ") ORDER BY c.weight ASC LIMIT 50";

    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_, sql.c_str(), -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return results;

    sqlite3_bind_int(stmt, 1, cold_max_weight);
    sqlite3_bind_int(stmt, 2, cold_days);
    while (sqlite3_step(stmt) == SQLITE_ROW) {
        CandidateRecord r;
        r.doc_id = sqlite3_column_int(stmt, 0);
        const char* p;
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1)); r.summary = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 2)); r.label = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 3)); r.importance = p ? p : "";
        r.weight = sqlite3_column_int(stmt, 4);
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 5)); r.evolution_tier = p ? p : "warm";
        results.push_back(std::move(r));
    }
    sqlite3_finalize(stmt);
    return results;
}

} // namespace mw
