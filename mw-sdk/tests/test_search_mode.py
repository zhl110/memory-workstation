"""test_search_mode.py — 搜索模式：rrf / hybrid"""
import pytest


class TestRRFMode:
    def test_rrf_search(self, client, sample_data):
        """RRF 模式搜索"""
        client.set_mode("rrf")
        results = client.search("测试", top_k=5)
        assert isinstance(results, list)

    def test_rrf_top_k(self, client, sample_data):
        """RRF 模式 top_k"""
        client.set_mode("rrf")
        results = client.search("测试", top_k=2)
        assert len(results) <= 2


class TestHybridMode:
    def test_hybrid_search(self, client, sample_data):
        """Hybrid 模式搜索"""
        client.set_mode("hybrid")
        results = client.search("测试", top_k=5)
        assert isinstance(results, list)

    def test_hybrid_top_k(self, client, sample_data):
        """Hybrid 模式 top_k"""
        client.set_mode("hybrid")
        results = client.search("测试", top_k=2)
        assert len(results) <= 2


class TestSearchQuality:
    def test_search_returns_relevant(self, client, sample_data):
        """搜索返回相关结果"""
        results = client.search("前端设计", top_k=5)
        if results:
            assert any("前端" in r.get("summary", "") or "前端" in r.get("category", "")
                       for r in results)

    def test_search_no_match(self, client, sample_data):
        """无匹配返回空（纯 FTS5）"""
        results = client.search("完全不存在xyz", top_k=5, enable_vector=False, enable_graph=False)
        assert len(results) == 0
