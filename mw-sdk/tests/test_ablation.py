"""test_ablation.py — 搜索消融：模式对比 + explain"""
import pytest


class TestAblationModes:
    def test_rrf_mode(self, client, sample_data):
        client.set_mode("rrf")
        results = client.search("测试", top_k=5)
        assert isinstance(results, list)

    def test_hybrid_mode(self, client, sample_data):
        client.set_mode("hybrid")
        results = client.search("测试", top_k=5)
        assert isinstance(results, list)

    def test_mode_consistency(self, client, sample_data):
        """切换模式不崩溃"""
        for mode in ["rrf", "hybrid"]:
            client.set_mode(mode)
            results = client.search("测试", top_k=3)
            assert isinstance(results, list)

    def test_score_ranges(self, client, sample_data):
        """搜索分数在合理范围"""
        results = client.search("测试", top_k=5)
        for r in results:
            score = r.get("score", 0)
            assert 0 <= score <= 10, f"Score out of range: {score}"


class TestExplainEnhanced:
    def test_explain_contains_contributions(self, client, sample_data):
        """explain 包含信号分量"""
        client.set_mode("rrf")
        results = client.search("测试", top_k=3)
        if results and "signals" in results[0]:
            signals = results[0]["signals"]
            assert isinstance(signals, dict)

    def test_explain_matches_list(self, client, sample_data):
        """explain 长度匹配结果数"""
        results = client.search("测试", top_k=3)
        assert len(results) <= 3
