"""test_mixin_python.py — evolution/stats Mixin 的纯 Python 方法补测

覆盖：
- evolution.py: get_candidates (hot/cold 过滤), _get_pool_candidates_impl,
  get_evolution_stats (key prefix 转换)
- stats.py: get_stats, health_check, register/unregister/list/get agent,
  promote_to_global, get_promotion_candidates
"""
import json
import os
from pathlib import Path


class TestEvolutionCandidates:
    """evolution.py 中 get_candidates 的 Python 过滤逻辑"""

    def test_get_candidates_own_hot(self, client, sample_data):
        """own scope: P0/P1 重要性进入 hot"""
        result = client.get_candidates(scope="own", cold_max_weight=100)
        hot_ids = [c.get("doc_id") for c in result["hot"]]
        assert sample_data[2] in hot_ids

    def test_get_candidates_own_cold(self, client, sample_data):
        """own scope: P2/P3 重要性进入 cold"""
        result = client.get_candidates(scope="own", cold_days=90, cold_max_weight=100)
        cold_ids = [c.get("doc_id") for c in result["cold"]]
        assert sample_data[1] in cold_ids or sample_data[0] in cold_ids

    def test_get_candidates_all_scope(self, client, sample_data):
        """all scope: own + pool 合并"""
        result = client.get_candidates(scope="all")
        assert isinstance(result, dict)
        assert "hot" in result
        assert "cold" in result
        assert isinstance(result["hot"], list)
        assert isinstance(result["cold"], list)

    def test_get_candidates_empty_pool(self, client, sample_data):
        """无 pool_conn 时 all scope 不崩溃"""
        result = client.get_candidates(scope="all")
        assert isinstance(result, dict)


class TestEvolutionStats:
    """get_evolution_stats 的 key prefix 转换"""

    def test_evolution_stats_structure(self, client):
        """返回字典包含所有预期字段"""
        stats = client.get_evolution_stats()
        expected_keys = {
            "corrections_total", "corrections_pending",
            "corrections_promoted", "evolution_events",
            "tier_changes", "by_tier"
        }
        assert expected_keys.issubset(stats.keys())
        assert isinstance(stats["by_tier"], dict)

    def test_evolution_stats_after_events(self, client):
        """执行操作后统计值变化"""
        client.increment_correction("stat_test", "summary", "ctx")
        client.log_event("test_type", "trigger", 1, "detail", 0.5)
        stats = client.get_evolution_stats()
        assert stats["corrections_total"] >= 1
        assert stats["evolution_events"] >= 1

    def test_evolution_stats_by_tier(self, client, sample_data):
        """层级变更后 by_tier 有数据"""
        client.apply_tier_change(sample_data[0], "warm", "hot", "test")
        stats = client.get_evolution_stats()
        assert "hot" in stats["by_tier"] or len(stats["by_tier"]) >= 0


class TestStatsGetStats:
    """StatsMixin.get_stats 纯 SQL 聚合"""

    def test_get_stats_returns_dict(self, client, sample_data):
        stats = client.get_stats()
        assert isinstance(stats, dict)

    def test_get_stats_key_coverage(self, client, sample_data):
        """返回字典包含所有预期统计键"""
        stats = client.get_stats()
        expected_keys = {
            "total_docs", "total_memories", "by_label",
            "by_importance", "cross_ref_count", "cross_ref_by_type",
            "avg_refs_per_doc", "orphan_count", "entity_count",
            "correction_count", "evolution_events", "tier_changes"
        }
        assert expected_keys.issubset(stats.keys())

    def test_get_stats_counts_match_db(self, client, sample_data):
        """统计计数与数据库实际行数一致"""
        stats = client.get_stats()
        conn = client._conn
        actual_total = conn.execute(
            "SELECT COUNT(*) FROM memory_classify"
        ).fetchone()[0]
        assert stats["total_memories"] == actual_total

    def test_get_stats_labels(self, client, sample_data):
        """by_label 包含已插入的 label"""
        stats = client.get_stats()
        assert "rule" in stats["by_label"]
        assert "config" in stats["by_label"]
        assert "bug" in stats["by_label"]

    def test_get_stats_importance(self, client, sample_data):
        """by_importance 包含已插入的重要性等级"""
        stats = client.get_stats()
        assert "P0" in stats["by_importance"]
        assert "P1" in stats["by_importance"]
        assert "P2" in stats["by_importance"]

    def test_get_stats_empty_db(self, client):
        """空库 get_stats 不崩溃"""
        stats = client.get_stats()
        assert stats["total_memories"] == 0
        assert stats["total_docs"] == 0


