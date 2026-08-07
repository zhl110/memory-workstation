"""MW SDK 类型定义

所有 MemoryClient 方法的返回值类型。TypedDict 是纯类型标注，不影响运行时行为。
"""

from __future__ import annotations

from typing import Any, TypedDict


# ═══════════════════════════════════════════════════════════
# 输入类型
# ═══════════════════════════════════════════════════════════

class ClassificationDict(TypedDict, total=False):
    """分类结果字典（上层 Agent 完成分类后传入 SDK）"""
    label: str
    importance: str
    category: str
    sub_category: str
    summary: str
    knowledge_type: str
    applicability: str
    depth: str
    content_type: str
    entities: list[EntityMiniDict]
    tags: list[str]
    workspace_id: str
    memory_type: str
    scope: str  # 记忆所属范围：global/project/session
    scene: str  # 场景标签
    emotion: str  # 情绪标签


class EntityMiniDict(TypedDict):
    """实体简写（写入时用）"""
    name: str
    type: str


class CrossRefRefDict(TypedDict):
    """交叉引用写入项"""
    related_doc_id: int
    relation_type: str
    note: str


# ═══════════════════════════════════════════════════════════
# 搜索相关
# ═══════════════════════════════════════════════════════════

class SearchExplainDict(TypedDict):
    """搜索 explain 子结构"""
    query: str
    search_mode: str
    matched_by: str
    matches: list[str]
    signals: dict[str, Any]
    contributions: dict[str, float]


class SearchResultDict(TypedDict):
    """搜索结果（search() 返回值元素）"""
    doc_id: int
    summary: str
    category: str
    importance: str
    weight: int
    score: float
    signals: dict[str, Any]
    explain: SearchExplainDict | None


# ═══════════════════════════════════════════════════════════
# 记忆读取
# ═══════════════════════════════════════════════════════════

class MemoryDetailDict(TypedDict):
    """完整记忆（get_memory() 返回值）"""
    doc_id: int
    file_path: str
    summary: str
    label: str
    importance: str
    weight: int
    category: str
    sub_category: str
    depth: str
    entities: list[EntityMiniDict]


class LinkedDict(TypedDict):
    """关联记忆（get_linked() 返回值元素）"""
    doc_id: int
    relation_type: str
    note: str
    summary: str
    category: str
    importance: str


# ═══════════════════════════════════════════════════════════
# 规则与实体
# ═══════════════════════════════════════════════════════════

class RuleDict(TypedDict):
    """全局规则（get_rules() 返回值元素）"""
    id: int
    rule_text: str
    category: str
    sub_category: str
    priority: str
    confidence: float
    conflict_with: str
    complements: str


class EntityDict(TypedDict):
    """实体（get_entities() 返回值元素）"""
    doc_id: int
    entity_name: str
    entity_type: str
    weight: float
    summary: str


# ═══════════════════════════════════════════════════════════
# 知识图谱
# ═══════════════════════════════════════════════════════════

class GraphStatsDict(TypedDict):
    """图谱统计（get_graph_stats() 返回值）"""
    total_nodes: int
    total_edges: int
    avg_degree: float
    orphan_count: int
    orphan_rate: float
    edge_type_distribution: dict[str, int]


class BfsNodeDict(TypedDict):
    """BFS 遍历节点（bfs_traverse() 返回值元素）"""
    doc_id: int
    hop: int
    relation_type: str
    path: list[int]


class PathNodeDict(TypedDict):
    """路径节点（find_path() 返回值元素）"""
    doc_id: int
    relation_type: str


class CrossRefCandidateDict(TypedDict):
    """交叉引用候选（_find_cross_ref_candidates() 返回值元素）"""
    doc_id: int
    summary: str
    score: float


# ═══════════════════════════════════════════════════════════
# 统计与健康
# ═══════════════════════════════════════════════════════════

class StatsDict(TypedDict):
    """知识库统计（get_stats() 返回值）"""
    total_docs: int
    total_memories: int
    by_label: dict[str, int]
    by_importance: dict[str, int]
    cross_ref_count: int
    cross_ref_by_type: dict[str, int]
    avg_refs_per_doc: float
    orphan_count: int
    entity_count: int
    correction_count: int
    evolution_events: int
    tier_changes: int


class HealthComponentDict(TypedDict, total=False):
    """健康检查单项"""
    status: str
    detail: str
    fts5_entries: int
    fts5_behind: int
    nodes: int
    edges: int
    orphan_rate: float


class HealthCheckDict(TypedDict):
    """健康检查结果（health_check() 返回值）"""
    database: HealthComponentDict
    pool: HealthComponentDict
    c_engine: HealthComponentDict
    vector: HealthComponentDict
    graph: HealthComponentDict


# ═══════════════════════════════════════════════════════════
# 进化系统
# ═══════════════════════════════════════════════════════════

