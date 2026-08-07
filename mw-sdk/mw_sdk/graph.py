"""GraphMixin — 知识图谱遍历

纯 C++ GraphEngine 委派。MemoryClient 通过继承使用。
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from .types import GraphStatsDict, BfsNodeDict, PathNodeDict

if TYPE_CHECKING:
    from .client import MemoryClient


class GraphMixin:
    """知识图谱遍历（BFS / Dijkstra / 路径查找）"""

    _cpp_graph: object  # C++ GraphEngine

    def find_path(self: MemoryClient, source: int, target: int,
                  max_hops: int = 5) -> list[PathNodeDict] | None:
        result = self._cpp_graph.find_path(source, target, max_hops)
        if result:
            if isinstance(result[0], int):
                return [{"doc_id": did, "relation_type": ""} for did in result]
            return [{"doc_id": r.doc_id, "relation_type": r.relation_type}
                    for r in result]
        return None

    def get_graph_stats(self: MemoryClient) -> GraphStatsDict:
        stats = self._cpp_graph.get_stats()
        return {
            "total_nodes": stats.total_nodes,
            "total_edges": stats.total_edges,
            "avg_degree": round(stats.avg_degree, 2),
            "orphan_count": stats.orphan_count,
            "orphan_rate": round(stats.orphan_rate, 4),
            "edge_type_distribution": dict(stats.edge_type_distribution),
        }

    def add_graph_edge(self: MemoryClient, doc_id: int, related_doc_id: int,
                       relation_type: str = "related", note: str = "") -> bool:
        return self._cpp_graph.add_edge(doc_id, related_doc_id, relation_type, note)

    def bfs_traverse(self: MemoryClient, source: int, max_hops: int = 3,
                     relation_type: str = "") -> list[BfsNodeDict]:
        result = self._cpp_graph.bfs_traverse(source, max_hops, relation_type)
        return [{"doc_id": r.doc_id, "hop": r.hop,
                 "relation_type": r.relation_type, "path": list(r.path)}
                for r in result]

    def bfs_by_hop(self: MemoryClient, source: int, max_hops: int = 3,
                   relation_type: str = "") -> dict[int, list[BfsNodeDict]]:
        result = self._cpp_graph.bfs_by_hop(source, max_hops, relation_type)
        by_hop = {}
        for hop, items in result.items():
            by_hop[hop] = [{"doc_id": r.doc_id, "hop": r.hop,
                            "relation_type": r.relation_type, "path": list(r.path)}
                           for r in items]
        return by_hop
