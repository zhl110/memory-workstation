#pragma once

#include "mw_core.h"
#include <string>
#include <vector>
#include <map>
#include <set>
#include <deque>
#include <optional>
#include <mutex>
#include <memory>

namespace mw {

struct GraphEdge {
    int target;
    std::string relation_type;
    double weight;
};

struct TraverseResult {
    int doc_id;
    int hop;
    std::string relation_type;
    std::vector<int> path;
};

struct GraphStats {
    int total_nodes;
    int total_edges;
    double avg_degree;
    int orphan_count;
    double orphan_rate;
    std::map<std::string, int> edge_type_distribution;
};

class GraphEngine {
public:
    explicit GraphEngine(Storage& storage);

    // Build graph from database
    void build();
    void invalidate();

    // BFS traverse
    std::vector<TraverseResult> bfs_traverse(int source, int max_hops = 3,
                                             const std::string& relation_type = "");

    // BFS by hop
    std::map<int, std::vector<TraverseResult>> bfs_by_hop(int source, int max_hops = 3,
                                                          const std::string& relation_type = "");

    // Get neighbors
    std::vector<GraphEdge> get_neighbors(int doc_id, const std::string& relation_type = "");

    // Shortest path (Dijkstra)
    std::optional<std::vector<int>> shortest_path(int source, int target, int max_hops = 5);

    // Find path (Dijkstra + BFS fallback)
    std::optional<std::vector<int>> find_path(int source, int target, int max_hops = 5);

    // Stats
    GraphStats get_stats();

    // Add edge
    bool add_edge(int doc_id, int related_doc_id, const std::string& relation_type = "related",
                  const std::string& note = "");

private:
    Storage& storage_;
    mutable std::recursive_mutex mutex_;
    bool built_ = false;

    // Adjacency list: doc_id -> list of edges
    std::map<int, std::vector<GraphEdge>> adj_;
    std::set<int> nodes_;

    // Edge type weights
    static double edge_weight(const std::string& type);
};

} // namespace mw
