"""test_search.py — FTS5 / LIKE / Entity / 融合搜索"""
import pytest


class TestFTS5Search:
    def test_fts_returns_results(self, client, sample_data):
        """FTS5 trigram 搜索能找到内容"""
        results = client.search("前端设计", top_k=10)
        assert len(results) > 0

    def test_fts_chinese(self, client, sample_data):
        """中文关键词搜索"""
        results = client.search("数据库", top_k=10)
        assert len(results) > 0

    def test_fts_no_match(self, client, sample_data):
        """不存在的关键词返回空（纯 FTS5）"""
        results = client.search("完全不存在的关键词xyz", top_k=10, enable_vector=False, enable_graph=False)
        assert len(results) == 0

    def test_fts_empty_query(self, client, sample_data):
        """空查询不崩溃"""
        results = client.search("", top_k=10)
        assert isinstance(results, list)


class TestEntitySearch:
    def test_entity_search(self, client, sample_data):
        """Entity 搜索"""
        # 先插入 entity
        client._cpp_storage.insert_entities(sample_data[0], [("MW_SDK", "system")])
        # 禁用向量搜索（测试环境无 ONNX 模型）
        results = client.search("MW_SDK", top_k=10, enable_vector=False)
        assert len(results) > 0


class TestCombinedSearch:
    def test_search_returns_score(self, client, sample_data):
        """搜索结果包含 score 字段"""
        results = client.search("前端", top_k=5)
        if results:
            assert "score" in results[0]

    def test_search_returns_doc_id(self, client, sample_data):
        """搜索结果包含 doc_id"""
        results = client.search("测试", top_k=5)
        if results:
            assert "doc_id" in results[0]

    def test_search_top_k(self, client, sample_data):
        """top_k 限制结果数量"""
        results = client.search("测试", top_k=1)
        assert len(results) <= 1


class TestDedupBehavior:
    def test_dedup_no_cross_call_contamination(self, client, sample_data):
        """dedup 不应跨调用污染：连续两次搜索同一关键词都应返回结果"""
        r1 = client.search("测试", top_k=5)
        r2 = client.search("测试", top_k=5)
        assert len(r1) > 0, "第一次搜索无结果"
        assert len(r2) > 0, "第二次搜索返回 0 条，dedup 跨调用污染未修复"
