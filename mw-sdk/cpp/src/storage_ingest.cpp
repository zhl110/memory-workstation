#include "mw_core.h"
#include "embedding_engine.h"
#include <algorithm>
#include <chrono>
#include <cstring>
#include <random>
#include <set>

namespace mw {

// applicability → weight 映射常量
constexpr int WEIGHT_GENERAL_RULE = 95;
constexpr int WEIGHT_SCENE_KNOWLEDGE = 50;
constexpr int WEIGHT_SESSION_TRACE = 20;

// UTF-8 安全字节截断：返回 s 的合法截断点（不会拦腰切断多字节字符）
// title 等字段存入 SQLite 前必须用此函数，避免中文 UTF-8 被 substr 切成非法字节
static size_t utf8_safe_truncate(const std::string& s, size_t max_bytes) {
    if (s.size() <= max_bytes) return s.size();
    size_t cut = max_bytes;
    unsigned char c = (unsigned char)s[cut - 1];
    if (c >= 0xC0) {
        // 截断点落在 lead（首）字节上：该多字节字符只保留一小半，整段丢弃
        cut -= 1;
    } else if (c >= 0x80) {
        // 截断点落在 continuation 字节上：向前找到该字符的 lead 并整个丢弃
        size_t i = cut - 1;
        while (i > 0 && (unsigned char)s[i] >= 0x80 && (unsigned char)s[i] <= 0xBF) i--;
        if ((unsigned char)s[i] >= 0xC0) cut = i;
    }
    return cut;
}

// 按 UTF-8 字符边界查找分隔符（| . \n 以及中文句号 。U+3002=E3 80 82）。
// 不能用 find_first_of("。.\n")：它对集合内任一"单字节"做匹配，
// 而中文 UTF-8 字符的中间字节可能恰好等于 E3/80/82/2E 等，会把汉字从中切坏。
// 返回分隔符首字节位置，未找到返回 npos。
static std::string::size_type utf8_find_break(const std::string& s) {
    const unsigned char CN_PERIOD[3] = {0xE3, 0x80, 0x82};
    size_t i = 0;
    while (i < s.size()) {
        unsigned char c = (unsigned char)s[i];
        if (c == '|' || c == '.' || c == '\n') return i;
        if (i + 2 < s.size() && c == CN_PERIOD[0] &&
            (unsigned char)s[i + 1] == CN_PERIOD[1] &&
            (unsigned char)s[i + 2] == CN_PERIOD[2]) return i;
        // 跳到下一个字符边界
        if (c >= 0xF0) i += 4;
        else if (c >= 0xE0) i += 3;
        else if (c >= 0xC0) i += 2;
        else i += 1;
    }
    return std::string::npos;
}

// ── JSON embedding 解析辅助 ──────────────────────────────────

static std::vector<float> parse_json_embedding(const char* json_str) {
    std::vector<float> result;
    if (!json_str || json_str[0] != '[') return result;

    const char* p = json_str + 1;  // skip '['
    while (*p && *p != ']') {
        // 跳过空白
        while (*p == ' ' || *p == ',' || *p == '\n' || *p == '\r' || *p == '\t') p++;
        if (*p == ']' || *p == '\0') break;

        char* end = nullptr;
        float val = std::strtof(p, &end);
        if (end != p) {
            result.push_back(val);
            p = end;
        } else {
            break;  // 解析失败
        }
    }
    return result;
}

// ── Memory CRUD ───────────────────────────────────────────────

std::optional<MemoryRecord> Storage::get_memory(int doc_id) {
    if (!conn_) return std::nullopt;

    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "SELECT c.doc_id, c.label, c.importance, c.weight, c.compact_content, "
        "c.content_category, c.sub_category, c.depth, c.scope, c.project "
        "FROM memory_classify c "
        "JOIN document_files d ON c.doc_id = d.id "
        "WHERE c.doc_id = ? AND d.is_deleted = 0",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return std::nullopt;

    sqlite3_bind_int(stmt, 1, doc_id);
    std::optional<MemoryRecord> result;
    if (sqlite3_step(stmt) == SQLITE_ROW) {
        MemoryRecord r;
        r.doc_id = sqlite3_column_int(stmt, 0);
        const char* p;
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1)); r.label = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 2)); r.importance = p ? p : "";
        r.weight = sqlite3_column_int(stmt, 3);
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 4)); r.summary = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 5)); r.category = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 6)); r.sub_category = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 8)); r.scope = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 9)); r.project = p ? p : "";
        result = r;
    }
    sqlite3_finalize(stmt);
    return result;
}

std::map<int, MemoryRecord> Storage::get_memories_batch(const std::vector<int>& doc_ids) {
    std::map<int, MemoryRecord> results;
    if (!conn_ || doc_ids.empty()) return results;

    // Chunk to avoid exceeding SQLite variable limit
    constexpr size_t kChunkSize = 500;

    for (size_t chunk_start = 0; chunk_start < doc_ids.size(); chunk_start += kChunkSize) {
        size_t chunk_end = std::min(chunk_start + kChunkSize, doc_ids.size());

        std::string placeholders;
        for (size_t i = chunk_start; i < chunk_end; i++) {
            if (i > chunk_start) placeholders += ",";
            placeholders += "?";
        }

        std::string sql =
            "SELECT c.doc_id, c.label, c.importance, c.weight, c.compact_content, "
            "c.content_category, c.sub_category, c.scope, c.project "
            "FROM memory_classify c "
            "JOIN document_files d ON c.doc_id = d.id "
            "WHERE c.doc_id IN (" + placeholders + ") AND d.is_deleted = 0";

        sqlite3_stmt* stmt = nullptr;
        int rc = sqlite3_prepare_v2(conn_, sql.c_str(), -1, &stmt, nullptr);
        if (rc != SQLITE_OK) continue;

        for (size_t i = chunk_start; i < chunk_end; i++) {
            sqlite3_bind_int(stmt, i - chunk_start + 1, doc_ids[i]);
        }

        while (sqlite3_step(stmt) == SQLITE_ROW) {
            MemoryRecord r;
            r.doc_id = sqlite3_column_int(stmt, 0);
            const char* p;
            p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1)); r.label = p ? p : "";
            p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 2)); r.importance = p ? p : "";
            r.weight = sqlite3_column_int(stmt, 3);
            p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 4)); r.summary = p ? p : "";
            p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 5)); r.category = p ? p : "";
            p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 6)); r.sub_category = p ? p : "";
            p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 7)); r.scope = p ? p : "global";
            p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 8)); r.project = p ? p : "";
            results[r.doc_id] = r;
        }
        sqlite3_finalize(stmt);
    }

    return results;
}

std::vector<MemoryRecord> Storage::get_memories_by_category(const std::string& category, int limit) {
    std::vector<MemoryRecord> results;
    if (!conn_) return results;

    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "SELECT c.doc_id, c.label, c.importance, c.weight, c.compact_content, "
        "c.content_category, c.sub_category "
        "FROM memory_classify c "
        "JOIN document_files d ON c.doc_id = d.id "
        "WHERE d.is_deleted = 0 AND c.content_category = ? "
        "ORDER BY c.weight DESC LIMIT ?",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return results;

    sqlite3_bind_text(stmt, 1, category.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int(stmt, 2, limit);

    while (sqlite3_step(stmt) == SQLITE_ROW) {
        MemoryRecord r;
        r.doc_id = sqlite3_column_int(stmt, 0);
        const char* p;
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1)); r.label = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 2)); r.importance = p ? p : "";
        r.weight = sqlite3_column_int(stmt, 3);
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 4)); r.summary = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 5)); r.category = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 6)); r.sub_category = p ? p : "";
        results.push_back(r);
    }
    sqlite3_finalize(stmt);
    return results;
}

