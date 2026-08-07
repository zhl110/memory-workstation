"""SceneMixin — 场景 / 情绪 / 对话状态（v0.19.0）

纯 C++ Storage 委派，无 Python 状态。MemoryClient 通过继承使用。
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .types import SceneDict, SceneRuleDict, EmotionDict, SessionStateDict
from .utils import cpp_to_dict

if TYPE_CHECKING:
    from .client import MemoryClient


class SceneMixin:
    """场景 / 情绪 / 对话状态"""

    _cpp_storage: object  # injected by MemoryClient

    def set_scene(self: MemoryClient, scene_id: str, name: str,
                  parent_scene: str = "", description: str = "") -> bool:
        from ._core import mw_core as _cpp_core
        scene = _cpp_core.SceneRecord()
        scene.scene_id = scene_id
        scene.name = name
        scene.parent_scene = parent_scene
        scene.description = description
        return self._cpp_storage.set_scene(scene)

    def get_scene(self: MemoryClient, scene_id: str) -> SceneDict | None:
        result = self._cpp_storage.get_scene(scene_id)
        return cpp_to_dict(result) if result else None

    def list_scenes(self: MemoryClient) -> list[SceneDict]:
        return [cpp_to_dict(r) for r in self._cpp_storage.list_scenes()]

    def set_scene_rule(self: MemoryClient, rule_id: str, scene_id: str,
                       rule_type: str, rule_text: str, priority: int = 0) -> bool:
        from ._core import mw_core as _cpp_core
        rule = _cpp_core.SceneRuleRecord()
        rule.rule_id = rule_id
        rule.scene_id = scene_id
        rule.rule_type = rule_type
        rule.rule_text = rule_text
        rule.priority = priority
        return self._cpp_storage.set_scene_rule(rule)

    def get_scene_rules(self: MemoryClient, scene_id: str) -> list[SceneRuleDict]:
        return [cpp_to_dict(r) for r in self._cpp_storage.get_scene_rules(scene_id)]

    def set_emotion(self: MemoryClient, doc_id: int, emotion_type: str,
                    emotion_detail: str = "", intensity: float = 0.5) -> bool:
        return self._cpp_storage.set_emotion(doc_id, emotion_type, emotion_detail, intensity)

    def get_emotion(self: MemoryClient, doc_id: int) -> EmotionDict | None:
        result = self._cpp_storage.get_emotion(doc_id)
        return cpp_to_dict(result) if result else None

    def save_session_state(self: MemoryClient, agent_name: str, session_id: str = "",
                           last_topic: str = "", unfinished_tasks: str = "[]",
                           emotion_state: str = "") -> bool:
        from ._core import mw_core as _cpp_core
        state = _cpp_core.SessionStateRecord()
        state.state_id = f"{agent_name}_{session_id}_{int(datetime.now(timezone.utc).timestamp())}"
        state.agent_name = agent_name
        state.session_id = session_id
        state.last_topic = last_topic
        state.unfinished_tasks = unfinished_tasks
        state.emotion_state = emotion_state
        return self._cpp_storage.save_session_state(state)

    def get_session_state(self: MemoryClient, agent_name: str,
                          session_id: str = "") -> SessionStateDict | None:
        result = self._cpp_storage.get_session_state(agent_name, session_id)
        return cpp_to_dict(result) if result else None
