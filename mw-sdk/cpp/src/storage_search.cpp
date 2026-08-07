#include "mw_core.h"
#include <algorithm>
#include <sstream>

namespace mw {

// ── FTS5 Search ──────────────────────────────────────────────

std::vector<SearchResult> Storage::fts_search(const std::string& query, int limit,
                                               const std::string& extra_keywords) {
    std::vector<SearchResult> results;
    if (!conn_ || query.empty()) return results;

    // Sanitize FTS5 query
    std::string safe = query;
    for (char c : {'"', '\'', '-', '(', ')', ':', '^', '[', ']', '{', '}', '*', '~'}) {
        safe.erase(std::remove(safe.begin(), safe.end(), c), safe.end());
    }
    safe.erase(0, safe.find_first_not_of(" \t\n\r"));
    auto pos = safe.find_last_not_of(" \t\n\r");
    if (pos != std::string::npos) safe.erase(pos + 1);

    // Sanitize extra_keywords（同样去除 FTS5 特殊字符）
    std::string safe_extra = extra_keywords;
    for (char c : {'"', '\'', '-', '(', ')', ':', '^', '[', ']', '{', '}', '*', '~'}) {
        safe_extra.erase(std::remove(safe_extra.begin(), safe_extra.end(), c), safe_extra.end());
    }
    safe_extra.erase(0, safe_extra.find_first_not_of(" \t\n\r"));
    pos = safe_extra.find_last_not_of(" \t\n\r");
    if (pos != std::string::npos) safe_extra.erase(pos + 1);

    // 合并查询词
    std::string fts_query = safe;
    if (!safe_extra.empty()) {
        fts_query = safe + " OR " + safe_extra;
    }

    if (fts_query.empty()) return results;

    sqlite3_stmt* stmt = nullptr;
    std::string sql =
        "SELECT doc_id, bm25(memory_fts, 1.0, 5.0, 3.0, 2.0, 2.0, 4.0, 6.0) AS score "
        "FROM memory_fts WHERE memory_fts MATCH ? ORDER BY score LIMIT ?";

    int rc = sqlite3_prepare_v2(conn_, sql.c_str(), -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return results;

    sqlite3_bind_text(stmt, 1, fts_query.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int(stmt, 2, limit);

    while (sqlite3_step(stmt) == SQLITE_ROW) {
        SearchResult r;
        r.doc_id = sqlite3_column_int(stmt, 0);
        r.score = -sqlite3_column_double(stmt, 1); // bm25 returns negative
        results.push_back(r);
    }
    sqlite3_finalize(stmt);

    // 归一化 BM25 分数到 [0, 1]（与 entity/vector 分数量纲一致）
    if (!results.empty()) {
        double max_score = 0;
        for (auto& r : results) {
            if (r.score > max_score) max_score = r.score;
        }
        if (max_score > 0) {
            for (auto& r : results) {
                r.score /= max_score;
            }
        }
    }

    return results;
}

std::vector<SearchResult> Storage::like_search(const std::string& query, int limit) {
    std::vector<SearchResult> results;
    if (!conn_ || query.empty()) return results;

    // 直接用 %query% 模式，不做反斜杠转义
    // 用户搜索词几乎不会包含 % 或 _，即使包含也只影响匹配精度不会出错
    std::string pattern = "%" + query + "%";
    sqlite3_stmt* stmt = nullptr;
    // 注意：不能对 FTS5 虚拟表用 LIKE，会导致死锁
    // 改为查询 memory_classify 表
    std::string sql =
        "SELECT doc_id, 1.0 FROM memory_classify "
        "WHERE summary LIKE ? OR compact_content LIKE ? LIMIT ?";

    int rc = sqlite3_prepare_v2(conn_, sql.c_str(), -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return results;

    sqlite3_bind_text(stmt, 1, pattern.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 2, pattern.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int(stmt, 3, limit);

    while (sqlite3_step(stmt) == SQLITE_ROW) {
        SearchResult r;
        r.doc_id = sqlite3_column_int(stmt, 0);
        r.score = sqlite3_column_double(stmt, 1);
        results.push_back(r);
    }
    sqlite3_finalize(stmt);
    return results;
}

// ── Entity Search ─────────────────────────────────────────────

std::map<int, double> Storage::entity_search(const std::string& query) {
    std::map<int, double> results;
    if (!conn_ || query.empty()) return results;

    // Split query by comma/space
    std::vector<std::string> tokens;
    std::istringstream iss(query);
    std::string token;
    while (std::getline(iss, token, ',')) {
        // Trim
        token.erase(0, token.find_first_not_of(" \t"));
        token.erase(token.find_last_not_of(" \t") + 1);
        if (!token.empty()) tokens.push_back(token);
    }
    if (tokens.empty()) return results;

    // Build OR query — simple equality first, LIKE fallback only if needed
    std::string where_name, where_type;
    for (size_t i = 0; i < tokens.size(); i++) {
        if (i > 0) { where_name += " OR "; where_type += " OR "; }
        where_name += "entity_name = ?";
        where_type += "entity_type = ?";
    }

    std::string sql =
        "SELECT doc_id, SUM(weight) as total FROM memory_entity "
        "WHERE (" + where_name + ") OR (" + where_type + ") "
        "GROUP BY doc_id";

    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_, sql.c_str(), -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return results;

    // Bind exact match parameters: first all entity_name, then all entity_type
    for (size_t i = 0; i < tokens.size(); i++) {
        sqlite3_bind_text(stmt, i + 1, tokens[i].c_str(), -1, SQLITE_TRANSIENT);
    }
    for (size_t i = 0; i < tokens.size(); i++) {
        sqlite3_bind_text(stmt, tokens.size() + i + 1, tokens[i].c_str(), -1, SQLITE_TRANSIENT);
    }

    while (sqlite3_step(stmt) == SQLITE_ROW) {
        int doc_id = sqlite3_column_int(stmt, 0);
        double weight = sqlite3_column_double(stmt, 1);
        results[doc_id] = std::min(weight / 10.0, 1.0);
    }
    sqlite3_finalize(stmt);
    return results;
}

} // namespace mw