int Storage::insert_memory(const std::string& content,
                           const std::map<std::string, std::string>& cls,
                           const std::string& source) {
    if (!conn_) return -1;

    // 1. Insert document_files
    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "INSERT INTO document_files (file_path, file_hash, file_size, create_time, modify_time, origin_source, raw_text_snippet) "
        "VALUES (?, ?, ?, datetime('now'), datetime('now'), ?, ?)",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return -1;

    // Generate unique path using timestamp + random
    auto now_ms = std::chrono::system_clock::now().time_since_epoch().count();
    std::random_device rd;
    std::mt19937 gen(rd());
    std::uniform_int_distribution<> dis(1000, 9999);
    std::string path = "sdk://" + source + "/" + std::to_string(now_ms) + "_" + std::to_string(dis(gen)) + ".md";
    std::string hash = std::to_string(now_ms);
    sqlite3_bind_text(stmt, 1, path.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 2, hash.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int(stmt, 3, content.size());
    sqlite3_bind_text(stmt, 4, source.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 5, content.substr(0, std::min(content.size(), CONTENT_PREVIEW_SIZE)).c_str(), -1, SQLITE_TRANSIENT);

    rc = sqlite3_step(stmt);
    sqlite3_finalize(stmt);
    if (rc != SQLITE_DONE) return -1;

    int doc_id = (int)sqlite3_last_insert_rowid(conn_);

    // 2. Insert memory_classify
    auto get = [&cls](const std::string& key, const std::string& def = "") -> std::string {
        auto it = cls.find(key);
        return it != cls.end() ? it->second : def;
    };

    const std::string& label = get("label", "unknown");
    const std::string& importance = get("importance", "P2");
    const std::string& applicability = get("applicability", "场景知识");
    int weight = (applicability == "通用规则") ? WEIGHT_GENERAL_RULE
                : (applicability == "场景知识") ? WEIGHT_SCENE_KNOWLEDGE
                : WEIGHT_SESSION_TRACE;

    // 从 compact_content 提取 title（智能提取，跳过通用标签）
    std::string title;
    // 优先：提取 **内容**：后面的文本
    auto content_pos = content.find("**内容**：");
    if (content_pos == std::string::npos) content_pos = content.find("**内容**:");
    if (content_pos != std::string::npos) {
        auto start = content_pos + 9;  // len("**内容**：") = 9 (Chinese colon)
        title = content.substr(start);
        // 截断到 | 或换行或句号
        auto sep = utf8_find_break(title);
        if (sep != std::string::npos) title = title.substr(0, sep);
        title = title.substr(0, utf8_safe_truncate(title, 50));
    } else {
        // 备选：去 ## 前缀，取第一个非标签行
        title = content;
        if (title.size() > 2 && title[0] == '#' && title[1] == '#') {
            auto start = title.find_first_not_of("# \t");
            if (start != std::string::npos) title = title.substr(start);
        }
        title = title.substr(0, utf8_safe_truncate(title, 50));
        auto period = utf8_find_break(title);
        if (period != std::string::npos) title = title.substr(0, period);
    }

    rc = sqlite3_prepare_v2(conn_,
        "INSERT INTO memory_classify "
        "(doc_id, label, title, memory_tier, weight, importance, compact_content, "
        "content_category, sub_category, depth, tags, workspace_id, memory_type, create_time, scope, project) "
        "VALUES (?, ?, ?, 'warm', ?, ?, ?, ?, ?, ?, '[]', 'default', ?, datetime('now'), ?, ?)",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return -1;

    sqlite3_bind_int(stmt, 1, doc_id);
    sqlite3_bind_text(stmt, 2, label.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 3, title.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int(stmt, 4, weight);
    sqlite3_bind_text(stmt, 5, importance.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 6, content.c_str(), -1, SQLITE_TRANSIENT);  // compact_content = 原始内容（非 summary）
    sqlite3_bind_text(stmt, 7, get("category").c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 8, get("sub_category").c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 9, get("depth").c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 10, get("memory_type", "session").c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 11, get("scope").c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 12, get("project").c_str(), -1, SQLITE_TRANSIENT);

    rc = sqlite3_step(stmt);
    sqlite3_finalize(stmt);
    if (rc != SQLITE_DONE) return -1;

    // 3. Insert FTS5
    rc = sqlite3_prepare_v2(conn_,
        "INSERT INTO memory_fts(doc_id, title, summary, content_category, sub_category, compact_content) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        -1, &stmt, nullptr);
    if (rc == SQLITE_OK) {
        const std::string& summary = get("summary");
        sqlite3_bind_int(stmt, 1, doc_id);
        sqlite3_bind_text(stmt, 2, title.c_str(), -1, SQLITE_TRANSIENT);  // 使用提取的 title
        sqlite3_bind_text(stmt, 3, summary.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(stmt, 4, get("category").c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(stmt, 5, get("sub_category").c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(stmt, 6, summary.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_step(stmt);
        sqlite3_finalize(stmt);
    }

    return doc_id;
}

bool Storage::update_memory(int doc_id, const std::string& summary,
                            const std::string& importance, int weight) {
    if (!conn_) return false;

    sqlite3_stmt* stmt = nullptr;
    std::string sql = "UPDATE memory_classify SET compact_content=?";
    std::vector<std::pair<int, std::string>> text_params;
    std::vector<std::pair<int, int>> int_params;
    int param_idx = 1;

    text_params.push_back({param_idx++, summary});

    if (!importance.empty()) {
        sql += ", importance=?";
        text_params.push_back({param_idx++, importance});
    }
    if (weight > 0) {
        sql += ", weight=?";
        int_params.push_back({param_idx++, weight});
    }
    sql += " WHERE doc_id=?";
    int_params.push_back({param_idx, doc_id});

    int rc = sqlite3_prepare_v2(conn_, sql.c_str(), -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return false;

    for (auto& [idx, val] : text_params) {
        sqlite3_bind_text(stmt, idx, val.c_str(), -1, SQLITE_TRANSIENT);
    }
    for (auto& [idx, val] : int_params) {
        sqlite3_bind_int(stmt, idx, val);
    }

    rc = sqlite3_step(stmt);
    sqlite3_finalize(stmt);

    if (rc == SQLITE_DONE) {
        // Update FTS5
        sqlite3_stmt* fts_stmt = nullptr;
        if (sqlite3_prepare_v2(conn_,
            "UPDATE memory_fts SET summary=?, title=?, compact_content=? WHERE doc_id=?",
            -1, &fts_stmt, nullptr) == SQLITE_OK) {
            std::string fts_title;
            auto fts_content_pos = summary.find("**内容**：");
            if (fts_content_pos == std::string::npos) fts_content_pos = summary.find("**内容**:");
            if (fts_content_pos != std::string::npos) {
                auto start = fts_content_pos + 9;
                fts_title = summary.substr(start);
                auto sep = utf8_find_break(fts_title);
                if (sep != std::string::npos) fts_title = fts_title.substr(0, sep);
                fts_title = fts_title.substr(0, utf8_safe_truncate(fts_title, 50));
            } else {
                fts_title = summary;
                if (fts_title.size() > 2 && fts_title[0] == '#' && fts_title[1] == '#') {
                    auto start = fts_title.find_first_not_of("# \t");
                    if (start != std::string::npos) fts_title = fts_title.substr(start);
                }
                fts_title = fts_title.substr(0, utf8_safe_truncate(fts_title, 50));
                auto period = utf8_find_break(fts_title);
                if (period != std::string::npos) fts_title = fts_title.substr(0, period);
            }
            sqlite3_bind_text(fts_stmt, 1, summary.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_text(fts_stmt, 2, fts_title.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_text(fts_stmt, 3, summary.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_int(fts_stmt, 4, doc_id);
            sqlite3_step(fts_stmt);
            sqlite3_finalize(fts_stmt);
        }
        return true;
    }
    return false;
}

// ── Entity Operations ─────────────────────────────────────────

int Storage::insert_entities(int doc_id, const std::vector<std::pair<std::string, std::string>>& entities) {
    if (!conn_ || entities.empty()) return 0;

    int count = 0;
    sqlite3_stmt* stmt = nullptr;
    std::string sql = "INSERT INTO memory_entity (doc_id, entity_name, entity_type, weight, created_at) "
        "VALUES (?, ?, ?, " + std::to_string(ENTITY_INITIAL_WEIGHT) + ", datetime('now')) "
        "ON CONFLICT(doc_id, entity_name, entity_type) "
        "DO UPDATE SET weight = weight + " + std::to_string(ENTITY_WEIGHT_INCREMENT);
    int rc = sqlite3_prepare_v2(conn_, sql.c_str(), -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return 0;

    for (const auto& [name, etype] : entities) {
        if (name.empty() || etype.empty()) continue;
        sqlite3_bind_int(stmt, 1, doc_id);
        sqlite3_bind_text(stmt, 2, name.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(stmt, 3, etype.c_str(), -1, SQLITE_TRANSIENT);
        if (sqlite3_step(stmt) == SQLITE_DONE) {
            count++;
        }
        sqlite3_reset(stmt);
    }
    sqlite3_finalize(stmt);
    return count;
}

// ── Cross Reference Operations ────────────────────────────────

int Storage::insert_cross_refs(int doc_id, const std::vector<std::map<std::string, std::string>>& refs) {
    if (!conn_ || refs.empty()) return 0;

    int count = 0;
    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "INSERT OR IGNORE INTO memory_cross_ref (doc_id, related_doc_id, relation_type, note) "
        "VALUES (?, ?, ?, ?)",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return 0;

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

        if (sqlite3_step(stmt) == SQLITE_DONE) {
            count++;
        }
        sqlite3_reset(stmt);
    }
    sqlite3_finalize(stmt);
    return count;
}

// ── Cross Reference Candidate Finding ─────────────────────────

std::vector<std::map<std::string, std::string>> Storage::find_cross_ref_candidates(int doc_id, int top_k) {
    std::vector<std::map<std::string, std::string>> results;
    if (!conn_) return results;

    std::set<int> seen;

    // 1) Get current doc's entities
    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "SELECT entity_name FROM memory_entity WHERE doc_id=?",
        -1, &stmt, nullptr);
    if (rc == SQLITE_OK) {
        sqlite3_bind_int(stmt, 1, doc_id);
        std::vector<std::string> my_entities;
        while (sqlite3_step(stmt) == SQLITE_ROW) {
            const char* p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 0));
            if (p) my_entities.push_back(p);
        }
        sqlite3_finalize(stmt);

        // Strategy A: Find docs sharing entities
        for (const auto& ent : my_entities) {
            rc = sqlite3_prepare_v2(conn_,
                "SELECT DISTINCT e.doc_id, c.compact_content, c.weight "
                "FROM memory_entity e "
                "JOIN memory_classify c ON e.doc_id = c.doc_id "
                "WHERE e.entity_name = ? AND e.doc_id != ? "
                "ORDER BY c.weight DESC LIMIT ?",
                -1, &stmt, nullptr);
            if (rc != SQLITE_OK) continue;

            sqlite3_bind_text(stmt, 1, ent.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_int(stmt, 2, doc_id);
            sqlite3_bind_int(stmt, 3, top_k);

            while (sqlite3_step(stmt) == SQLITE_ROW) {
                int id = sqlite3_column_int(stmt, 0);
                if (seen.count(id)) continue;
                seen.insert(id);

                const char* summary = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1));
                int weight = sqlite3_column_int(stmt, 2);

                std::map<std::string, std::string> item;
                item["doc_id"] = std::to_string(id);
                item["summary"] = summary ? summary : "";
                item["score"] = std::to_string(weight / 100.0);
                results.push_back(std::move(item));

                if ((int)results.size() >= top_k) break;
            }
            sqlite3_finalize(stmt);
            if ((int)results.size() >= top_k) break;
        }
    }

    // Strategy B: Same category (fill remaining)
    if ((int)results.size() < top_k) {
        rc = sqlite3_prepare_v2(conn_,
            "SELECT content_category FROM memory_classify WHERE doc_id=?",
            -1, &stmt, nullptr);
        if (rc == SQLITE_OK) {
            sqlite3_bind_int(stmt, 1, doc_id);
            std::string category;
            if (sqlite3_step(stmt) == SQLITE_ROW) {
                const char* p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 0));
                if (p) category = p;
            }
            sqlite3_finalize(stmt);

            if (!category.empty()) {
                rc = sqlite3_prepare_v2(conn_,
                    "SELECT doc_id, compact_content, weight "
                    "FROM memory_classify "
                    "WHERE content_category = ? AND doc_id != ? "
                    "ORDER BY weight DESC LIMIT ?",
                    -1, &stmt, nullptr);
                if (rc == SQLITE_OK) {
                    sqlite3_bind_text(stmt, 1, category.c_str(), -1, SQLITE_TRANSIENT);
                    sqlite3_bind_int(stmt, 2, doc_id);
                    sqlite3_bind_int(stmt, 3, top_k - (int)results.size());

                    while (sqlite3_step(stmt) == SQLITE_ROW) {
                        int id = sqlite3_column_int(stmt, 0);
                        if (seen.count(id)) continue;
                        seen.insert(id);

                        const char* summary = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1));
                        int weight = sqlite3_column_int(stmt, 2);

                        std::map<std::string, std::string> item;
                        item["doc_id"] = std::to_string(id);
                        item["summary"] = summary ? summary : "";
                        item["score"] = std::to_string(weight / 100.0);
                        results.push_back(std::move(item));

                        if ((int)results.size() >= top_k) break;
                    }
                    sqlite3_finalize(stmt);
                }
            }
        }
    }

    return results;
}

// ── Mention Scanning ──────────────────────────────────────────

std::vector<MentionHit> Storage::scan_mentions(int doc_id, int min_name_len, int top_entities) {
    std::vector<MentionHit> hits;
    if (!conn_) return hits;

    // 1. Get doc's compact_content
    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "SELECT compact_content FROM memory_classify WHERE doc_id=?",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return hits;

    sqlite3_bind_int(stmt, 1, doc_id);
    std::string content;
    if (sqlite3_step(stmt) == SQLITE_ROW) {
        const char* p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 0));
        if (p) content = p;
    }
    sqlite3_finalize(stmt);

    if (content.empty()) return hits;

    // 2. Get candidate entities from other docs (by weight desc)
    rc = sqlite3_prepare_v2(conn_,
        "SELECT doc_id, entity_name FROM memory_entity "
        "WHERE doc_id != ? ORDER BY weight DESC LIMIT ?",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return hits;

    sqlite3_bind_int(stmt, 1, doc_id);
    sqlite3_bind_int(stmt, 2, top_entities);

    struct EntInfo { int doc_id; std::string name; };
    std::vector<EntInfo> entities;
    while (sqlite3_step(stmt) == SQLITE_ROW) {
        EntInfo e;
        e.doc_id = sqlite3_column_int(stmt, 0);
        const char* p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1));
        e.name = p ? p : "";
        if ((int)e.name.size() >= min_name_len) {
            entities.push_back(std::move(e));
        }
    }
    sqlite3_finalize(stmt);

    if (entities.empty()) return hits;

    // 3. Count mentions: substring match of entity_name in content
    std::set<std::pair<int, std::string>> seen;
    for (const auto& ent : entities) {
        auto key = std::make_pair(ent.doc_id, ent.name);
        if (seen.count(key)) continue;
        seen.insert(key);

        // Count non-overlapping occurrences
        int count = 0;
        size_t pos = 0;
        while ((pos = content.find(ent.name, pos)) != std::string::npos) {
            count++;
            pos += ent.name.size();
        }

        if (count > 0) {
            hits.push_back({ent.doc_id, ent.name, count});
        }
    }

    // Sort by mention_count descending
    std::sort(hits.begin(), hits.end(),
              [](const MentionHit& a, const MentionHit& b) {
                  return a.mention_count > b.mention_count;
              });

    return hits;
}

// ── Batch Ingest ─────────────────────────────────────────────

Storage::BatchIngestResult Storage::batch_ingest(const std::string& content,
                                                   const std::map<std::string, std::string>& cls,
                                                   const std::vector<std::pair<std::string, std::string>>& entities,
                                                   const std::string& source,
                                                   bool auto_refs,
                                                   int ref_top_k) {
    BatchIngestResult result = {-1, 0, 0};
    if (!conn_) return result;

    // 单事务包裹：memory + entities + cross_refs 一次提交
    begin_transaction();

    // 1. Insert memory (document_files + memory_classify + memory_fts)
    // 内联写入，不调用 insert_memory（避免它自己 COMMIT 打破事务）
    auto now_ms = std::chrono::system_clock::now().time_since_epoch().count();
    std::random_device rd;
    std::mt19937 gen(rd());
    std::uniform_int_distribution<> dis(1000, 9999);
    std::string path = "sdk://" + source + "/" + std::to_string(now_ms) + "_" + std::to_string(dis(gen)) + ".md";
    std::string hash = std::to_string(now_ms);

    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "INSERT INTO document_files (file_path, file_hash, file_size, create_time, modify_time, origin_source, raw_text_snippet) "
        "VALUES (?, ?, ?, datetime('now'), datetime('now'), ?, ?)",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) { rollback_transaction(); return result; }

    sqlite3_bind_text(stmt, 1, path.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 2, hash.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int(stmt, 3, content.size());
    sqlite3_bind_text(stmt, 4, source.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 5, content.substr(0, std::min(content.size(), CONTENT_PREVIEW_SIZE)).c_str(), -1, SQLITE_TRANSIENT);

    rc = sqlite3_step(stmt);
    sqlite3_finalize(stmt);
    if (rc != SQLITE_DONE) { rollback_transaction(); return result; }

    result.doc_id = (int)sqlite3_last_insert_rowid(conn_);

    auto get = [&cls](const std::string& key, const std::string& def = "") -> std::string {
        auto it = cls.find(key);
        return it != cls.end() ? it->second : def;
    };

    const std::string& label = get("label", "unknown");
    const std::string& importance = get("importance", "P2");
    const std::string& applicability = get("applicability", "场景知识");
    int weight = (applicability == "通用规则") ? WEIGHT_GENERAL_RULE
                : (applicability == "场景知识") ? WEIGHT_SCENE_KNOWLEDGE
                : WEIGHT_SESSION_TRACE;

    // 从 content 提取 title（智能提取，跳过通用标签）
    std::string bat_title;
    auto bat_content_pos = content.find("**内容**：");
    if (bat_content_pos == std::string::npos) bat_content_pos = content.find("**内容**:");
    if (bat_content_pos != std::string::npos) {
        auto start = bat_content_pos + 9;
        bat_title = content.substr(start);
        auto sep = utf8_find_break(bat_title);
        if (sep != std::string::npos) bat_title = bat_title.substr(0, sep);
        bat_title = bat_title.substr(0, utf8_safe_truncate(bat_title, 50));
    } else {
        bat_title = content;
        if (bat_title.size() > 2 && bat_title[0] == '#' && bat_title[1] == '#') {
            auto start = bat_title.find_first_not_of("# \t");
            if (start != std::string::npos) bat_title = bat_title.substr(start);
        }
        bat_title = bat_title.substr(0, utf8_safe_truncate(bat_title, 50));
        auto bat_period = utf8_find_break(bat_title);
        if (bat_period != std::string::npos) bat_title = bat_title.substr(0, bat_period);
    }

    rc = sqlite3_prepare_v2(conn_,
        "INSERT INTO memory_classify "
        "(doc_id, label, title, memory_tier, weight, importance, compact_content, "
        "content_category, sub_category, depth, tags, workspace_id, memory_type, create_time, scope, project, "
        "scene, emotion, tier, valid_from, valid_until) "
        "VALUES (?, ?, ?, 'warm', ?, ?, ?, ?, ?, ?, '[]', 'default', ?, datetime('now'), ?, ?, ?, ?, ?, ?, ?)",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) { rollback_transaction(); result.doc_id = -1; return result; }

    sqlite3_bind_int(stmt, 1, result.doc_id);
    sqlite3_bind_text(stmt, 2, label.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 3, bat_title.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int(stmt, 4, weight);
    sqlite3_bind_text(stmt, 5, importance.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 6, content.c_str(), -1, SQLITE_TRANSIENT);  // compact_content = 原始内容（非 summary）
    sqlite3_bind_text(stmt, 7, get("category").c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 8, get("sub_category").c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 9, get("depth").c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 10, get("memory_type", "session").c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 11, get("scope").c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 12, get("project").c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 13, get("scene").c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 14, get("emotion").c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 15, get("tier", "warm").c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 16, get("valid_from").c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 17, get("valid_until").c_str(), -1, SQLITE_TRANSIENT);

    rc = sqlite3_step(stmt);
    sqlite3_finalize(stmt);
    if (rc != SQLITE_DONE) { rollback_transaction(); result.doc_id = -1; return result; }

    // 2. Insert FTS5
    rc = sqlite3_prepare_v2(conn_,
        "INSERT INTO memory_fts(doc_id, title, summary, content_category, sub_category, compact_content, keywords) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        -1, &stmt, nullptr);
    if (rc == SQLITE_OK) {
        const std::string& summary = get("summary");
        const std::string& keywords = get("keywords");
        sqlite3_bind_int(stmt, 1, result.doc_id);
        sqlite3_bind_text(stmt, 2, bat_title.c_str(), -1, SQLITE_TRANSIENT);  // 使用提取的 title
        sqlite3_bind_text(stmt, 3, summary.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(stmt, 4, get("category").c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(stmt, 5, get("sub_category").c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(stmt, 6, content.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(stmt, 7, keywords.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_step(stmt);
        sqlite3_finalize(stmt);
    }

    // 3. Insert entities（同一事务内）
    if (!entities.empty()) {
        std::string sql_ent = "INSERT INTO memory_entity (doc_id, entity_name, entity_type, weight, created_at) "
            "VALUES (?, ?, ?, " + std::to_string(ENTITY_INITIAL_WEIGHT) + ", datetime('now')) "
            "ON CONFLICT(doc_id, entity_name, entity_type) "
            "DO UPDATE SET weight = weight + " + std::to_string(ENTITY_WEIGHT_INCREMENT);
        rc = sqlite3_prepare_v2(conn_, sql_ent.c_str(), -1, &stmt, nullptr);
        if (rc == SQLITE_OK) {
            for (const auto& [name, etype] : entities) {
                if (name.empty() || etype.empty()) continue;
                sqlite3_bind_int(stmt, 1, result.doc_id);
                sqlite3_bind_text(stmt, 2, name.c_str(), -1, SQLITE_TRANSIENT);
                sqlite3_bind_text(stmt, 3, etype.c_str(), -1, SQLITE_TRANSIENT);
                if (sqlite3_step(stmt) == SQLITE_DONE) {
                    result.entities_inserted++;
                }
                sqlite3_reset(stmt);
            }
            sqlite3_finalize(stmt);
        }
    }

    // 4. Auto cross refs（同一事务内）
    if (auto_refs) {
        auto candidates = find_cross_ref_candidates(result.doc_id, ref_top_k);
        if (!candidates.empty()) {
            rc = sqlite3_prepare_v2(conn_,
                "INSERT OR IGNORE INTO memory_cross_ref (doc_id, related_doc_id, relation_type, note) "
                "VALUES (?, ?, ?, ?)",
                -1, &stmt, nullptr);
            if (rc == SQLITE_OK) {
                for (const auto& cand : candidates) {
                    auto it = cand.find("doc_id");
                    if (it == cand.end()) continue;
                    int other_id = 0;
                    try { other_id = std::stoi(it->second); } catch (...) {}
                    if (other_id <= 0 || other_id == result.doc_id) continue;

                    std::string note;
                    auto ns = cand.find("summary");
                    if (ns != cand.end()) {
                        note = ns->second.substr(0, std::min(ns->second.size(), (size_t)100));
                    }

                    // 单向边（get_linked 用 UNION 双向读取）
                    sqlite3_bind_int(stmt, 1, result.doc_id);
                    sqlite3_bind_int(stmt, 2, other_id);
                    sqlite3_bind_text(stmt, 3, "related", -1, SQLITE_TRANSIENT);
                    sqlite3_bind_text(stmt, 4, note.c_str(), -1, SQLITE_TRANSIENT);
                    if (sqlite3_step(stmt) == SQLITE_DONE) {
                        result.cross_refs_inserted++;
                    }
                    sqlite3_reset(stmt);
                }
                sqlite3_finalize(stmt);
            }
        }
    }

    // 单次提交
    commit_transaction();
    return result;
}

// ── Cross References ──────────────────────────────────────────

std::vector<LinkedResult> Storage::get_linked(int doc_id) {
    std::vector<LinkedResult> results;
    if (!conn_) return results;

    // 确保 weight 列存在
    ensure_weight_column();

    sqlite3_stmt* stmt = nullptr;
    // UNION 双向查询：出边 + 入边，排除自引用，按权重降序
    int rc = sqlite3_prepare_v2(conn_,
        "SELECT related_doc_id, relation_type, note, weight FROM memory_cross_ref WHERE doc_id=? AND related_doc_id!=? "
        "UNION "
        "SELECT doc_id, relation_type, note, weight FROM memory_cross_ref WHERE related_doc_id=? AND doc_id!=? "
        "ORDER BY weight DESC",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return results;

    sqlite3_bind_int(stmt, 1, doc_id);
    sqlite3_bind_int(stmt, 2, doc_id);
    sqlite3_bind_int(stmt, 3, doc_id);
    sqlite3_bind_int(stmt, 4, doc_id);
    while (sqlite3_step(stmt) == SQLITE_ROW) {
        LinkedResult r;
        r.doc_id = sqlite3_column_int(stmt, 0);
        const char* p;
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1));
        r.relation_type = p ? p : "related";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 2));
        r.note = p ? p : "";
        r.weight = sqlite3_column_double(stmt, 3);
        results.push_back(r);
    }
    sqlite3_finalize(stmt);
    return results;
}

int Storage::count_cross_refs(int doc_id) {
    if (!conn_) return 0;

    sqlite3_stmt* stmt = nullptr;
    int rc;
    if (doc_id <= 0) {
        // count all
        rc = sqlite3_prepare_v2(conn_,
            "SELECT COUNT(*) FROM memory_cross_ref",
            -1, &stmt, nullptr);
    } else {
        rc = sqlite3_prepare_v2(conn_,
            "SELECT COUNT(*) FROM memory_cross_ref WHERE doc_id=? OR related_doc_id=?",
            -1, &stmt, nullptr);
    }
    if (rc != SQLITE_OK) return 0;

    if (doc_id > 0) {
        sqlite3_bind_int(stmt, 1, doc_id);
        sqlite3_bind_int(stmt, 2, doc_id);
    }
    int count = 0;
    if (sqlite3_step(stmt) == SQLITE_ROW) {
        count = sqlite3_column_int(stmt, 0);
    }
    sqlite3_finalize(stmt);
    return count;
}

// ── Access Records ────────────────────────────────────────────

void Storage::record_access(int doc_id) {
    if (!conn_) return;

    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "INSERT INTO memory_access_record (doc_id, access_time) VALUES (?, datetime('now'))",
        -1, &stmt, nullptr);
    if (rc == SQLITE_OK) {
        sqlite3_bind_int(stmt, 1, doc_id);
        sqlite3_step(stmt);
        sqlite3_finalize(stmt);
    }

    // Auto-increase weight
    std::string sql_weight = "UPDATE memory_classify SET weight = MIN(weight + " + std::to_string(WEIGHT_ACCESS_INCREMENT) + ", " + std::to_string(WEIGHT_CAP) + ") WHERE doc_id = ?";
    rc = sqlite3_prepare_v2(conn_, sql_weight.c_str(), -1, &stmt, nullptr);
    if (rc == SQLITE_OK) {
        sqlite3_bind_int(stmt, 1, doc_id);
        sqlite3_step(stmt);
        sqlite3_finalize(stmt);
    }

    // Note: Caller manages transaction boundary — no COMMIT here
}

void Storage::record_access_batch(const std::vector<int>& doc_ids) {
    if (!conn_ || doc_ids.empty()) return;

    // 单事务批量记录，避免 N 次 COMMIT
    begin_transaction();

    sqlite3_stmt* insert_stmt = nullptr;
    sqlite3_stmt* update_stmt = nullptr;

    sqlite3_prepare_v2(conn_,
        "INSERT INTO memory_access_record (doc_id, access_time) VALUES (?, datetime('now'))",
        -1, &insert_stmt, nullptr);
    std::string sql_weight_batch = "UPDATE memory_classify SET weight = MIN(weight + " + std::to_string(WEIGHT_ACCESS_INCREMENT) + ", " + std::to_string(WEIGHT_CAP) + ") WHERE doc_id = ?";
    sqlite3_prepare_v2(conn_, sql_weight_batch.c_str(), -1, &update_stmt, nullptr);

    for (int doc_id : doc_ids) {
        if (insert_stmt) {
            sqlite3_bind_int(insert_stmt, 1, doc_id);
            sqlite3_step(insert_stmt);
            sqlite3_reset(insert_stmt);
        }
        if (update_stmt) {
            sqlite3_bind_int(update_stmt, 1, doc_id);
            sqlite3_step(update_stmt);
            sqlite3_reset(update_stmt);
        }
    }

    sqlite3_finalize(insert_stmt);
    sqlite3_finalize(update_stmt);

    commit_transaction();
}

bool Storage::has_recent_access(int doc_id, int days) {
    if (!conn_) return false;

    sqlite3_stmt* stmt = nullptr;
    std::string sql = "SELECT COUNT(*) FROM memory_access_record "
                      "WHERE doc_id=? AND access_time > datetime('now', '-' || ? || ' days')";

    int rc = sqlite3_prepare_v2(conn_, sql.c_str(), -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return false;

    sqlite3_bind_int(stmt, 1, doc_id);
    sqlite3_bind_int(stmt, 2, days);
    bool found = false;
    if (sqlite3_step(stmt) == SQLITE_ROW) {
        found = sqlite3_column_int(stmt, 0) > 0;
    }
    sqlite3_finalize(stmt);
    return found;
}

int Storage::days_since_last_access(int doc_id) {
    if (!conn_) return NO_ACCESS_SENTINEL;

    sqlite3_stmt* stmt = nullptr;
    const char* sql = "SELECT JULIANDAY('now') - JULIANDAY(MAX(access_time)) "
                      "FROM memory_access_record WHERE doc_id = ?";

    int rc = sqlite3_prepare_v2(conn_, sql, -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return NO_ACCESS_SENTINEL;

    sqlite3_bind_int(stmt, 1, doc_id);
    int days = NO_ACCESS_SENTINEL;
    if (sqlite3_step(stmt) == SQLITE_ROW) {
        if (sqlite3_column_type(stmt, 0) != SQLITE_NULL) {
            days = static_cast<int>(sqlite3_column_double(stmt, 0));
        }
    }
    sqlite3_finalize(stmt);
    return days;
}

std::map<int, int> Storage::get_access_days_batch(const std::vector<int>& doc_ids) {
    std::map<int, int> result;
    if (!conn_ || doc_ids.empty()) return result;

    // Chunk to avoid exceeding SQLITE_VARIABLE_LIMIT (999)
    constexpr size_t kChunkSize = 500;

    for (size_t chunk_start = 0; chunk_start < doc_ids.size(); chunk_start += kChunkSize) {
        size_t chunk_end = std::min(chunk_start + kChunkSize, doc_ids.size());

        std::string placeholders;
        for (size_t i = chunk_start; i < chunk_end; i++) {
            if (i > chunk_start) placeholders += ",";
            placeholders += "?";
        }

        std::string sql = "SELECT doc_id, CAST(JULIANDAY('now') - JULIANDAY(MAX(access_time)) AS INTEGER) as days "
                          "FROM memory_access_record "
                          "WHERE doc_id IN (" + placeholders + ") "
                          "GROUP BY doc_id";

        sqlite3_stmt* stmt = nullptr;
        int rc = sqlite3_prepare_v2(conn_, sql.c_str(), -1, &stmt, nullptr);
        if (rc != SQLITE_OK) continue;

        for (size_t i = chunk_start; i < chunk_end; i++) {
            sqlite3_bind_int(stmt, i - chunk_start + 1, doc_ids[i]);
        }

        while (sqlite3_step(stmt) == SQLITE_ROW) {
            int doc_id = sqlite3_column_int(stmt, 0);
            int days = sqlite3_column_int(stmt, 1);
            result[doc_id] = days;
        }
        sqlite3_finalize(stmt);
    }

    return result;
}

std::map<int, int> Storage::get_weights_batch(const std::vector<int>& doc_ids) {
    std::map<int, int> result;
    if (!conn_ || doc_ids.empty()) return result;

    constexpr size_t kChunkSize = 500;

    for (size_t chunk_start = 0; chunk_start < doc_ids.size(); chunk_start += kChunkSize) {
        size_t chunk_end = std::min(chunk_start + kChunkSize, doc_ids.size());

        std::string placeholders;
        for (size_t i = chunk_start; i < chunk_end; i++) {
            if (i > chunk_start) placeholders += ",";
            placeholders += "?";
        }

        std::string sql = "SELECT doc_id, weight FROM memory_classify "
                          "WHERE doc_id IN (" + placeholders + ")";

        sqlite3_stmt* stmt = nullptr;
        int rc = sqlite3_prepare_v2(conn_, sql.c_str(), -1, &stmt, nullptr);
        if (rc != SQLITE_OK) continue;

        for (size_t i = chunk_start; i < chunk_end; i++) {
            sqlite3_bind_int(stmt, i - chunk_start + 1, doc_ids[i]);
        }

        while (sqlite3_step(stmt) == SQLITE_ROW) {
            int doc_id = sqlite3_column_int(stmt, 0);
            int weight = sqlite3_column_int(stmt, 1);
            result[doc_id] = weight;
        }
        sqlite3_finalize(stmt);
    }

    return result;
}

std::set<int> Storage::has_recent_access_batch(const std::vector<int>& doc_ids, int days) {
    std::set<int> result;
    if (!conn_ || doc_ids.empty()) return result;

    // Chunk to avoid exceeding SQLITE_VARIABLE_LIMIT (999)
    constexpr size_t kChunkSize = 500;

    for (size_t chunk_start = 0; chunk_start < doc_ids.size(); chunk_start += kChunkSize) {
        size_t chunk_end = std::min(chunk_start + kChunkSize, doc_ids.size());

        std::string placeholders;
        for (size_t i = chunk_start; i < chunk_end; i++) {
            if (i > chunk_start) placeholders += ",";
            placeholders += "?";
        }

        std::string sql = "SELECT DISTINCT doc_id FROM memory_access_record "
                          "WHERE doc_id IN (" + placeholders + ") "
                          "AND access_time > datetime('now', '-' || ? || ' days')";

        sqlite3_stmt* stmt = nullptr;
        int rc = sqlite3_prepare_v2(conn_, sql.c_str(), -1, &stmt, nullptr);
        if (rc != SQLITE_OK) continue;

        for (size_t i = chunk_start; i < chunk_end; i++) {
            sqlite3_bind_int(stmt, i - chunk_start + 1, doc_ids[i]);
        }
        sqlite3_bind_int(stmt, chunk_end - chunk_start + 1, days);

        while (sqlite3_step(stmt) == SQLITE_ROW) {
            result.insert(sqlite3_column_int(stmt, 0));
        }
        sqlite3_finalize(stmt);
    }

    return result;
}

// ── Weight Operations ─────────────────────────────────────────

int Storage::decay_weights(double factor, int min_weight, int decay_days) {
    if (!conn_) return 0;

    std::string sql =
        "UPDATE memory_classify SET weight = MAX(CAST(weight * ? AS INTEGER), ?) "
        "WHERE doc_id IN ("
        "  SELECT c.doc_id FROM memory_classify c "
        "  LEFT JOIN memory_access_record a ON c.doc_id = a.doc_id "
        "  WHERE a.doc_id IS NULL OR a.access_time < datetime('now', '-' || ? || ' days')"
        ")";

    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_, sql.c_str(), -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return 0;

    sqlite3_bind_double(stmt, 1, factor);
    sqlite3_bind_int(stmt, 2, min_weight);
    sqlite3_bind_int(stmt, 3, decay_days);
    sqlite3_step(stmt);
    int changed = sqlite3_changes(conn_);
    sqlite3_finalize(stmt);

    return changed;
}

// ── Vector Embedding ─────────────────────────────────────────

std::vector<float> Storage::get_memory_embedding(int doc_id) {
    if (!conn_) return {};

    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "SELECT embedding FROM memory_vector WHERE doc_id = ?",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return {};

    sqlite3_bind_int(stmt, 1, doc_id);

    std::vector<float> result;
    if (sqlite3_step(stmt) == SQLITE_ROW) {
        // embedding 可能是 JSON 字符串或二进制 blob
        int type = sqlite3_column_type(stmt, 0);
        if (type == SQLITE_TEXT) {
            // JSON 字符串格式: "[-0.037, 0.072, ...]"
            const char* text = (const char*)sqlite3_column_text(stmt, 0);
            if (text) {
                result = parse_json_embedding(text);
            }
        } else {
            // 二进制 blob 格式
            const void* blob = sqlite3_column_blob(stmt, 0);
            int blob_size = sqlite3_column_bytes(stmt, 0);
            if (blob && blob_size > 0 && blob_size % sizeof(float) == 0) {
                size_t float_count = blob_size / sizeof(float);
                result.resize(float_count);
                std::memcpy(result.data(), blob, float_count * sizeof(float));
            }
        }
    }
    sqlite3_finalize(stmt);
    return result;
}

std::vector<std::pair<int, std::vector<float>>> Storage::get_all_embeddings() {
    std::vector<std::pair<int, std::vector<float>>> results;
    if (!conn_) return results;

    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "SELECT doc_id, embedding FROM memory_vector",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return results;

    while (sqlite3_step(stmt) == SQLITE_ROW) {
        int doc_id = sqlite3_column_int(stmt, 0);
        std::vector<float> embedding;

        int type = sqlite3_column_type(stmt, 1);
        if (type == SQLITE_TEXT) {
            // JSON 字符串格式
            const char* text = (const char*)sqlite3_column_text(stmt, 1);
            if (text) {
                embedding = parse_json_embedding(text);
            }
        } else {
            // 二进制 blob 格式
            const void* blob = sqlite3_column_blob(stmt, 1);
            int blob_size = sqlite3_column_bytes(stmt, 1);
            if (blob && blob_size > 0 && blob_size % sizeof(float) == 0) {
                size_t float_count = blob_size / sizeof(float);
                embedding.resize(float_count);
                std::memcpy(embedding.data(), blob, float_count * sizeof(float));
            }
        }

        if (!embedding.empty()) {
            results.push_back({doc_id, std::move(embedding)});
        }
    }
    sqlite3_finalize(stmt);
    return results;
}

// ── v0.19.0: Scene / Emotion / Session State ──────────────────

bool Storage::set_scene(const SceneRecord& scene) {
    if (!conn_) return false;
    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "INSERT OR REPLACE INTO memory_scene (scene_id, name, parent_scene, description, create_time) "
        "VALUES (?, ?, ?, ?, datetime('now'))",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return false;
    sqlite3_bind_text(stmt, 1, scene.scene_id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 2, scene.name.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 3, scene.parent_scene.empty() ? nullptr : scene.parent_scene.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 4, scene.description.empty() ? nullptr : scene.description.c_str(), -1, SQLITE_TRANSIENT);
    rc = sqlite3_step(stmt);
    sqlite3_finalize(stmt);
    return rc == SQLITE_DONE;
}

std::optional<SceneRecord> Storage::get_scene(const std::string& scene_id) {
    if (!conn_) return std::nullopt;
    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "SELECT scene_id, name, parent_scene, description, create_time "
        "FROM memory_scene WHERE scene_id = ?",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return std::nullopt;
    sqlite3_bind_text(stmt, 1, scene_id.c_str(), -1, SQLITE_TRANSIENT);
    std::optional<SceneRecord> result;
    if (sqlite3_step(stmt) == SQLITE_ROW) {
        SceneRecord r;
        const char* p;
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 0)); r.scene_id = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1)); r.name = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 2)); r.parent_scene = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 3)); r.description = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 4)); r.create_time = p ? p : "";
        result = r;
    }
    sqlite3_finalize(stmt);
    return result;
}

