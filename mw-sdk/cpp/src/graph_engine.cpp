#include "graph_engine.h"
#include <algorithm>
#include <queue>
#include <limits>
#include <sqlite3.h>

namespace mw {

GraphEngine::GraphEngine(Storage& storage) : storage_(storage) {}

void GraphEngine::build() {
    std::lock_guard<std::recursive_mutex> lock(mutex_);
    adj_.clear();
    nodes_.clear();

    sqlite3* db = storage_.raw_conn();
    if (!db) return;

    // Get all nodes
    sqlite3_stmt* stmt = nullptr;
    if (sqlite3_prepare_v2(db, "SELECT doc_id FROM memory_classify", -1, &stmt, nullptr) == SQLITE_OK) {
        while (sqlite3_step(stmt) == SQLITE_ROW) {
            int id = sqlite3_column_int(stmt, 0);
            nodes_.insert(id);
            adj_[id];  // Ensure entry exists
        }
        sqlite3_finalize(stmt);
    }

    // Get all edges
    if (sqlite3_prepare_v2(db,
        "SELECT doc_id, related_doc_id, relation_type FROM memory_cross_ref",
        -1, &stmt, nullptr) == SQLITE_OK) {
        while (sqlite3_step(stmt) == SQLITE_ROW) {
            int src = sqlite3_column_int(stmt, 0);
            int dst = sqlite3_column_int(stmt, 1);
            const char* rel = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 2));
            std::string rel_type = rel ? rel : "related";

            double w = edge_weight(rel_type);
            adj_[src].push_back({dst, rel_type, w});
            nodes_.insert(src);
            nodes_.insert(dst);
        }
        sqlite3_finalize(stmt);
    }

    built_ = true;
}

void GraphEngine::invalidate() {
    std::lock_guard<std::recursive_mutex> lock(mutex_);
    built_ = false;
    adj_.clear();
    nodes_.clear();
}

// 边权重：Dijkstra 中权重越低 = 关联越强 = 路径越优先
// 范围：0.3(最强) ~ 0.9(最弱)
double GraphEngine::edge_weight(const std::string& type) {
    if (type == "premise")  return 0.3;  // 前提关系，逻辑最强
    if (type == "extend")   return 0.4;  // 扩展关系，知识延伸
    if (type == "example")  return 0.5;  // 示例关系，具体化
    if (type == "similar")  return 0.6;  // 语义相似
    if (type == "related")  return 0.7;  // 一般关联
    if (type == "mention")  return 0.8;  // 提及，弱信号
    if (type == "refute")   return 0.9;  // 反驳，负向关系
    return 0.7;  // 默认：一般关联
}

// ── BFS ───────────────────────────────────────────────────────

std::vector<TraverseResult> GraphEngine::bfs_traverse(int source, int max_hops,
                                                      const std::string& relation_type) {
    std::lock_guard<std::recursive_mutex> lock(mutex_);
    if (!built_) build();

    std::vector<TraverseResult> result;
    if (nodes_.find(source) == nodes_.end()) return result;

    std::set<int> visited;
    // 使用 shared_ptr 共享路径数据，避免每次扩展都复制整个向量
    using PathRef = std::shared_ptr<std::vector<int>>;
    struct BfsState { int node; int hop; std::string rel_type; PathRef path; };
    std::deque<BfsState> queue;
    queue.push_back({source, 0, "", std::make_shared<std::vector<int>>(std::vector<int>{source})});

    while (!queue.empty()) {
        auto state = queue.front();
        queue.pop_front();

        if (visited.count(state.node)) continue;
        visited.insert(state.node);

        if (state.hop > 0) {
            result.push_back({state.node, state.hop, state.rel_type, *state.path});
        }

        if (state.hop >= max_hops) continue;

        auto it = adj_.find(state.node);
        if (it != adj_.end()) {
            for (const auto& edge : it->second) {
                if (visited.count(edge.target)) continue;
                if (!relation_type.empty() && edge.relation_type != relation_type) continue;
                // 共享父路径，只追加新节点
                PathRef new_path = std::make_shared<std::vector<int>>(*state.path);
                new_path->push_back(edge.target);
                queue.push_back({edge.target, state.hop + 1, edge.relation_type, new_path});
            }
        }
    }

    return result;
}

std::map<int, std::vector<TraverseResult>> GraphEngine::bfs_by_hop(int source, int max_hops,
                                                                   const std::string& relation_type) {
    auto traverse_result = bfs_traverse(source, max_hops, relation_type);
    std::map<int, std::vector<TraverseResult>> by_hop;
    for (const auto& item : traverse_result) {
        by_hop[item.hop].push_back(item);
    }
    return by_hop;
}

std::vector<GraphEdge> GraphEngine::get_neighbors(int doc_id, const std::string& relation_type) {
    std::lock_guard<std::recursive_mutex> lock(mutex_);
    if (!built_) build();

    std::vector<GraphEdge> result;
    auto it = adj_.find(doc_id);
    if (it == adj_.end()) return result;

    for (const auto& edge : it->second) {
        if (!relation_type.empty() && edge.relation_type != relation_type) continue;
        result.push_back(edge);
    }
    return result;
}

// ── Dijkstra ──────────────────────────────────────────────────

