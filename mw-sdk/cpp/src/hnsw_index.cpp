#include "hnsw_index.h"
#include <cstring>
#include <stdexcept>
#include <string>

namespace mw {

HNSWIndex::HNSWIndex(int dim, int M, int ef_construction)
    : dim_(dim), M_(M), M_max0_(2 * M), ef_construction_(ef_construction),
      ml_(1.0 / std::log(M > 1 ? M : 2)) {}

float HNSWIndex::distance(const std::vector<float>& a, const std::vector<float>& b) const {
    // Cosine similarity (归一化后的 inner product)
    float dot = 0, norm_a = 0, norm_b = 0;
    for (int i = 0; i < dim_; i++) {
        dot += a[i] * b[i];
        norm_a += a[i] * a[i];
        norm_b += b[i] * b[i];
    }
    if (norm_a == 0 || norm_b == 0) return 0;
    return dot / (std::sqrt(norm_a) * std::sqrt(norm_b));
}

int HNSWIndex::random_level() {
    std::uniform_real_distribution<double> dist(0.0, 1.0);
    return static_cast<int>(std::floor(-std::log(dist(rng_) * ml_)));
}

void HNSWIndex::search_layer(const std::vector<float>& query, int entry,
                              int ef, int layer, std::vector<SearchResult>& results) const {
    if (nodes_.find(entry) == nodes_.end()) return;

    // Visited set
    std::unordered_set<int> visited;
    // distance() returns cosine similarity (higher = closer)
    // candidates: max-heap — top() = closest candidate for greedy expansion
    std::priority_queue<std::pair<float, int>,
                        std::vector<std::pair<float, int>>,
                        std::less<std::pair<float, int>>> candidates;

    // w (results): min-heap — top() = furthest result for early termination
    std::priority_queue<std::pair<float, int>,
                        std::vector<std::pair<float, int>>,
                        std::greater<std::pair<float, int>>> w;

    float d = distance(query, nodes_.at(entry).vector);
    candidates.push({d, entry});
    w.push({d, entry});
    visited.insert(entry);

    while (!candidates.empty()) {
        auto [c_dist, c] = candidates.top();
        candidates.pop();

        // w is min-heap: w.top() = furthest (lowest similarity) in results
        if ((int)w.size() >= ef) {
            auto [f_dist, _] = w.top();
            // Candidate further than furthest result — no point expanding further
            if (c_dist < f_dist) break;
        }

        // Check neighbors
        if (layer < (int)nodes_.at(c).neighbors.size()) {
            for (int neighbor : nodes_.at(c).neighbors[layer]) {
                if (visited.count(neighbor)) continue;
                visited.insert(neighbor);

                float n_dist = distance(query, nodes_.at(neighbor).vector);
                if (w.empty()) continue;
                auto [f2, _2] = w.top();

                // Add if closer than furthest result, or if under ef limit
                if (n_dist > f2 || (int)w.size() < ef) {
                    candidates.push({n_dist, neighbor});
                    w.push({n_dist, neighbor});
                    if ((int)w.size() > ef) w.pop();  // Remove furthest (lowest similarity)
                }
            }
        }
    }

    // Extract results (sorted by distance ascending = closest first)
    while (!w.empty()) {
        auto [dist, id] = w.top();
        w.pop();
        results.push_back({id, dist});
    }
    std::reverse(results.begin(), results.end());
}

std::vector<int> HNSWIndex::select_neighbors(const std::vector<float>& query,
                                               std::unordered_set<int>& candidates,
                                               int k, int layer) const {
    // Simple heuristic: select k closest from candidates
    std::vector<std::pair<float, int>> scored;
    scored.reserve(candidates.size());
    for (int id : candidates) {
        auto it = nodes_.find(id);
        if (it != nodes_.end()) {
            float d = distance(query, it->second.vector);
            scored.push_back({d, id});
        }
    }
    std::sort(scored.begin(), scored.end(),
              [](auto& a, auto& b) { return a.first < b.first; });

    std::vector<int> result;
    for (int i = 0; i < std::min(k, (int)scored.size()); i++) {
        result.push_back(scored[i].second);
    }
    return result;
}

void HNSWIndex::add(int id, const std::vector<float>& vector) {
    if ((int)vector.size() != dim_) {
        throw std::runtime_error("Vector dimension mismatch: expected " +
                                 std::to_string(dim_) + ", got " + std::to_string(vector.size()));
    }

    int level = random_level();

    Node node;
    node.id = id;
    node.vector = vector;
    node.max_layer = level;
    node.neighbors.resize(level + 1);

    // First node
    if (nodes_.empty()) {
        nodes_[id] = node;
        entry_id_ = id;
        max_layer_ = level;
        return;
    }

    // Search from top layer down to layer 1
    int curr_entry = entry_id_;
    for (int l = max_layer_; l > level; l--) {
        std::vector<SearchResult> results;
        search_layer(vector, curr_entry, 1, l, results);
        if (!results.empty()) {
            curr_entry = results[0].id;
        }
    }

    // Search at layers [min(level, max_layer_) ... 0] with ef_construction
    for (int l = std::min(level, max_layer_); l >= 0; l--) {
        std::vector<SearchResult> results;
        search_layer(vector, curr_entry, ef_construction_, l, results);

        // Collect candidates
        std::unordered_set<int> candidates;
        for (auto& r : results) candidates.insert(r.id);

        // Select M neighbors
        auto neighbors = select_neighbors(vector, candidates, M_, l);

        // Add bidirectional edges
        node.neighbors[l] = neighbors;
        for (int neighbor : neighbors) {
            if (nodes_.find(neighbor) != nodes_.end()) {
                auto& node_ref = nodes_[neighbor];
                if (l < (int)node_ref.neighbors.size()) {
                    node_ref.neighbors[l].push_back(id);

                    // If too many neighbors, prune (keep closest M)
                    int max_m = (l == 0) ? M_max0_ : M_;
                    if ((int)node_ref.neighbors[l].size() > max_m) {
                        std::unordered_set<int> c(node_ref.neighbors[l].begin(),
                                                   node_ref.neighbors[l].end());
                        auto pruned = select_neighbors(node_ref.vector, c, max_m, l);
                        node_ref.neighbors[l] = pruned;
                    }
                }
            }
        }

        curr_entry = results.empty() ? id : results[0].id;
    }

    // Update entry point if new node is higher
    if (level > max_layer_) {
        entry_id_ = id;
        max_layer_ = level;
    }

    nodes_[id] = node;
}

void HNSWIndex::reserve(size_t expected_size) {
    nodes_.reserve(expected_size);
}

void HNSWIndex::add_batch(const std::vector<int>& ids,
                            const std::vector<std::vector<float>>& vectors) {
    if (ids.empty()) return;

    // 预分配：避免逐条插入时 hash map 反 rehash
    size_t new_size = nodes_.size() + ids.size();
    if (new_size > nodes_.bucket_count() * 0.7) {
        nodes_.reserve(new_size * 2);
    }

    for (size_t i = 0; i < ids.size(); i++) {
        add(ids[i], vectors[i]);
    }
}

std::vector<HNSWIndex::SearchResult> HNSWIndex::search(const std::vector<float>& query,
                                                         int top_k, int ef) const {
    if (nodes_.empty() || entry_id_ < 0) return {};

    int curr_entry = entry_id_;

    // Search from top layer down to layer 1
    for (int l = max_layer_; l > 0; l--) {
        std::vector<SearchResult> results;
        search_layer(query, curr_entry, 1, l, results);
        if (!results.empty()) {
            curr_entry = results[0].id;
        }
    }

    // Search at layer 0 with ef
    std::vector<SearchResult> results;
    search_layer(query, curr_entry, std::max(ef, top_k), 0, results);

    // Return top_k
    if ((int)results.size() > top_k) {
        results.resize(top_k);
    }
    return results;
}

void HNSWIndex::remove(int id) {
    auto it = nodes_.find(id);
    if (it == nodes_.end()) return;

    // Remove from all neighbor lists
    for (auto& [nid, node] : nodes_) {
        for (auto& neighbors : node.neighbors) {
            neighbors.erase(
                std::remove(neighbors.begin(), neighbors.end(), id),
                neighbors.end());
        }
    }

    nodes_.erase(it);

    // Rebuild entry point and max_layer_
    if (nodes_.empty()) {
        entry_id_ = -1;
        max_layer_ = 0;
        return;
    }

    // Find node with highest layer as new entry point
    int best_id = -1;
    int best_layer = -1;
    for (auto& [nid, node] : nodes_) {
        if (node.max_layer > best_layer ||
            (node.max_layer == best_layer && nid < best_id)) {
            best_layer = node.max_layer;
            best_id = nid;
        }
    }
    entry_id_ = best_id;
    max_layer_ = best_layer;
}

void HNSWIndex::clear() {
    nodes_.clear();
    entry_id_ = -1;
    max_layer_ = 0;
}

// ── Serialization ──────────────────────────────────────────────

std::vector<char> HNSWIndex::serialize() const {
    std::vector<char> data;

    auto write_int = [&](int v) {
        data.insert(data.end(), reinterpret_cast<char*>(&v), reinterpret_cast<char*>(&v) + 4);
    };
    auto write_float = [&](float v) {
        data.insert(data.end(), reinterpret_cast<char*>(&v), reinterpret_cast<char*>(&v) + 4);
    };

    write_int(dim_);
    write_int(M_);
    write_int(ef_construction_);
    write_int(entry_id_);
    write_int(max_layer_);
    write_int((int)nodes_.size());

    for (auto& [id, node] : nodes_) {
        write_int(node.id);
        write_int(node.max_layer);
        for (float v : node.vector) write_float(v);
        for (int l = 0; l <= node.max_layer; l++) {
            int ncount = (l < (int)node.neighbors.size()) ? (int)node.neighbors[l].size() : 0;
            write_int(ncount);
            if (ncount > 0) {
                for (int nid : node.neighbors[l]) write_int(nid);
            }
        }
    }

    return data;
}

bool HNSWIndex::deserialize(const std::vector<char>& data) {
    size_t offset = 0;
    auto read_int = [&](int& v) {
        if (offset + 4 > data.size()) return false;
        std::memcpy(&v, data.data() + offset, 4);
        offset += 4;
        return true;
    };
    auto read_float = [&](float& v) {
        if (offset + 4 > data.size()) return false;
        std::memcpy(&v, data.data() + offset, 4);
        offset += 4;
        return true;
    };

    int dim, M, ef, entry, max_l, count;
    if (!read_int(dim) || !read_int(M) || !read_int(ef) ||
        !read_int(entry) || !read_int(max_l) || !read_int(count)) return false;

    dim_ = dim;
    M_ = M;
    M_max0_ = 2 * M;
    ef_construction_ = ef;
    ml_ = 1.0 / std::log(M > 1 ? M : 2);
    entry_id_ = entry;
    max_layer_ = max_l;

    nodes_.clear();
    for (int i = 0; i < count; i++) {
        Node node;
        int nid, nlayer;
        if (!read_int(nid) || !read_int(nlayer)) return false;
        node.id = nid;
        node.max_layer = nlayer;
        node.vector.resize(dim_);
        for (int d = 0; d < dim_; d++) {
            if (!read_float(node.vector[d])) return false;
        }
        node.neighbors.resize(nlayer + 1);
        for (int l = 0; l <= nlayer; l++) {
            int ncount;
            if (!read_int(ncount)) return false;
            node.neighbors[l].resize(ncount);
            for (int j = 0; j < ncount; j++) {
                if (!read_int(node.neighbors[l][j])) return false;
            }
        }
        nodes_[nid] = std::move(node);
    }

    return true;
}

} // namespace mw