std::vector<SceneRecord> Storage::list_scenes() {
    std::vector<SceneRecord> results;
    if (!conn_) return results;
    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "SELECT scene_id, name, parent_scene, description, create_time "
        "FROM memory_scene ORDER BY scene_id",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return results;
    while (sqlite3_step(stmt) == SQLITE_ROW) {
        SceneRecord r;
        const char* p;
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 0)); r.scene_id = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1)); r.name = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 2)); r.parent_scene = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 3)); r.description = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 4)); r.create_time = p ? p : "";
        results.push_back(std::move(r));
    }
    sqlite3_finalize(stmt);
    return results;
}

bool Storage::set_scene_rule(const SceneRuleRecord& rule) {
    if (!conn_) return false;
    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "INSERT OR REPLACE INTO memory_scene_rule (rule_id, scene_id, rule_type, rule_text, priority, create_time) "
        "VALUES (?, ?, ?, ?, ?, datetime('now'))",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return false;
    sqlite3_bind_text(stmt, 1, rule.rule_id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 2, rule.scene_id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 3, rule.rule_type.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 4, rule.rule_text.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int(stmt, 5, rule.priority);
    rc = sqlite3_step(stmt);
    sqlite3_finalize(stmt);
    return rc == SQLITE_DONE;
}

std::vector<SceneRuleRecord> Storage::get_scene_rules(const std::string& scene_id) {
    std::vector<SceneRuleRecord> results;
    if (!conn_) return results;
    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "SELECT rule_id, scene_id, rule_type, rule_text, priority, create_time "
        "FROM memory_scene_rule WHERE scene_id = ? ORDER BY priority DESC",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return results;
    sqlite3_bind_text(stmt, 1, scene_id.c_str(), -1, SQLITE_TRANSIENT);
    while (sqlite3_step(stmt) == SQLITE_ROW) {
        SceneRuleRecord r;
        const char* p;
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 0)); r.rule_id = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1)); r.scene_id = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 2)); r.rule_type = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 3)); r.rule_text = p ? p : "";
        r.priority = sqlite3_column_int(stmt, 4);
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 5)); r.create_time = p ? p : "";
        results.push_back(std::move(r));
    }
    sqlite3_finalize(stmt);
    return results;
}