std::optional<std::vector<int>> GraphEngine::shortest_path(int source, int target, int max_hops) {
    std::lock_guard<std::recursive_mutex> lock(mutex_);
    if (!built_) build();

    if (nodes_.find(source) == nodes_.end() || nodes_.find(target) == nodes_.end()) {
        return std::nullopt;
    }

    // Dijkstra with hop tracking
    const double INF = std::numeric_limits<double>::max();
    std::map<int, double> dist;
    std::map<int, int> prev;
    std::map<int, int> hops;

    for (int node : nodes_) {
        dist[node] = INF;
    }
    dist[source] = 0;
    hops[source] = 0;

    // Priority queue: (distance, node, hop_count)
    using State = std::tuple<double, int, int>;
    std::priority_queue<State, std::vector<State>, std::greater<>> pq;
    pq.push({0, source, 0});

    while (!pq.empty()) {
        auto [d, u, h] = pq.top();
        pq.pop();

        if (d > dist[u]) continue;
        if (u == target) break;
        if (h >= max_hops) continue;

        auto it = adj_.find(u);
        if (it == adj_.end()) continue;

        for (const auto& edge : it->second) {
            double new_dist = d + edge.weight;
            if (new_dist < dist[edge.target]) {
                dist[edge.target] = new_dist;
                prev[edge.target] = u;
                hops[edge.target] = h + 1;
                pq.push({new_dist, edge.target, h + 1});
            }
        }
    }

    if (dist[target] == INF || hops[target] > max_hops) return std::nullopt;

    // Reconstruct path (with cycle detection)
    std::vector<int> path;
    std::set<int> path_visited;
    for (int at = target; at != source; at = prev[at]) {
        if (path_visited.count(at)) return std::nullopt;  // 循环断裂
        path_visited.insert(at);
        path.push_back(at);
    }
    path.push_back(source);
    std::reverse(path.begin(), path.end());
    return path;
}

std::optional<std::vector<int>> GraphEngine::find_path(int source, int target, int max_hops) {
    std::lock_guard<std::recursive_mutex> lock(mutex_);
    // Try Dijkstra first
    auto path = shortest_path(source, target, max_hops);
    if (path) return path;

    // Fallback to BFS
    if (!built_) build();

    if (nodes_.find(source) == nodes_.end() || nodes_.find(target) == nodes_.end()) {
        return std::nullopt;
    }

    std::set<int> visited;
    std::queue<std::pair<int, std::vector<int>>> q;
    q.push({source, {source}});

    while (!q.empty()) {
        auto [node, path] = q.front();
        q.pop();

        if (visited.count(node)) continue;
        visited.insert(node);

        if (node == target) return path;
        if ((int)path.size() > max_hops) continue;

        auto it = adj_.find(node);
        if (it != adj_.end()) {
            for (const auto& edge : it->second) {
                if (!visited.count(edge.target)) {
                    std::vector<int> new_path = path;
                    new_path.push_back(edge.target);
                    if ((int)new_path.size() <= max_hops + 1) {
                        q.push({edge.target, new_path});
                    }
                }
            }
        }
    }

    return std::nullopt;
}

// ── Stats ─────────────────────────────────────────────────────

GraphStats GraphEngine::get_stats() {
    std::lock_guard<std::recursive_mutex> lock(mutex_);
    if (!built_) build();

    GraphStats stats;
    stats.total_nodes = nodes_.size();
    stats.total_edges = 0;

    int total_degree = 0;
    std::set<int> orphans;

    for (int node : nodes_) {
        auto it = adj_.find(node);
        if (it == adj_.end() || it->second.empty()) {
            orphans.insert(node);
        } else {
            total_degree += it->second.size();
            stats.total_edges += it->second.size();
            for (const auto& edge : it->second) {
                stats.edge_type_distribution[edge.relation_type]++;
            }
        }
    }

    stats.avg_degree = stats.total_nodes > 0 ?
        static_cast<double>(total_degree) / stats.total_nodes : 0;
    stats.orphan_count = orphans.size();
    stats.orphan_rate = stats.total_nodes > 0 ?
        static_cast<double>(stats.orphan_count) / stats.total_nodes : 0;

    return stats;
}

bool GraphEngine::add_edge(int doc_id, int related_doc_id, const std::string& relation_type,
                           const std::string& note) {
    if (doc_id == related_doc_id) return false;  // 防止自引用
    std::lock_guard<std::recursive_mutex> lock(mutex_);
    sqlite3* db = storage_.raw_conn();
    if (!db) return false;

    // 先检查是否已存在
    sqlite3_stmt* check = nullptr;
    int rc = sqlite3_prepare_v2(db,
        "SELECT 1 FROM memory_cross_ref WHERE doc_id=? AND related_doc_id=? AND relation_type=?",
        -1, &check, nullptr);
    if (rc != SQLITE_OK) return false;
    sqlite3_bind_int(check, 1, doc_id);
    sqlite3_bind_int(check, 2, related_doc_id);
    sqlite3_bind_text(check, 3, relation_type.c_str(), -1, SQLITE_TRANSIENT);
    bool exists = (sqlite3_step(check) == SQLITE_ROW);
    sqlite3_finalize(check);
    if (exists) return false;

    sqlite3_stmt* stmt = nullptr;
    rc = sqlite3_prepare_v2(db,
        "INSERT INTO memory_cross_ref (doc_id, related_doc_id, relation_type, note) "
        "VALUES (?, ?, ?, ?)",
        -1, &stmt, nullptr);
    if (rc != SQLITE_OK) return false;

    sqlite3_bind_int(stmt, 1, doc_id);
    sqlite3_bind_int(stmt, 2, related_doc_id);
    sqlite3_bind_text(stmt, 3, relation_type.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 4, note.c_str(), -1, SQLITE_TRANSIENT);

    rc = sqlite3_step(stmt);
    sqlite3_finalize(stmt);

    if (rc == SQLITE_DONE) {
        invalidate();
        return true;
    }
    return false;
}

} // namespace mw
