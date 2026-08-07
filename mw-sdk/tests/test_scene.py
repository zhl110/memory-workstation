"""test_scene.py — v0.19.0 场景 / 情绪 / 对话状态"""
import pytest


class TestScene:
    def test_set_scene(self, client):
        """设置场景"""
        ok = client.set_scene("code", "代码场景", description="编写代码时的规则")
        assert ok is True

    def test_get_scene(self, client):
        """获取场景"""
        client.set_scene("design", "设计场景")
        scene = client.get_scene("design")
        assert scene is not None
        assert scene["scene_id"] == "design"
        assert scene["name"] == "设计场景"

    def test_get_scene_not_found(self, client):
        """获取不存在的场景"""
        scene = client.get_scene("nonexistent")
        assert scene is None

    def test_list_scenes(self, client):
        """列出所有场景"""
        client.set_scene("s1", "场景1")
        client.set_scene("s2", "场景2")
        scenes = client.list_scenes()
        assert len(scenes) >= 2
        ids = {s["scene_id"] for s in scenes}
        assert "s1" in ids
        assert "s2" in ids

    def test_set_scene_parent(self, client):
        """设置父子场景"""
        client.set_scene("parent", "父场景")
        client.set_scene("child", "子场景", parent_scene="parent")
        scene = client.get_scene("child")
        assert scene is not None
        assert scene["parent_scene"] == "parent"


class TestSceneRule:
    def test_set_scene_rule(self, client):
        """设置场景规则"""
        client.set_scene("code", "代码场景")
        ok = client.set_scene_rule("r1", "code", "must", "使用TypeScript", priority=10)
        assert ok is True

    def test_get_scene_rules(self, client):
        """获取场景规则"""
        client.set_scene("code", "代码场景")
        client.set_scene_rule("r1", "code", "must", "使用TypeScript", priority=10)
        client.set_scene_rule("r2", "code", "should", "写注释", priority=5)
        rules = client.get_scene_rules("code")
        assert len(rules) >= 2
        # 按优先级降序
        assert rules[0]["priority"] >= rules[1]["priority"]

    def test_get_scene_rules_empty(self, client):
        """空场景无规则"""
        rules = client.get_scene_rules("nonexistent")
        assert isinstance(rules, list)
        assert len(rules) == 0


class TestEmotion:
    def test_set_emotion(self, client, sample_data):
        """记录情绪"""
        ok = client.set_emotion(sample_data[0], "positive", "satisfied", 0.8)
        assert ok is True

    def test_get_emotion(self, client, sample_data):
        """获取情绪"""
        client.set_emotion(sample_data[1], "negative", "frustrated", 0.9)
        emo = client.get_emotion(sample_data[1])
        assert emo is not None
        assert emo["emotion_type"] == "negative"
        assert emo["emotion_detail"] == "frustrated"

    def test_get_emotion_not_found(self, client, sample_data):
        """无情绪记录"""
        emo = client.get_emotion(sample_data[2])
        assert emo is None

    def test_set_emotion_multiple(self, client, sample_data):
        """情绪追加（同 doc_id 可有多条，get 返回最新）"""
        client.set_emotion(sample_data[0], "positive", "happy", 0.7)
        client.set_emotion(sample_data[0], "neutral", "calm", 0.5)
        emo = client.get_emotion(sample_data[0])
        assert emo is not None


class TestSessionState:
    def test_save_session_state(self, client):
        """保存对话状态"""
        ok = client.save_session_state("claude", "ses_001", last_topic="MW架构", emotion_state="focused")
        assert ok is True

    def test_get_session_state(self, client):
        """获取对话状态"""
        client.save_session_state("mimo", "ses_002", last_topic="测试")
        state = client.get_session_state("mimo")
        assert state is not None
        assert state["agent_name"] == "mimo"
        assert state["last_topic"] == "测试"

    def test_get_session_state_not_found(self, client):
        """无对话状态"""
        state = client.get_session_state("nonexistent")
        assert state is None

    def test_save_session_state_multiple(self, client):
        """状态追加（同 agent 可有多条，get 返回最新）"""
        client.save_session_state("codex", "ses_a", last_topic="topic A")
        client.save_session_state("codex", "ses_b", last_topic="topic B")
        state = client.get_session_state("codex")
        assert state is not None