bool Storage::set_emotion(int doc_id, const std::string& emotion_type,
                          const std::string& emotion_detail, double intensity) {
    if (!conn_) return false;
    // 生成唯一 emotion_id: doc_id + timestamp
    std::string emotion_id = "emo_" + std::to_string(doc_id) + "_" +
        std::to_string(std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count());

    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "INSERT INTO memory_emotion (emotion_id, doc_id, emotion_type, emotion_detail, intensity, create_time) "
        "VALUES (?, ?, ?, ?, ?, datetime('now'))",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return false;
    sqlite3_bind_text(stmt, 1, emotion_id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int(stmt, 2, doc_id);
    sqlite3_bind_text(stmt, 3, emotion_type.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 4, emotion_detail.empty() ? nullptr : emotion_detail.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_double(stmt, 5, intensity);
    rc = sqlite3_step(stmt);
    sqlite3_finalize(stmt);
    return rc == SQLITE_DONE;
}

std::optional<EmotionRecord> Storage::get_emotion(int doc_id) {
    if (!conn_) return std::nullopt;
    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "SELECT emotion_id, doc_id, emotion_type, emotion_detail, intensity, create_time "
        "FROM memory_emotion WHERE doc_id = ? ORDER BY create_time DESC LIMIT 1",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return std::nullopt;
    sqlite3_bind_int(stmt, 1, doc_id);
    std::optional<EmotionRecord> result;
    if (sqlite3_step(stmt) == SQLITE_ROW) {
        EmotionRecord r;
        const char* p;
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 0)); r.emotion_id = p ? p : "";
        r.doc_id = sqlite3_column_int(stmt, 1);
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 2)); r.emotion_type = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 3)); r.emotion_detail = p ? p : "";
        r.intensity = sqlite3_column_double(stmt, 4);
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 5)); r.create_time = p ? p : "";
        result = r;
    }
    sqlite3_finalize(stmt);
    return result;
}

