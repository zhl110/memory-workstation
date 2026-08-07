#include "rules.h"
#include <sqlite3.h>

namespace mw {

Rules::Rules(Storage& storage) : storage_(storage) {}

std::vector<Rule> Rules::get_rules(const std::string& category, int limit) {
    std::vector<Rule> results;
    sqlite3* db = storage_.raw_conn();
    if (!db) return results;

    sqlite3_stmt* stmt = nullptr;
    std::string sql;
    if (category.empty()) {
        sql = "SELECT id, rule_text, category, sub_category, priority, confidence, "
              "conflict_with, complements "
              "FROM global_rules WHERE status='active' "
              "ORDER BY confidence DESC LIMIT ?";
    } else {
        sql = "SELECT id, rule_text, category, sub_category, priority, confidence, "
              "conflict_with, complements "
              "FROM global_rules WHERE status='active' AND category LIKE ? "
              "ORDER BY confidence DESC LIMIT ?";
    }

    int rc = sqlite3_prepare_v2(db, sql.c_str(), -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return results;

    if (category.empty()) {
        sqlite3_bind_int(stmt, 1, limit);
    } else {
        std::string pat = "%" + category + "%";
        sqlite3_bind_text(stmt, 1, pat.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_int(stmt, 2, limit);
    }

    while (sqlite3_step(stmt) == SQLITE_ROW) {
        Rule r;
        r.id = sqlite3_column_int(stmt, 0);
        const char* p;
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1)); r.rule_text = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 2)); r.category = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 3)); r.sub_category = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 4)); r.priority = p ? p : "";
        r.confidence = sqlite3_column_double(stmt, 5);
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 6)); r.conflict_with = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 7)); r.complements = p ? p : "";
        results.push_back(r);
    }
    sqlite3_finalize(stmt);
    return results;
}

std::vector<Entity> Rules::get_entities(const std::string& name, int limit) {
    std::vector<Entity> results;
    sqlite3* db = storage_.raw_conn();
    if (!db) return results;

    sqlite3_stmt* stmt = nullptr;
    std::string sql;
    if (name.empty()) {
        sql = "SELECT e.doc_id, e.entity_name, e.entity_type, e.weight "
              "FROM memory_entity e "
              "ORDER BY e.weight DESC LIMIT ?";
    } else {
        sql = "SELECT e.doc_id, e.entity_name, e.entity_type, e.weight "
              "FROM memory_entity e "
              "WHERE e.entity_name LIKE ? "
              "ORDER BY e.weight DESC LIMIT ?";
    }

    int rc = sqlite3_prepare_v2(db, sql.c_str(), -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return results;

    if (name.empty()) {
        sqlite3_bind_int(stmt, 1, limit);
    } else {
        std::string pat = "%" + name + "%";
        sqlite3_bind_text(stmt, 1, pat.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_int(stmt, 2, limit);
    }

    while (sqlite3_step(stmt) == SQLITE_ROW) {
        Entity e;
        e.doc_id = sqlite3_column_int(stmt, 0);
        const char* p;
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1)); e.entity_name = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 2)); e.entity_type = p ? p : "";
        e.weight = sqlite3_column_double(stmt, 3);
        results.push_back(e);
    }
    sqlite3_finalize(stmt);
    return results;
}

std::vector<CrossRefCandidate> Rules::find_cross_ref_candidates(int doc_id, int top_k) {
    // Delegate to Storage implementation, then convert result format
    auto storage_candidates = storage_.find_cross_ref_candidates(doc_id, top_k);

    std::vector<CrossRefCandidate> results;
    results.reserve(storage_candidates.size());

    for (const auto& item : storage_candidates) {
        CrossRefCandidate c;
        c.doc_id = 0;
        auto it = item.find("doc_id");
        if (it != item.end()) {
            try { c.doc_id = std::stoi(it->second); } catch (...) {}
        }
        it = item.find("summary");
        c.summary = it != item.end() ? it->second : "";
        it = item.find("score");
        c.score = 0.0;
        if (it != item.end()) {
            try { c.score = std::stod(it->second); } catch (...) {}
        }
        results.push_back(c);
    }

    return results;
}

