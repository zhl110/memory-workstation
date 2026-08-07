"""test_evolution.py — 进化系统：access / tier / correction / decay"""
import pytest


class TestRecordAccess:
    def test_record_access(self, client, sample_data):
        """记录访问"""
        client.record_access(sample_data[0])
        has = client._cpp_storage.has_recent_access(sample_data[0], 1)
        assert has is True

    def test_record_access_batch(self, client, sample_data):
        """批量记录访问"""
        client._cpp_storage.record_access_batch(sample_data)


class TestTierSystem:
    def test_apply_tier_change(self, client, sample_data):
        """层级变更"""
        client.apply_tier_change(sample_data[0], "warm", "hot", "test promotion")
        history = client.get_tier_history(sample_data[0])
        assert isinstance(history, list)

    def test_get_tier_history(self, client, sample_data):
        """层级历史"""
        client.apply_tier_change(sample_data[0], "warm", "cold", "demotion")
        history = client.get_tier_history(sample_data[0], limit=5)
        assert len(history) >= 1


class TestCorrection:
    def test_increment_correction(self, client):
        """纠正计数"""
        client.increment_correction("test_pattern", "test summary", "test context")
        pending = client.get_correction_pending(min_count=1)
        assert isinstance(pending, list)

    def test_suppress_correction(self, client):
        """抑制纠正"""
        client.increment_correction("suppress_test", "sum", "ctx")
        client.suppress_correction("suppress_test")

    def test_promote_correction(self, client):
        """晋升纠正"""
        client.increment_correction("promote_test", "sum", "ctx")
        client.promote_correction("promote_test")

    def test_list_corrections(self, client):
        """列出纠正"""
        client.increment_correction("list_test", "sum", "ctx")
        corrections = client.list_corrections(limit=10)
        assert isinstance(corrections, list)


class TestDecay:
    def test_decay_weights(self, client, sample_data):
        """权重衰减"""
        client.record_access(sample_data[0])
        decayed = client.decay_weights(factor=0.8, min_weight=10, decay_days=30)
        assert isinstance(decayed, int)


class TestEvolutionStats:
    def test_get_evolution_stats(self, client):
        """进化统计"""
        stats = client.get_evolution_stats()
        assert isinstance(stats, dict)

    def test_get_evolution_log(self, client):
        """进化日志"""
        client.log_event("test_event", "test", 1, "detail", 0.9)
        logs = client.get_evolution_log("test_event")
        assert isinstance(logs, list)


class TestAlwaysLoad:
    def test_set_and_get(self, client, sample_data):
        """常驻加载"""
        client.set_always_load(sample_data[0], True)
        al = client.get_always_load()
        assert len(al) > 0

    def test_clear(self, client, sample_data):
        """清除常驻"""
        client.set_always_load(sample_data[0], True)
        client.clear_always_load(sample_data[0])
        al = client.get_always_load()
        assert sample_data[0] not in [x.get("doc_id", x) if isinstance(x, dict) else x for x in al]