bool Storage::save_session_state(const SessionStateRecord& state) {
    if (!conn_) return false;
    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "INSERT OR REPLACE INTO memory_session_state "
        "(state_id, agent_name, session_id, last_topic, unfinished_tasks, emotion_state, update_time) "
        "VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return false;
    sqlite3_bind_text(stmt, 1, state.state_id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 2, state.agent_name.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 3, state.session_id.empty() ? nullptr : state.session_id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 4, state.last_topic.empty() ? nullptr : state.last_topic.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 5, state.unfinished_tasks.empty() ? nullptr : state.unfinished_tasks.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 6, state.emotion_state.empty() ? nullptr : state.emotion_state.c_str(), -1, SQLITE_TRANSIENT);
    rc = sqlite3_step(stmt);
    sqlite3_finalize(stmt);
    return rc == SQLITE_DONE;
}

std::optional<SessionStateRecord> Storage::get_session_state(const std::string& agent_name,
                                                              const std::string& session_id) {
    if (!conn_) return std::nullopt;
    sqlite3_stmt* stmt = nullptr;
    int rc;
    if (session_id.empty()) {
        rc = sqlite3_prepare_v2(conn_,
            "SELECT state_id, agent_name, session_id, last_topic, unfinished_tasks, emotion_state, update_time "
            "FROM memory_session_state WHERE agent_name = ? ORDER BY update_time DESC LIMIT 1",
            -1, &stmt, nullptr);
    } else {
        rc = sqlite3_prepare_v2(conn_,
            "SELECT state_id, agent_name, session_id, last_topic, unfinished_tasks, emotion_state, update_time "
            "FROM memory_session_state WHERE agent_name = ? AND session_id = ?",
            -1, &stmt, nullptr);
    }
    if (rc != SQLITE_OK) return std::nullopt;
    sqlite3_bind_text(stmt, 1, agent_name.c_str(), -1, SQLITE_TRANSIENT);
    if (!session_id.empty()) {
        sqlite3_bind_text(stmt, 2, session_id.c_str(), -1, SQLITE_TRANSIENT);
    }
    std::optional<SessionStateRecord> result;
    if (sqlite3_step(stmt) == SQLITE_ROW) {
        SessionStateRecord r;
        const char* p;
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 0)); r.state_id = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1)); r.agent_name = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 2)); r.session_id = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 3)); r.last_topic = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 4)); r.unfinished_tasks = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 5)); r.emotion_state = p ? p : "";
        p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 6)); r.update_time = p ? p : "";
        result = r;
    }
    sqlite3_finalize(stmt);
    return result;
}

