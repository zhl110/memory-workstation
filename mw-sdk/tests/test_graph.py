"""知识图谱增强测试"""

import pytest
from mw_sdk import MemoryClient


@pytest.fixture
def client_with_graph(tmp_path):
    """创建带有图谱数据的客户端"""
    db_path = str(tmp_path / "test_graph.db")
    m = MemoryClient(db_path, mode="rrf")
    m.init_schema()
    m._pool_conn = None

    items = [
        ('Python编程规则：使用snake_case命名', {'label': 'rule', 'summary': 'Python命名规则', 'importance': 'P1'}),
        ('Python数据库操作：使用SQLite', {'label': 'config', 'summary': '数据库配置', 'importance': 'P2'}),
        ('搜索优化：使用FTS5索引', {'label': 'feature', 'summary': '搜索优化', 'importance': 'P0'}),
        ('图谱知识：使用NetworkX', {'label': 'note', 'summary': '图谱知识', 'importance': 'P1'}),
        ('测试规范：使用pytest', {'label': 'rule', 'summary': '测试规范', 'importance': 'P2'}),
    ]

    doc_ids = []
    for content, cls in items:
        doc_id = m.insert_classified(content, cls)
        doc_ids.append(doc_id)

    m.insert_cross_refs(doc_ids[0], [
        {"related_doc_id": doc_ids[1], "relation_type": "extend", "note": "Python数据库操作扩展"},
        {"related_doc_id": doc_ids[2], "relation_type": "related", "note": "搜索相关"},
    ])
    m.insert_cross_refs(doc_ids[1], [
        {"related_doc_id": doc_ids[3], "relation_type": "premise", "note": "图谱是数据库操作的基础"},
    ])
    m.insert_cross_refs(doc_ids[2], [
        {"related_doc_id": doc_ids[4], "relation_type": "example", "note": "pytest是测试示例"},
    ])

    yield m, doc_ids
    m.close()


class TestGraphEngine:
    def test_shortest_path(self, client_with_graph):
        m, doc_ids = client_with_graph
        path = m.find_path(doc_ids[0], doc_ids[1])
        assert path is not None
        assert len(path) == 2
        assert path[0]["doc_id"] == doc_ids[0]
        assert path[1]["doc_id"] == doc_ids[1]

    def test_shortest_path_with_hops(self, client_with_graph):
        m, doc_ids = client_with_graph
        path = m.find_path(doc_ids[0], doc_ids[3])
        assert path is not None
        assert len(path) == 3

    def test_shortest_path_max_hops(self, client_with_graph):
        m, doc_ids = client_with_graph
        path = m.find_path(doc_ids[0], doc_ids[3], max_hops=1)
        assert path is None

    def test_no_path(self, client_with_graph):
        m, doc_ids = client_with_graph
        isolated_id = m.insert_classified("孤立节点", {'label': 'note', 'summary': '孤立'})
        path = m.find_path(doc_ids[0], isolated_id)
        assert path is None

    def test_get_neighbors(self, client_with_graph):
        m, doc_ids = client_with_graph
        neighbors = m._cpp_graph.get_neighbors(doc_ids[0])
        assert len(neighbors) == 2

    def test_get_neighbors_with_filter(self, client_with_graph):
        m, doc_ids = client_with_graph
        neighbors = m._cpp_graph.get_neighbors(doc_ids[0], relation_type="extend")
        assert len(neighbors) == 1
        assert neighbors[0].target == doc_ids[1]

    def test_get_linked_with_relation_type(self, client_with_graph):
        m, doc_ids = client_with_graph
        all_linked = m.get_linked(doc_ids[0])
        assert len(all_linked) == 2

        extend_linked = m.get_linked(doc_ids[0])
        extend_only = [r for r in extend_linked if r["doc_id"] == doc_ids[1]]
        assert len(extend_only) == 1

    def test_get_linked_backward_compat(self, client_with_graph):
        m, doc_ids = client_with_graph
        results = m.get_linked(doc_ids[0])
        assert len(results) > 0
        assert all("doc_id" in r for r in results)

    def test_get_linked_incoming_edges(self, client_with_graph):
        m, doc_ids = client_with_graph
        # doc 1 被 doc 0 引用（入边），验证双向查询
        linked = m.get_linked(doc_ids[1])
        doc_ids_found = [r["doc_id"] for r in linked]
        assert doc_ids[0] in doc_ids_found  # doc 0 → doc 1 的入边
        # doc 2 也被 doc 0 引用
        linked2 = m.get_linked(doc_ids[2])
        doc_ids_found2 = [r["doc_id"] for r in linked2]
        assert doc_ids[0] in doc_ids_found2

    def test_graph_health_stats(self, client_with_graph):
        m, doc_ids = client_with_graph
        isolated_id = m.insert_classified("孤立节点", {'label': 'note', 'summary': '孤立'})
        stats = m.get_graph_stats()
        assert stats["total_nodes"] == 6
        assert stats["total_edges"] == 4
        assert stats["avg_degree"] > 0
        assert stats["orphan_count"] >= 0
        assert 0 <= stats["orphan_rate"] <= 1

    def test_add_edge(self, client_with_graph):
        m, doc_ids = client_with_graph
        result = m.add_graph_edge(doc_ids[3], doc_ids[4], "mention", "图谱提到测试")
        assert result is True

    def test_add_edge_duplicate(self, client_with_graph):
        m, doc_ids = client_with_graph
        # 用 fixture 中不存在的新边测试
        result1 = m.add_graph_edge(doc_ids[3], doc_ids[4], "see_also", "第一次")
        result2 = m.add_graph_edge(doc_ids[3], doc_ids[4], "see_also", "第二次")
        assert result1 is True
        assert result2 is False  # 重复插入返回 False

    def test_empty_graph(self, tmp_path):
        db_path = str(tmp_path / "empty_graph.db")
        with MemoryClient(db_path, mode="rrf") as m:
            m.init_schema()
            m._pool_conn = None
            stats = m.get_graph_stats()
            assert stats["total_nodes"] == 0
            assert stats["total_edges"] == 0
            assert stats["avg_degree"] == 0
            assert stats["orphan_count"] == 0
            assert stats["orphan_rate"] == 0

    def test_edge_type_distribution(self, client_with_graph):
        m, doc_ids = client_with_graph
        stats = m.get_graph_stats()
        dist = stats["edge_type_distribution"]
        assert "extend" in dist
        assert "related" in dist
        assert "premise" in dist
        assert "example" in dist