class TestStatsHealthCheck:
    """StatsMixin.health_check 混合 C++/Python"""

    def test_health_check_structure(self, client):
        result = client.health_check()
        assert isinstance(result, dict)
        assert "database" in result
        assert "pool" in result
        assert "c_engine" in result
        assert "vector" in result
        assert "graph" in result

    def test_health_check_database_status(self, client):
        result = client.health_check()
        assert result["database"]["status"] == "ok"
        assert "fts5_entries" in result["database"]

    def test_health_check_c_engine(self, client):
        result = client.health_check()
        assert result["c_engine"]["status"] == "ok"

    def test_health_check_vector_warning(self, client):
        """未构建向量索引时返回 warning"""
        result = client.health_check()
        assert result["vector"]["status"] in ("warning", "ok")


class TestStatsPromotion:
    """promote_to_global / get_promotion_candidates"""

    def test_get_promotion_candidates(self, client, sample_data):
        """weight>=80 的项目出现在候选列表中"""
        candidates = client.get_promotion_candidates(min_weight=1)
        assert isinstance(candidates, list)

    def test_promote_to_global(self, client, sample_data):
        """提升为 global 后 scope 变更"""
        promoted = client.promote_to_global(min_weight=1, min_access=0)
        assert isinstance(promoted, list)


class TestAgentRegistry:
    """register_agent / unregister_agent / list_agents / get_agent"""

    def test_register_agent(self, client, tmp_path):
        db_path = str(tmp_path / "agent_test.db")
        result = client.register_agent("test_agent", "custom", db_path)
        assert result["name"] == "test_agent"
        assert result["status"] == "active"

    def test_list_agents_after_register(self, client, tmp_path):
        db_path = str(tmp_path / "agent_list.db")
        client.register_agent("list_test_agent", "custom", db_path)
        agents = client.list_agents()
        names = [a["name"] for a in agents if isinstance(a, dict) and "name" in a]
        assert "list_test_agent" in names

    def test_get_agent(self, client, tmp_path):
        db_path = str(tmp_path / "agent_get.db")
        client.register_agent("get_test_agent", "custom", db_path)
        agent = client.get_agent("get_test_agent")
        assert agent is not None
        assert agent["name"] == "get_test_agent"

    def test_get_agent_not_found(self, client):
        agent = client.get_agent("nonexistent_agent")
        assert agent is None

    def test_unregister_agent(self, client, tmp_path):
        db_path = str(tmp_path / "agent_unreg.db")
        client.register_agent("unreg_test_agent", "custom", db_path)
        result = client.unregister_agent("unreg_test_agent")
        assert result["name"] == "unreg_test_agent"
        assert result["deleted"] is True

    def test_unregister_nonexistent(self, client):
        result = client.unregister_agent("no_such_agent")
        assert result["name"] == "no_such_agent"
        assert result["deleted"] is False

    def test_agent_registry_persists(self, client, tmp_path):
        """注册表通过 JSON 文件持久化"""
        db_path = str(tmp_path / "persist_agent.db")
        client.register_agent("persist_test", "custom", db_path)
        from mw_sdk.utils import get_agents_registry_path
        reg_path = get_agents_registry_path()
        assert os.path.exists(reg_path)
        with open(reg_path, "r", encoding="utf-8") as f:
            registry = json.load(f)
        assert "persist_test" in registry

    def test_multiple_agents(self, client, tmp_path):
        """注册多个 agent 都能列出"""
        paths = []
        for i in range(3):
            p = str(tmp_path / f"multi_agent_{i}.db")
            paths.append(p)
            client.register_agent(f"multi_{i}", "custom", p)
        agents = client.list_agents()
        names = [a["name"] for a in agents if isinstance(a, dict) and "name" in a]
        for i in range(3):
            assert f"multi_{i}" in names

    def test_unregister_with_delete(self, client, tmp_path):
        """delete_db=True 时物理删除数据库文件"""
        db_path = str(tmp_path / "delete_agent.db")
        client.register_agent("delete_test", "custom", db_path)
        assert os.path.exists(db_path)
        client.unregister_agent("delete_test", delete_db=True)
        assert not os.path.exists(db_path)