// ── v0.20.0: Tier / Temporal / Entity Resolution ──────────────

bool Storage::set_tier(int doc_id, const std::string& tier, const std::string& reason) {
    if (!conn_) return false;

    // 获取旧 tier
    std::string old_tier = get_tier(doc_id);

    // 更新 tier
    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "UPDATE memory_classify SET tier = ?, tier_updated_at = datetime('now') WHERE doc_id = ?",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return false;
    sqlite3_bind_text(stmt, 1, tier.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int(stmt, 2, doc_id);
    rc = sqlite3_step(stmt);
    sqlite3_finalize(stmt);

    // 记录变更日志
    if (rc == SQLITE_DONE && old_tier != tier) {
        sqlite3_stmt* log_stmt = nullptr;
        int rc2 = sqlite3_prepare_v2(conn_,
            "INSERT INTO memory_tier_log (doc_id, from_tier, to_tier, reason, created_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            -1, &log_stmt, nullptr);
        if (rc2 == SQLITE_OK) {
            sqlite3_bind_int(log_stmt, 1, doc_id);
            sqlite3_bind_text(log_stmt, 2, old_tier.empty() ? nullptr : old_tier.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_text(log_stmt, 3, tier.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_text(log_stmt, 4, reason.empty() ? nullptr : reason.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_step(log_stmt);
            sqlite3_finalize(log_stmt);
        }
    }
    return rc == SQLITE_DONE;
}

std::string Storage::get_tier(int doc_id) {
    if (!conn_) return "warm";
    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "SELECT tier FROM memory_classify WHERE doc_id = ?",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return "warm";
    sqlite3_bind_int(stmt, 1, doc_id);
    std::string tier = "warm";
    if (sqlite3_step(stmt) == SQLITE_ROW) {
        const char* p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 0));
        if (p) tier = p;
    }
    sqlite3_finalize(stmt);
    return tier;
}

std::vector<CandidateRecord> Storage::get_hot_memories(int limit) {
    std::vector<CandidateRecord> results;
    if (!conn_) return results;
    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "SELECT c.doc_id, c.compact_content, c.label, c.importance, c.weight, c.tier "
        "FROM memory_classify c "
        "JOIN document_files d ON c.doc_id = d.id "
        "WHERE c.tier = 'hot' AND d.is_deleted = 0 AND c.compact_content != '' "
        "ORDER BY c.weight DESC LIMIT ?",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return results;
    sqlite3_bind_int(stmt, 1, limit);
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

bool Storage::archive_memory(int doc_id, const std::string& reason) {
    return set_tier(doc_id, "cold", reason);
}

bool Storage::forget_memory(int doc_id, const std::string& reason) {
    if (!conn_) return false;
    // 硬删除：清理 doc 关联的全部数据。旧实现只软删 document_files.is_deleted=1，
    // 导致 memory_classify/vector/cross_ref 残留，FTS5 仍能搜到已"删除"记忆（bug2 假删除根因）。
    struct DelSpec { const char* sql; int params; };
    const DelSpec specs[] = {
        {"DELETE FROM memory_fts WHERE doc_id = ?", 1},
        {"DELETE FROM memory_classify WHERE doc_id = ?", 1},
        {"DELETE FROM memory_vector WHERE doc_id = ?", 1},
        {"DELETE FROM memory_access_record WHERE doc_id = ?", 1},
        {"DELETE FROM memory_cross_ref WHERE doc_id = ? OR related_doc_id = ?", 2},
        {"DELETE FROM document_files WHERE id = ?", 1},
    };

    bool ok = true;  // 记录是否存在
    bool any = false;
    for (const auto& spec : specs) {
        sqlite3_stmt* st = nullptr;
        if (sqlite3_prepare_v2(conn_, spec.sql, -1, &st, nullptr) != SQLITE_OK) {
            if (st) sqlite3_finalize(st);
            continue;
        }
        sqlite3_bind_int(st, 1, doc_id);
        if (spec.params == 2) sqlite3_bind_int(st, 2, doc_id);
        int rc = sqlite3_step(st);
        sqlite3_finalize(st);
        if (rc == SQLITE_DONE) any = true;
        else ok = false;
    }

    // 记录日志
    if (any) log_event("forget", reason, doc_id, "", 1.0);
    return ok && any;
}

bool Storage::set_valid_time(int doc_id, const std::string& valid_from,
                              const std::string& valid_until) {
    if (!conn_) return false;
    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "UPDATE memory_classify SET valid_from = ?, valid_until = ? WHERE doc_id = ?",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return false;
    sqlite3_bind_text(stmt, 1, valid_from.empty() ? nullptr : valid_from.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 2, valid_until.empty() ? nullptr : valid_until.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int(stmt, 3, doc_id);
    rc = sqlite3_step(stmt);
    sqlite3_finalize(stmt);
    return rc == SQLITE_DONE;
}

std::vector<MemoryRecord> Storage::get_current_valid(const std::string& entity_name) {
    std::vector<MemoryRecord> results;
    if (!conn_) return results;

    // 先找实体关联的 doc_id
    sqlite3_stmt* estmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "SELECT doc_id FROM memory_entity WHERE entity_name = ?",
        -1, &estmt, nullptr);
    if (rc != SQLITE_OK) return results;
    sqlite3_bind_text(estmt, 1, entity_name.c_str(), -1, SQLITE_TRANSIENT);

    std::vector<int> doc_ids;
    while (sqlite3_step(estmt) == SQLITE_ROW) {
        doc_ids.push_back(sqlite3_column_int(estmt, 0));
    }
    sqlite3_finalize(estmt);

    // 过滤当前有效的（valid_until IS NULL 或 valid_until > now）
    for (int did : doc_ids) {
        sqlite3_stmt* stmt = nullptr;
        rc = sqlite3_prepare_v2(conn_,
            "SELECT c.doc_id, c.label, c.importance, c.weight, c.compact_content, "
            "c.content_category, c.sub_category, c.scope, c.project "
            "FROM memory_classify c "
            "JOIN document_files d ON c.doc_id = d.id "
            "WHERE c.doc_id = ? AND d.is_deleted = 0 "
            "AND (c.valid_until IS NULL OR c.valid_until > datetime('now'))",
            -1, &stmt, nullptr);
        if (rc != SQLITE_OK) continue;
        sqlite3_bind_int(stmt, 1, did);
        if (sqlite3_step(stmt) == SQLITE_ROW) {
            MemoryRecord r;
            r.doc_id = sqlite3_column_int(stmt, 0);
            const char* p;
            p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1)); r.label = p ? p : "";
            p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 2)); r.importance = p ? p : "";
            r.weight = sqlite3_column_int(stmt, 3);
            p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 4)); r.summary = p ? p : "";
            p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 5)); r.category = p ? p : "";
            p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 6)); r.sub_category = p ? p : "";
            p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 7)); r.scope = p ? p : "";
            p = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 8)); r.project = p ? p : "";
            results.push_back(std::move(r));
        }
        sqlite3_finalize(stmt);
    }
    return results;
}