class CandidateDict(TypedDict):
    """进化候选（get_candidates() 内部元素）"""
    doc_id: int
    summary: str
    importance: str
    weight: int
    evolution_tier: str


class CandidatesDict(TypedDict):
    """进化候选分组（get_candidates() 返回值）"""
    cold: list[CandidateDict]
    hot: list[CandidateDict]


class IncrementCorrectionDict(TypedDict):
    """纠正计数（increment_correction() 返回值）"""
    count: int
    is_new: bool


class EvolutionStatsDict(TypedDict):
    """进化统计（get_evolution_stats() 返回值）"""
    corrections_total: int
    corrections_pending: int
    corrections_promoted: int
    evolution_events: int
    tier_changes: int
    by_tier: dict[str, int]


# ═══════════════════════════════════════════════════════════
# 操作结果
# ═══════════════════════════════════════════════════════════

class CrawlStatsDict(TypedDict):
    """图谱扫描结果（crawl_cross_ref() 返回值）"""
    processed: int
    new_edges: int
    skipped: int
    total_edges: int


class RebuildLinksDict(TypedDict):
    """图谱重建结果（rebuild_links() 返回值）"""
    processed: int
    new_edges: int
    skipped: int
    total: int


class CleanupStatsDict(TypedDict):
    """清理结果（cleanup_memories() 返回值）"""
    test_count: int
    stale_count: int
    deleted: int
    mode: str


class VectorBuildDict(TypedDict):
    """向量构建结果（build_vector_index() 返回值）"""
    built: int
    skipped: int
    errors: int
    note: str


class VectorStatsDict(TypedDict):
    """向量统计（get_vector_stats() 返回值）"""
    indexed: int
    available: bool


# ═══════════════════════════════════════════════════════════
# Agent 注册
# ═══════════════════════════════════════════════════════════

class AgentInfoDict(TypedDict):
    """Agent 信息（get_agent() / list_agents() 返回值元素）"""
    name: str
    type: str
    db: str
    status: str
    registered_at: str


class AgentRegisterDict(TypedDict):
    """Agent 注册结果（register_agent() 返回值）"""
    name: str
    db_path: str
    status: str


class AgentUnregisterDict(TypedDict):
    """Agent 注销结果（unregister_agent() 返回值）"""
    name: str
    deleted: bool


# ═══════════════════════════════════════════════════════════
# v0.19.0: 场景 / 情绪 / 对话状态
# ═══════════════════════════════════════════════════════════

class SceneDict(TypedDict, total=False):
    """场景定义（get_scene() / list_scenes() 返回值）"""
    scene_id: str
    name: str
    parent_scene: str | None
    description: str
    create_time: str


class SceneRuleDict(TypedDict, total=False):
    """场景规则（get_scene_rules() 返回值元素）"""
    rule_id: str
    scene_id: str
    rule_type: str  # must / should / prefer
    rule_text: str
    priority: int
    create_time: str


class EmotionDict(TypedDict, total=False):
    """情绪记录（get_emotion() 返回值）"""
    emotion_id: str
    doc_id: int
    emotion_type: str  # positive / neutral / negative
    emotion_detail: str
    intensity: float
    create_time: str


class SessionStateDict(TypedDict, total=False):
    """对话状态（get_session_state() 返回值）"""
    state_id: str
    agent_name: str
    session_id: str
    last_topic: str
    unfinished_tasks: str  # JSON array
    emotion_state: str
    update_time: str


# ═══════════════════════════════════════════════════════════
# v0.20.0: 记忆分层 / 时序管理
# ═══════════════════════════════════════════════════════════

class TierChangeDict(TypedDict, total=False):
    """分层变更日志"""
    id: int
    doc_id: int
    from_tier: str
    to_tier: str
    reason: str
    created_at: str


# ═══════════════════════════════════════════════════════════
# v0.21.0: 项目连续上下文
# ═══════════════════════════════════════════════════════════

class ProjectStageDict(TypedDict, total=False):
    """完成阶段"""
    name: str
    time: str
    done: bool


class ProjectDecisionDict(TypedDict, total=False):
    """活跃决策"""
    summary: str
    doc_id: int
    importance: str
    time: str


class ProjectUpdateDict(TypedDict, total=False):
    """Agent 传入的 project_update 字段"""
    phase: str
    completed: str
    blocker: str
    current_goal: str


class ProjectStatusDict(TypedDict, total=False):
    """项目状况快照（get_project_status() 返回值）"""
    project: str
    phase: str
    current_goal: str
    completed_stages: list[ProjectStageDict]
    active_decisions: list[ProjectDecisionDict]
    key_pitfalls: list[ProjectDecisionDict]
    blockers: list[str]
    active_entities: list[str]
    recent_doc_ids: list[int]
    last_trigger_doc_id: int
    last_trigger_message: str
    last_updated_at: str
    stale: bool
    memory_count: int