int Rules::insert_cross_refs(int doc_id, const std::vector<std::map<std::string, std::string>>& refs) {
    sqlite3* db = storage_.raw_conn();
    if (!db) return 0;

    sqlite3_stmt* stmt = nullptr;
    if (sqlite3_prepare_v2(db,
        "INSERT OR IGNORE INTO memory_cross_ref "
        "(doc_id, related_doc_id, relation_type, note) "
        "VALUES (?, ?, ?, ?)",
        -1, &stmt, nullptr) != SQLITE_OK) {
        return 0;
    }

    int count = 0;
    for (const auto& ref : refs) {
        auto get = [&ref](const std::string& key, const std::string& def = "") -> std::string {
            auto it = ref.find(key);
            return it != ref.end() ? it->second : def;
        };

        int related_id = 0;
        try { related_id = std::stoi(get("related_doc_id", "0")); } catch (...) {}
        if (related_id <= 0) continue;

        sqlite3_bind_int(stmt, 1, doc_id);
        sqlite3_bind_int(stmt, 2, related_id);
        sqlite3_bind_text(stmt, 3, get("relation_type", "related").c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(stmt, 4, get("note", "").c_str(), -1, SQLITE_TRANSIENT);

        if (sqlite3_step(stmt) == SQLITE_DONE) count++;
        sqlite3_reset(stmt);
        sqlite3_clear_bindings(stmt);
    }

    sqlite3_finalize(stmt);
    // Note: Caller manages transaction boundary — no COMMIT here
    return count;
}

int Rules::auto_cross_ref(int doc_id, int top_k, bool do_scan_mentions) {
    auto candidates = find_cross_ref_candidates(doc_id, top_k);

    // Exclude self
    std::vector<CrossRefCandidate> targets;
    for (const auto& c : candidates) {
        if (c.doc_id != doc_id) targets.push_back(c);
        if ((int)targets.size() >= top_k) break;
    }

    if (targets.empty() && !do_scan_mentions) return 0;

    int count = 0;
    sqlite3* db = storage_.raw_conn();
    if (!db) return 0;

    // Prepare statement once
    sqlite3_stmt* stmt = nullptr;
    if (sqlite3_prepare_v2(db,
        "INSERT OR IGNORE INTO memory_cross_ref "
        "(doc_id, related_doc_id, relation_type, note) "
        "VALUES (?, ?, ?, ?)",
        -1, &stmt, nullptr) != SQLITE_OK) {
        return 0;
    }

    // 1. Single-direction edges from candidates（get_linked 用 UNION 双向读取）
    for (const auto& cand : targets) {
        if (doc_id == cand.doc_id) continue;  // 跳过自引用
        sqlite3_bind_int(stmt, 1, doc_id);
        sqlite3_bind_int(stmt, 2, cand.doc_id);
        sqlite3_bind_text(stmt, 3, "related", -1, SQLITE_TRANSIENT);
        std::string note = cand.summary.substr(0, std::min(cand.summary.size(), (size_t)100));
        sqlite3_bind_text(stmt, 4, note.c_str(), -1, SQLITE_TRANSIENT);

        if (sqlite3_step(stmt) == SQLITE_DONE) count++;
        sqlite3_reset(stmt);
        sqlite3_clear_bindings(stmt);
    }

    // 2. Mention scanning — find entity names mentioned in content
    if (do_scan_mentions) {
        auto mentions = storage_.scan_mentions(doc_id);
        for (const auto& ment : mentions) {
            std::string note = "正文中提到了「" + ment.entity_name + "」(" +
                               std::to_string(ment.mention_count) + "处)";
            sqlite3_bind_int(stmt, 1, doc_id);
            sqlite3_bind_int(stmt, 2, ment.related_doc_id);
            sqlite3_bind_text(stmt, 3, "mention", -1, SQLITE_TRANSIENT);
            sqlite3_bind_text(stmt, 4, note.c_str(), -1, SQLITE_TRANSIENT);

            if (sqlite3_step(stmt) == SQLITE_DONE) count++;
            sqlite3_reset(stmt);
            sqlite3_clear_bindings(stmt);
        }
    }

    sqlite3_finalize(stmt);
    // Note: Caller manages transaction boundary — no COMMIT here
    return count;
}

} // namespace mw
