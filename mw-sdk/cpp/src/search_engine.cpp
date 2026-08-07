#include "search_engine.h"
#include <algorithm>
#include <cmath>
#include <sstream>
#include <chrono>
#include <ctime>
#include <cstring>
#include <sqlite3.h>

namespace mw {

SearchEngine::SearchEngine(Storage& storage, const SearchConfig& config)
    : storage_(storage), config_(config) {}

// ── Vector Index Management ───────────────────────────────────

void SearchEngine::build_vector_index(int dim) {
    std::lock_guard<std::mutex> lock(hnsw_mutex_);
    hnsw_ = std::make_unique<HNSWIndex>(dim, config_.hnsw_M, config_.hnsw_ef_construction);
    vector_dim_ = dim;
}

void SearchEngine::add_vector(int id, const std::vector<float>& vec) {
    std::lock_guard<std::mutex> lock(hnsw_mutex_);
    if (!hnsw_) {
        vector_dim_ = vec.size();
        hnsw_ = std::make_unique<HNSWIndex>(vector_dim_, config_.hnsw_M, config_.hnsw_ef_construction);
    }
    hnsw_->add(id, vec);
}

std::vector<std::pair<int, float>> SearchEngine::vector_search(const std::vector<float>& query, int top_k) {
    std::lock_guard<std::mutex> lock(hnsw_mutex_);
    if (!hnsw_ || hnsw_->empty()) return {};

    auto results = hnsw_->search(query, top_k, config_.hnsw_ef_search);
    std::vector<std::pair<int, float>> out;
    out.reserve(results.size());
    for (auto& r : results) {
        out.push_back({r.id, r.distance});
    }
    return out;
}

bool SearchEngine::load_vector_index(const std::vector<char>& data) {
    std::lock_guard<std::mutex> lock(hnsw_mutex_);
    hnsw_ = std::make_unique<HNSWIndex>(vector_dim_ > 0 ? vector_dim_ : 384,
                                         config_.hnsw_M, config_.hnsw_ef_construction);
    return hnsw_->deserialize(data);
}

std::vector<char> SearchEngine::save_vector_index() const {
    std::lock_guard<std::mutex> lock(hnsw_mutex_);
    if (!hnsw_) return {};
    return hnsw_->serialize();
}

// ── Main Search ───────────────────────────────────────────────

std::vector<SearchResult> SearchEngine::search(const std::string& query, int top_k,
                                               bool enable_vector, bool enable_graph,
                                               int graph_expand_top, int graph_max_hops,
                                               const std::string& extra_keywords) {
    std::vector<float> embedding;
    if (enable_vector) {
        std::lock_guard<std::mutex> lock(hnsw_mutex_);
        if (hnsw_ && !hnsw_->empty()) {
            embedding = storage_.get_query_embedding(query);
        }
    }
    return search_impl(query, embedding, top_k, enable_graph,
                       graph_expand_top, graph_max_hops, extra_keywords);
}

std::vector<SearchResult> SearchEngine::search(const std::string& query,
                                               const std::vector<float>& query_embedding,
                                               int top_k, bool enable_graph,
                                               int graph_expand_top, int graph_max_hops,
                                               const std::string& extra_keywords) {
    return search_impl(query, query_embedding, top_k, enable_graph,
                       graph_expand_top, graph_max_hops, extra_keywords);
}

std::vector<SearchResult> SearchEngine::search_impl(const std::string& query,
                                                    const std::vector<float>& query_embedding,
                                                    int top_k, bool enable_graph,
                                                    int graph_expand_top, int graph_max_hops,
                                                    const std::string& extra_keywords) {
    // 0. 清空 dedup 缓存，防止跨调用污染
    {
        std::lock_guard<std::mutex> lock(dedup_mutex_);
        recent_hashes_.clear();
    }

    // 1. BM25 search — OR 语义扩大覆盖，适当放大候选集
    int fts_limit = std::max(top_k * 2, top_k + top_k / 2);
    auto bm25_scores = fts_search(query, fts_limit, extra_keywords);

    // 2. Entity search
    auto entity_scores = entity_search(query);

    // 3. Vector search (optional — empty embedding = skip)
    std::map<int, double> vector_scores;
    if (!query_embedding.empty()) {
        int vec_limit = std::max(top_k + top_k / 2, top_k);
        auto vec_results = vector_search(query_embedding, vec_limit);
        if (!vec_results.empty()) {
            double max_dist = 0;
            for (auto& [id, dist] : vec_results) {
                if (dist > max_dist) max_dist = dist;
            }
            if (max_dist <= 0) max_dist = 1;
            for (auto& [id, dist] : vec_results) {
                vector_scores[id] = dist / max_dist;
            }
        }
    }

    // 4. Collect all candidate IDs
    std::set<int> all_ids;
    for (auto& [id, _] : bm25_scores) all_ids.insert(id);
    for (auto& [id, _] : entity_scores) all_ids.insert(id);
    for (auto& [id, _] : vector_scores) all_ids.insert(id);

    if (all_ids.empty()) return {};

    // 6. Fuse based on mode
    std::vector<SearchResult> results;
    switch (config_.mode) {
        case SearchMode::RRF:
            results = search_rrf(all_ids, bm25_scores, entity_scores, vector_scores, top_k);
            break;
        case SearchMode::Hybrid:
            results = search_hybrid(all_ids, bm25_scores, entity_scores, vector_scores, top_k);
            break;
    }

    // 7. Graph expand
    if (enable_graph && results.size() > 0) {
        results = expand_graph(results, graph_expand_top, graph_max_hops);
    }

    if ((int)results.size() > top_k) {
        results.resize(top_k);
    }
    return results;
}

// ── FTS Search ────────────────────────────────────────────────

std::map<int, double> SearchEngine::fts_search(const std::string& query, int limit,
                                                const std::string& extra_keywords) {
    std::map<int, double> results;

    // 1. Always try FTS5 MATCH first (trigram tokenizer supports CJK)
    auto fts_results = storage_.fts_search(sanitize_fts5_query(query), limit, extra_keywords);
    for (auto& r : fts_results) {
        results[r.doc_id] = r.score;
    }

    // 2. Fallback to LIKE if no results (handles CJK with unicode61 tokenizer)
    // 注意：直接调用 Storage 方法，不通过 pybind11 绑定
    // 这样不会触发 GIL 释放的嵌套问题
    if (results.empty()) {
        auto like_results = storage_.like_search(query, limit * 5);
        for (auto& r : like_results) {
            if (results.find(r.doc_id) == results.end()) {
                results[r.doc_id] = r.score;
            }
        }
    }

    return results;
}

// ── Entity Search ─────────────────────────────────────────────

std::map<int, double> SearchEngine::entity_search(const std::string& query) {
    return storage_.entity_search(query);
}

// ── Fusion Modes ──────────────────────────────────────────────

std::vector<SearchResult> SearchEngine::search_rrf(const std::set<int>& all_ids,
                                                   const std::map<int, double>& bm25,
                                                   const std::map<int, double>& entity,
                                                   const std::map<int, double>& vector,
                                                   int top_k) {
    auto bm25_ranks = compute_ranks(bm25);
    auto entity_ranks = compute_ranks(entity);
    auto vector_ranks = compute_ranks(vector);

    int n = all_ids.size();
    std::vector<int> id_vec(all_ids.begin(), all_ids.end());
    auto recent_ids = storage_.has_recent_access_batch(id_vec, 7);

    // 读取三路权重并归一化（确保总和=1）
    double w0 = config_.weights[0];  // fts5
    double w1 = config_.weights[1];  // entity
    double w2 = config_.weights[2];  // vector
    double w_sum = w0 + w1 + w2;
    if (w_sum > 0) { w0 /= w_sum; w1 /= w_sum; w2 /= w_sum; }

    std::map<int, double> merged;
    for (int doc_id : all_ids) {
        int br = bm25_ranks.count(doc_id) ? bm25_ranks[doc_id] : n + 1;
        int er = entity_ranks.count(doc_id) ? entity_ranks[doc_id] : n + 1;
        int vr = vector_ranks.count(doc_id) ? vector_ranks[doc_id] : n + 1;

        // 加权 RRF：每个信号的排名分数乘以对应权重
        double rrf = w0 * rrf_score(br, config_.k)
                   + w1 * rrf_score(er, config_.k)
                   + w2 * rrf_score(vr, config_.k);

        if (recent_ids.count(doc_id)) {
            rrf *= RECENCY_BOOST;
        }

        merged[doc_id] = rrf;
    }

    return build_results(merged, bm25, entity, vector, top_k);
}

std::vector<SearchResult> SearchEngine::search_hybrid(const std::set<int>& all_ids,
                                                       const std::map<int, double>& bm25,
                                                       const std::map<int, double>& entity,
                                                       const std::map<int, double>& vector,
                                                       int top_k) {
    auto bm25_ranks = compute_ranks(bm25);
    auto entity_ranks = compute_ranks(entity);
    auto vector_ranks = compute_ranks(vector);

    int n = all_ids.size();
    std::vector<int> id_vec(all_ids.begin(), all_ids.end());
    // Batch query actual days since last access per document
    auto access_days = storage_.get_access_days_batch(id_vec);
    // Batch query weights for stability calculation
    auto mem_weights = storage_.get_weights_batch(id_vec);

    // 读取三路权重并归一化（确保总和=1）
    double w0 = config_.weights[0];  // fts5
    double w1 = config_.weights[1];  // entity
    double w2 = config_.weights[2];  // vector
    double w_sum = w0 + w1 + w2;
    if (w_sum > 0) { w0 /= w_sum; w1 /= w_sum; w2 /= w_sum; }

    std::map<int, double> merged;
    for (int doc_id : all_ids) {
        int br = bm25_ranks.count(doc_id) ? bm25_ranks[doc_id] : n + 1;
        int er = entity_ranks.count(doc_id) ? entity_ranks[doc_id] : n + 1;
        int vr = vector_ranks.count(doc_id) ? vector_ranks[doc_id] : n + 1;

        // 加权 RRF：每个信号的排名分数乘以对应权重
        double rrf = w0 * rrf_score(br, config_.k)
                   + w1 * rrf_score(er, config_.k)
                   + w2 * rrf_score(vr, config_.k);

        // Apply Ebbinghaus decay based on actual days since last access
        // Documents with no access record (new) get no decay
        auto it = access_days.find(doc_id);
        if (it != access_days.end() && it->second > 0) {
            // Stability based on weight: higher weight = higher stability = slower decay
            // weight range [50, 100], stability range [5, 10]
            int w = mem_weights.count(doc_id) ? mem_weights[doc_id] : 50;
            int stability = 5 + w / 20;  // 50→7, 70→8, 90→9, 100→10
            rrf *= ebbinghaus_retention(it->second, stability);
        }

        merged[doc_id] = rrf;
    }

    return build_results(merged, bm25, entity, vector, top_k);
}

// ── Helpers ───────────────────────────────────────────────────

double SearchEngine::rrf_score(int rank, int k) {
    return 1.0 / (k + rank);
}

std::map<int, int> SearchEngine::compute_ranks(const std::map<int, double>& scores) {
    std::vector<std::pair<int, double>> sorted(scores.begin(), scores.end());
    // 用 stable_sort：同分时保持 map 遍历顺序（按 doc_id 升序），排名确定可复现
    std::stable_sort(sorted.begin(), sorted.end(),
                     [](auto& a, auto& b) { return a.second > b.second; });
    std::map<int, int> ranks;
    for (int i = 0; i < (int)sorted.size(); i++) {
        ranks[sorted[i].first] = i + 1;
    }
    return ranks;
}

double SearchEngine::ebbinghaus_retention(int days_since_access, int stability) {
    if (days_since_access <= 0) return 1.0;
    return std::exp(-static_cast<double>(days_since_access) / stability);
}

bool SearchEngine::is_duplicate(const std::string& content) {
    if (config_.dedup_window_minutes <= 0) return false;

    std::lock_guard<std::mutex> lock(dedup_mutex_);

    // Simple hash
    std::hash<std::string> hasher;
    size_t h = hasher(content);
    std::string key = std::to_string(h);

    auto now = std::chrono::system_clock::now();
    auto it = recent_hashes_.find(key);
    if (it != recent_hashes_.end()) {
        // Check if within dedup window
        auto elapsed = std::chrono::duration_cast<std::chrono::minutes>(now - it->second).count();
        if (elapsed < config_.dedup_window_minutes) {
            return true;  // Within window → duplicate
        }
        // Expired → update timestamp and allow
        it->second = now;
        return false;
    }

    recent_hashes_[key] = now;
    return false;
}

void SearchEngine::clean_dedup_cache() {
    std::lock_guard<std::mutex> lock(dedup_mutex_);
    auto now = std::chrono::system_clock::now();
    // Remove expired entries
    for (auto it = recent_hashes_.begin(); it != recent_hashes_.end(); ) {
        auto elapsed = std::chrono::duration_cast<std::chrono::minutes>(now - it->second).count();
        if (elapsed >= config_.dedup_window_minutes) {
            it = recent_hashes_.erase(it);
        } else {
            ++it;
        }
    }
    // Safety cap
    if (recent_hashes_.size() > 1000) {
        recent_hashes_.clear();
    }
}

std::string SearchEngine::sanitize_fts5_query(const std::string& query) {
    std::string safe = query;
    for (char c : {'"', '\'', '-', '(', ')', ':', '^', '[', ']', '{', '}', '*', '~'}) {
        safe.erase(std::remove(safe.begin(), safe.end(), c), safe.end());
    }
    // Trim
    safe.erase(0, safe.find_first_not_of(" \t\n\r"));
    auto pos = safe.find_last_not_of(" \t\n\r");
    if (pos != std::string::npos) safe.erase(pos + 1);
    return safe;
}

std::vector<SearchResult> SearchEngine::build_results(const std::map<int, double>& merged,
                                                      const std::map<int, double>& bm25,
                                                      const std::map<int, double>& entity,
                                                      const std::map<int, double>& vector,
                                                      int top_k) {
    // Sort by score descending
    std::vector<std::pair<int, double>> sorted(merged.begin(), merged.end());
    std::sort(sorted.begin(), sorted.end(),
              [](auto& a, auto& b) { return a.second > b.second; });

    // 计算三路排名，用于填充 signals（explain 依赖）
    auto bm25_ranks = compute_ranks(bm25);
    auto entity_ranks = compute_ranks(entity);
    auto vector_ranks = compute_ranks(vector);

    clean_dedup_cache();

    // Batch-load all candidate memories in one query (avoids N+1)
    std::vector<int> candidate_ids;
    candidate_ids.reserve(sorted.size());
    for (auto& [doc_id, _] : sorted) {
        candidate_ids.push_back(doc_id);
    }
    auto memories = storage_.get_memories_batch(candidate_ids);

    std::vector<SearchResult> results;
    for (auto& [doc_id, score] : sorted) {
        if ((int)results.size() >= top_k) break;

        auto it = memories.find(doc_id);
        if (it == memories.end()) continue;

        // Dedup
        if (is_duplicate(it->second.summary)) continue;

        SearchResult r;
        r.doc_id = doc_id;
        r.score = score;
        r.summary = it->second.summary;
        r.category = it->second.category;
        r.importance = it->second.importance;
        r.weight = it->second.weight;
        r.scope = it->second.scope;
        r.project = it->second.project;
        // 填充三路信号：原始分 + 排名 + 融合分数（explain/消融依赖）
        r.signals["rrf_score"] = score;
        auto b25 = bm25.find(doc_id);
        if (b25 != bm25.end()) r.signals["bm25"] = b25->second;
        auto b25r = bm25_ranks.find(doc_id);
        if (b25r != bm25_ranks.end()) r.signals["bm25_rank"] = b25r->second;
        auto ent = entity.find(doc_id);
        if (ent != entity.end()) r.signals["entity"] = ent->second;
        auto entr = entity_ranks.find(doc_id);
        if (entr != entity_ranks.end()) r.signals["entity_rank"] = entr->second;
        auto vec = vector.find(doc_id);
        if (vec != vector.end()) r.signals["vector"] = vec->second;
        auto vecr = vector_ranks.find(doc_id);
        if (vecr != vector_ranks.end()) r.signals["vector_rank"] = vecr->second;
        results.push_back(r);
    }

    return results;
}

// ── Graph Expansion ──────────────────────────────────────────

std::vector<SearchResult> SearchEngine::expand_graph(const std::vector<SearchResult>& results,
                                                     int expand_top, int max_hops) {
    if (results.empty() || expand_top <= 0) return results;

    // Collect IDs from top results to expand
    std::set<int> existing_ids;
    std::vector<int> seed_ids;
    for (const auto& r : results) {
        existing_ids.insert(r.doc_id);
        if ((int)seed_ids.size() < expand_top) {
            seed_ids.push_back(r.doc_id);
        }
    }

    // BFS expansion from seed nodes
    struct HopInfo { int doc_id; int hop; double score; };
    std::vector<HopInfo> expanded;
    std::queue<std::pair<int, int>> bfs_queue;  // (doc_id, current_hop)

    for (int seed : seed_ids) {
        bfs_queue.push({seed, 0});
    }

    // Batch-query cross_ref neighbors to avoid N+1
    std::set<int> visited;
    while (!bfs_queue.empty()) {
        // Collect current-level nodes for batch query
        std::vector<int> current_level;
        std::vector<int> current_hops;
        std::set<int> in_current_level;  // Track to dedup within level
        while (!bfs_queue.empty()) {
            auto [node, hop] = bfs_queue.front();
            bfs_queue.pop();
            if (hop >= max_hops) continue;
            if (in_current_level.count(node)) continue;  // Dedup within same level only
            current_level.push_back(node);
            current_hops.push_back(hop);
            in_current_level.insert(node);
        }

        if (current_level.empty()) break;

        // Mark current-level nodes as visited AFTER collection
        for (int n : current_level) visited.insert(n);

        // Single batch SQL for all current-level nodes
        sqlite3* db = storage_.raw_conn();
        if (!db) break;

        std::string placeholders;
        for (size_t i = 0; i < current_level.size(); i++) {
            if (i > 0) placeholders += ",";
            placeholders += "?";
        }

        sqlite3_stmt* stmt = nullptr;
        // UNION 双向查询：出边 + 入边（含时间戳）
        std::string sql = "SELECT doc_id, related_doc_id, created_at FROM memory_cross_ref WHERE doc_id IN (" + placeholders + ") "
                          "UNION "
                          "SELECT related_doc_id, doc_id, created_at FROM memory_cross_ref WHERE related_doc_id IN (" + placeholders + ")";
        if (sqlite3_prepare_v2(db, sql.c_str(), -1, &stmt, nullptr) == SQLITE_OK) {
            // Bind 出边参数
            for (size_t i = 0; i < current_level.size(); i++) {
                sqlite3_bind_int(stmt, i + 1, current_level[i]);
            }
            // Bind 入边参数
            for (size_t i = 0; i < current_level.size(); i++) {
                sqlite3_bind_int(stmt, current_level.size() + i + 1, current_level[i]);
            }
            // Map doc_id -> hop for quick lookup
            std::map<int, int> node_hop;
            for (size_t i = 0; i < current_level.size(); i++) {
                node_hop[current_level[i]] = current_hops[i];
            }

            auto now = std::chrono::system_clock::now();
            while (sqlite3_step(stmt) == SQLITE_ROW) {
                int src = sqlite3_column_int(stmt, 0);
                int neighbor = sqlite3_column_int(stmt, 1);
                if (!existing_ids.count(neighbor)) {
                    int parent_hop = node_hop.count(src) ? node_hop[src] : 0;
                    if (!visited.count(neighbor)) {
                        // 时间加权：边越新，分数越高（30天半衰期）
                        double temporal_boost = 1.0;
                        if (const char* created_str = (const char*)sqlite3_column_text(stmt, 2)) {
                            int year, month, day, hour, min, sec;
                            if (sscanf(created_str, "%d-%d-%d %d:%d:%d",
                                       &year, &month, &day, &hour, &min, &sec) == 6) {
                                std::tm tm = {};
                                tm.tm_year = year - 1900;
                                tm.tm_mon = month - 1;
                                tm.tm_mday = day;
                                tm.tm_hour = hour;
                                tm.tm_min = min;
                                tm.tm_sec = sec;
                                auto tp = std::chrono::system_clock::from_time_t(std::mktime(&tm));
                                auto days = std::chrono::duration_cast<std::chrono::hours>(now - tp).count() / 24.0;
                                if (days >= 0) temporal_boost = std::pow(0.5, days / 30.0);
                            }
                        }
                        double decay = std::pow(GRAPH_DECAY, parent_hop + 1);
                        double base_score = results.empty() ? 0.5 : results[0].score;
                        expanded.push_back({neighbor, parent_hop + 1, base_score * decay * temporal_boost});
                        bfs_queue.push({neighbor, parent_hop + 1});
                    }
                }
            }
            sqlite3_finalize(stmt);
        }
    }

    // Merge expanded results into main results
    std::vector<SearchResult> merged = results;
    for (auto& exp : expanded) {
        existing_ids.insert(exp.doc_id);  // Dedup
        // Fetch memory details
        auto mem = storage_.get_memory(exp.doc_id);
        if (!mem) continue;

        SearchResult r;
        r.doc_id = exp.doc_id;
        r.score = exp.score;
        r.summary = mem->summary;
        r.category = mem->category;
        r.importance = mem->importance;
        r.weight = mem->weight;
        r.signals["graph_expand"] = 1.0;
        merged.push_back(r);
    }

    // Re-sort by score descending
    std::sort(merged.begin(), merged.end(),
              [](const SearchResult& a, const SearchResult& b) { return a.score > b.score; });

    return merged;
}

} // namespace mw
