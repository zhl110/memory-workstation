"""test_benchmark.py — 性能基准：搜索/图谱/ingest 速度"""
import pytest
import time


class TestSearchBenchmark:
    def test_search_speed(self, client, sample_data):
        """搜索延迟 < 100ms"""
        start = time.time()
        for _ in range(10):
            client.search("测试", top_k=5)
        elapsed = (time.time() - start) / 10
        assert elapsed < 0.1, f"Search too slow: {elapsed:.3f}s"

    def test_search_empty(self, client):
        """空库搜索不崩溃"""
        results = client.search("test", top_k=5)
        assert isinstance(results, list)


class TestGraphBenchmark:
    def test_insert_cross_refs_speed(self, client, sample_data):
        """cross_ref 写入速度"""
        a, b = sample_data[0], sample_data[1]
        start = time.time()
        client.insert_cross_refs(a, [
            {"related_doc_id": str(b), "relation_type": "related", "note": ""}
        ])
        elapsed = time.time() - start
        assert elapsed < 0.5, f"Cross ref too slow: {elapsed:.3f}s"

    def test_auto_cross_ref_speed(self, client, sample_data):
        """auto_cross_ref 速度"""
        start = time.time()
        for did in sample_data:
            client.auto_cross_ref(did, top_k=3)
        elapsed = time.time() - start
        assert elapsed < 2.0, f"Auto cross ref too slow: {elapsed:.3f}s"


class TestMemoryOperations:
    def test_insert_speed(self, client):
        """单条写入速度"""
        start = time.time()
        for i in range(20):
            client.insert_classified(
                f"benchmark item {i}",
                {"label": "rule", "summary": f"bench {i}", "importance": "P2"}
            )
        elapsed = time.time() - start
        assert elapsed < 2.0, f"Insert too slow: {elapsed:.3f}s"

    def test_get_memory_speed(self, client, sample_data):
        """读取速度"""
        start = time.time()
        for did in sample_data:
            client.get_memory(did)
        elapsed = time.time() - start
        assert elapsed < 0.5, f"Get memory too slow: {elapsed:.3f}s"
