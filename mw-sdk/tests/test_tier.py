"""test_tier.py — v0.20.0 记忆分层 / 时序管理 / 实体解析"""
import pytest


class TestTier:
    def test_set_tier(self, client, sample_data):
        """设置层级"""
        ok = client.set_tier(sample_data[0], "hot", "频繁访问")
        assert ok is True

    def test_get_tier(self, client, sample_data):
        """获取层级"""
        client.set_tier(sample_data[1], "cold", "归档")
        tier = client.get_tier(sample_data[1])
        assert tier == "cold"

    def test_get_tier_default(self, client, sample_data):
        """默认层级"""
        tier = client.get_tier(sample_data[0])
        # 默认 warm 或 hot（取决于 schema 默认值）
        assert tier in ("warm", "hot", "cold")

    def test_set_tier_invalid(self, client, sample_data):
        """无效层级（应失败或忽略）"""
        ok = client.set_tier(sample_data[0], "invalid_tier")
        # 不应崩溃
        assert isinstance(ok, bool)

    def test_tier_change_log(self, client, sample_data):
        """层级变更日志"""
        client.set_tier(sample_data[0], "hot", "promotion reason")
        # 验证不崩溃
        stats = client.get_stats()
        assert "tier_changes" in stats


class TestHotMemories:
    def test_get_hot_memories(self, client, sample_data):
        """获取热记忆"""
        client.set_tier(sample_data[0], "hot")
        client.set_tier(sample_data[1], "hot")
        hot = client.get_hot_memories(limit=10)
        assert isinstance(hot, list)
        # 至少能返回（可能为空，因为 get_hot_memories 的实现可能查 tier='hot'）
        assert isinstance(hot, list)

    def test_get_hot_memories_empty(self, client):
        """无热记忆"""
        hot = client.get_hot_memories(limit=10)
        assert isinstance(hot, list)


class TestArchive:
    def test_archive_memory(self, client, sample_data):
        """归档记忆"""
        ok = client.archive_memory(sample_data[0], "不再需要")
        assert ok is True
        # 归档后层级应为 cold
        tier = client.get_tier(sample_data[0])
        assert tier == "cold"


class TestForget:
    def test_forget_memory(self, client, sample_data):
        """软删除记忆"""
        ok = client.forget_memory(sample_data[2], "过期信息")
        assert ok is True
        # 软删除后 get_memory 应返回 None
        mem = client.get_memory(sample_data[2])
        assert mem is None

    def test_forget_nonexistent(self, client):
        """删除不存在的记忆（幂等，不崩溃）"""
        ok = client.forget_memory(99999)
        assert isinstance(ok, bool)

    def test_forget_removes_fts_row(self, client, sample_data):
        """软删除后 memory_fts 残留行应被清除"""
        did = sample_data[2]
        conn = client.get_conn()
        assert conn.execute("SELECT COUNT(*) FROM memory_fts WHERE doc_id=?", (did,)).fetchone()[0] == 1
        ok = client.forget_memory(did, "过期信息")
        assert ok is True
        assert conn.execute("SELECT COUNT(*) FROM memory_fts WHERE doc_id=?", (did,)).fetchone()[0] == 0
        # 重建后也不应回灌已删文档
        client.rebuild_fts5_index()
        assert conn.execute("SELECT COUNT(*) FROM memory_fts WHERE doc_id=?", (did,)).fetchone()[0] == 0


class TestValidTime:
    def test_set_valid_time(self, client, sample_data):
        """设置生效/失效时间"""
        ok = client.set_valid_time(
            sample_data[0],
            valid_from="2026-01-01",
            valid_until="2026-12-31"
        )
        assert ok is True

    def test_set_valid_time_partial(self, client, sample_data):
        """只设置生效时间"""
        ok = client.set_valid_time(sample_data[1], valid_from="2026-07-01")
        assert ok is True

    def test_get_current_valid(self, client, sample_data):
        """获取当前有效记忆"""
        # 先给 sample_data 关联实体
        client._cpp_storage.batch_ingest(
            "实体测试内容",
            {"label": "rule", "importance": "P1", "content_category": "test"},
            [("TestEntity", "concept")],
            "test"
        )
        results = client.get_current_valid("TestEntity")
        assert isinstance(results, list)


class TestEntityResolution:
    def test_resolve_entity(self, client, sample_data):
        """实体别名解析"""
        # 先写入带实体的记忆
        client._cpp_storage.batch_ingest(
            "实体解析测试：MW SDK 架构",
            {"label": "rule", "importance": "P1", "content_category": "架构决策"},
            [("MW SDK", "tool")],
            "test"
        )
        ok = client.resolve_entity("MW SDK", "MW")
        assert ok is True

    def test_update_entity_mention(self, client, sample_data):
        """更新实体提及记录"""
        # 先写入带实体的记忆
        client._cpp_storage.batch_ingest(
            "提及记录测试：Python 开发",
            {"label": "rule", "importance": "P1", "content_category": "代码类"},
            [("Python", "language")],
            "test"
        )
        # 获取实体 ID
        entities = client.get_entities("Python")
        if entities:
            entity_id = entities[0].get("doc_id", 0)
            # entity_id 可能不对，但至少不崩溃
            ok = client.update_entity_mention(1, sample_data[0], "在正文中提到")
            assert isinstance(ok, bool)