bool Storage::resolve_entity(const std::string& name, const std::string& alias) {
    if (!conn_ || name.empty() || alias.empty()) return false;
    // 更新已有实体的 alias
    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "UPDATE memory_entity SET alias = ? WHERE entity_name = ? AND alias = ''",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return false;
    sqlite3_bind_text(stmt, 1, alias.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 2, name.c_str(), -1, SQLITE_TRANSIENT);
    rc = sqlite3_step(stmt);
    sqlite3_finalize(stmt);
    return rc == SQLITE_DONE;
}

bool Storage::update_entity_mention(int entity_id, int memory_id, const std::string& context) {
    if (!conn_) return false;
    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(conn_,
        "INSERT INTO memory_entity_mention (entity_id, memory_id, context, created_at) "
        "VALUES (?, ?, ?, datetime('now'))",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return false;
    sqlite3_bind_int(stmt, 1, entity_id);
    sqlite3_bind_int(stmt, 2, memory_id);
    sqlite3_bind_text(stmt, 3, context.empty() ? nullptr : context.c_str(), -1, SQLITE_TRANSIENT);
    rc = sqlite3_step(stmt);
    sqlite3_finalize(stmt);

    // 更新 mention_count 和 last_seen_at
    if (rc == SQLITE_DONE) {
        sqlite3_stmt* ustmt = nullptr;
        if (sqlite3_prepare_v2(conn_,
            "UPDATE memory_entity SET mention_count = mention_count + 1, last_seen_at = datetime('now') "
            "WHERE id = ?",
            -1, &ustmt, nullptr) == SQLITE_OK) {
            sqlite3_bind_int(ustmt, 1, entity_id);
            sqlite3_step(ustmt);
            sqlite3_finalize(ustmt);
        }
    }
    return rc == SQLITE_DONE;
}

} // namespace mw
