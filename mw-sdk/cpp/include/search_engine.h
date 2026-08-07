#pragma once

#include "mw_core.h"
#include "hnsw_index.h"
#include <string>
#include <vector>
#include <map>
#include <set>
#include <functional>
#include <memory>
#include <mutex>
#include <chrono>

namespace mw {

enum class SearchMode { RRF, Hybrid };

struct SearchConfig {
    SearchMode mode = SearchMode::RRF;
    int k = 60;                      // RRF parameter
    int dedup_window_minutes = 5;    // Dedup window
    double weights[3] = {0.4, 0.2, 0.4};  // fts5, entity, vector
    int hnsw_M = 16;
    int hnsw_ef_construction = 200;
    int hnsw_ef_search = 50;         // 降到 50（226 条数据不需要 100）
};

class SearchEngine {
public:
    SearchEngine(Storage& storage, const SearchConfig& config = SearchConfig{});

    // Main search entry
    std::vector<SearchResult> search(const std::string& query, int top_k = 10,
                                     bool enable_vector = false,
                                     bool enable_graph = true,
                                     int graph_expand_top = 3,
                                     int graph_max_hops = 2,
                                     const std::string& extra_keywords = "");

    // Search with query embedding (Python daemon provides embedding)
    std::vector<SearchResult> search(const std::string& query,
                                     const std::vector<float>& query_embedding,
                                     int top_k = 10,
                                     bool enable_graph = true,
                                     int graph_expand_top = 3,
                                     int graph_max_hops = 2,
                                     const std::string& extra_keywords = "");

    // Vector index management
    bool has_vector_index() const { return hnsw_ && !hnsw_->empty(); }
    void build_vector_index(int dim);
    void add_vector(int id, const std::vector<float>& vec);
    std::vector<std::pair<int, float>> vector_search(const std::vector<float>& query, int top_k);
    bool load_vector_index(const std::vector<char>& data);
    std::vector<char> save_vector_index() const;

private:
    Storage& storage_;
    SearchConfig config_;
    std::map<std::string, std::chrono::system_clock::time_point> recent_hashes_;  // Dedup cache (hash → first_seen)
    mutable std::mutex dedup_mutex_;  // Protects recent_hashes_
    std::unique_ptr<HNSWIndex> hnsw_;
    mutable std::mutex hnsw_mutex_;  // Guards all hnsw_ access (HNSWIndex has no internal locks)
    int vector_dim_ = 0;

    // Sub-searches
    std::map<int, double> fts_search(const std::string& query, int limit,
                                     const std::string& extra_keywords = "");
    std::map<int, double> entity_search(const std::string& query);

    // Fusion modes
    std::vector<SearchResult> search_rrf(const std::set<int>& all_ids,
                                         const std::map<int, double>& bm25,
                                         const std::map<int, double>& entity,
                                         const std::map<int, double>& vector,
                                         int top_k);
    std::vector<SearchResult> search_hybrid(const std::set<int>& all_ids,
                                            const std::map<int, double>& bm25,
                                            const std::map<int, double>& entity,
                                            const std::map<int, double>& vector,
                                            int top_k);

    // Core search implementation (shared by both search() overloads)
    std::vector<SearchResult> search_impl(const std::string& query,
                                          const std::vector<float>& query_embedding,
                                          int top_k, bool enable_graph,
                                          int graph_expand_top, int graph_max_hops,
                                          const std::string& extra_keywords = "");

    // Helpers
    static double rrf_score(int rank, int k);
    static double ebbinghaus_retention(int days_since_access, int stability = 7);
    bool is_duplicate(const std::string& content);
    void clean_dedup_cache();
    static std::string sanitize_fts5_query(const std::string& query);
    std::vector<SearchResult> build_results(const std::map<int, double>& merged,
                                            const std::map<int, double>& bm25,
                                            const std::map<int, double>& entity,
                                            const std::map<int, double>& vector,
                                            int top_k);
    static std::map<int, int> compute_ranks(const std::map<int, double>& scores);
    std::vector<SearchResult> expand_graph(const std::vector<SearchResult>& results,
                                           int expand_top, int max_hops);
};

} // namespace mw