class TestBFSTraverse:
    def test_bfs_traverse_basic(self, client_with_graph):
        m, doc_ids = client_with_graph
        result = m._cpp_graph.bfs_traverse(doc_ids[0], max_hops=3)
        assert len(result) > 0
        assert all(hasattr(r, "hop") for r in result)
        assert all(hasattr(r, "doc_id") for r in result)

    def test_bfs_traverse_hop_levels(self, client_with_graph):
        m, doc_ids = client_with_graph
        result = m._cpp_graph.bfs_traverse(doc_ids[0], max_hops=3)
        hops = [r.hop for r in result]
        assert 1 in hops
        assert 2 in hops

    def test_bfs_traverse_max_hops(self, client_with_graph):
        m, doc_ids = client_with_graph
        result_1 = m._cpp_graph.bfs_traverse(doc_ids[0], max_hops=1)
        result_2 = m._cpp_graph.bfs_traverse(doc_ids[0], max_hops=2)
        assert all(r.hop <= 1 for r in result_1)
        assert any(r.hop == 2 for r in result_2)

    def test_bfs_traverse_relation_filter(self, client_with_graph):
        m, doc_ids = client_with_graph
        result = m._cpp_graph.bfs_traverse(doc_ids[0], relation_type="extend")
        assert all(r.relation_type == "extend" for r in result)

    def test_bfs_traverse_path_tracking(self, client_with_graph):
        m, doc_ids = client_with_graph
        result = m._cpp_graph.bfs_traverse(doc_ids[0], max_hops=3)
        for item in result:
            assert hasattr(item, "path")
            assert item.path[0] == doc_ids[0]
            assert item.path[-1] == item.doc_id

    def test_bfs_traverse_isolated_node(self, tmp_path):
        db_path = str(tmp_path / "isolated.db")
        with MemoryClient(db_path, mode="rrf") as m:
            m.init_schema()
            m._pool_conn = None
            isolated_id = m.insert_classified("孤立节点", {'label': 'note', 'summary': '孤立'})
            result = m._cpp_graph.bfs_traverse(isolated_id)
        assert len(result) == 0

    def test_bfs_by_hop(self, client_with_graph):
        m, doc_ids = client_with_graph
        by_hop = m._cpp_graph.bfs_by_hop(doc_ids[0], max_hops=3)
        assert 1 in by_hop
        assert len(by_hop[1]) == 2

    def test_reachable_nodes(self, client_with_graph):
        m, doc_ids = client_with_graph
        result = m._cpp_graph.bfs_traverse(doc_ids[0], max_hops=3)
        reachable = [r.doc_id for r in result]
        assert len(reachable) > 0
        assert doc_ids[0] not in reachable
        assert doc_ids[1] in reachable
        assert doc_ids[2] in reachable
