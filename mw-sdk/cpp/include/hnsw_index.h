#pragma once

#include <vector>
#include <unordered_map>
#include <unordered_set>
#include <random>
#include <mutex>
#include <algorithm>
#include <cmath>
#include <queue>
#include <limits>

namespace mw {

// HNSW 向量索引 — 自实现，无外部依赖
// 基于论文: "Efficient and robust approximate nearest neighbor search using HNSW graphs"
//
// Thread safety: NOT thread-safe internally. Callers must provide external
// synchronization (e.g., SearchEngine::hnsw_mutex_) for concurrent access.
// All public methods (add, search, remove, clear, serialize, deserialize)
// must not be called concurrently without external locking.
class HNSWIndex {
public:
    struct SearchResult {
        int id;
        float distance;
    };

    // M: 每层最大连接数, ef_construction: 构建时候选列表大小
    explicit HNSWIndex(int dim, int M = 16, int ef_construction = 200);

    // 基本操作
    void add(int id, const std::vector<float>& vector);
    void add_batch(const std::vector<int>& ids, const std::vector<std::vector<float>>& vectors);

    // 预分配内存（批量构建前调用，减少 realloc）
    void reserve(size_t expected_size);
    std::vector<SearchResult> search(const std::vector<float>& query, int top_k, int ef = 100) const;

    // 管理
    void remove(int id);
    void clear();
    size_t size() const { return nodes_.size(); }
    int dimension() const { return dim_; }

    // 持久化（序列化到内存块）
    std::vector<char> serialize() const;
    bool deserialize(const std::vector<char>& data);

    // 空索引检测
    bool empty() const { return nodes_.empty(); }

private:
    struct Node {
        int id;
        std::vector<float> vector;
        int max_layer;
        std::vector<std::vector<int>> neighbors;  // neighbors[layer] = list of neighbor ids
    };

    int dim_;
    int M_;
    int M_max0_;  // layer 0 用 2*M
    int ef_construction_;
    double ml_;  // 层级乘法因子 = 1/ln(M)

    mutable std::mt19937 rng_{42};  // 固定种子保证可重复性
    std::unordered_map<int, Node> nodes_;
    int entry_id_ = -1;
    int max_layer_ = 0;

    // 距离计算（cosine similarity，归一化后用 inner product）
    float distance(const std::vector<float>& a, const std::vector<float>& b) const;

    // 从候选集中选 top-k（ef 参数）
    std::vector<int> select_neighbors(const std::vector<float>& query,
                                       std::unordered_set<int>& candidates,
                                       int k, int layer) const;

    // 理想层级
    int random_level();

    // 内部搜索（单层）
    void search_layer(const std::vector<float>& query, int entry,
                      int ef, int layer, std::vector<SearchResult>& results) const;
};

} // namespace mw
