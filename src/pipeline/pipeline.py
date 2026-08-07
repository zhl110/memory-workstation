"""Pipeline — 统一分类管线：HardFilter → Prefilter → Keyword → PathFallback → Weight → Store。

V10 变更：
- 砍掉 LLM classify（exe 不再依赖外部 LLM）
- 新增 step_hard_filter：扩展名白名单 + 文件大小 + 排除路径 + 隐藏文件
- step_prefilter 阈值 50→10
- process_batch 改为 keyword + resolve_label + path_fallback 直接走（不再调 LLM）
- __init__ 支持 llm_manager=None（None 保护）
"""
from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

from ..core.enums import DocumentLabel, LABEL_TO_TIER
from ..core.token_counter import truncate_tokens
from ..import_manager.splitter import DocumentSplitter

logger = logging.getLogger(__name__)

# ─── 预筛常量（原 main.py 中复制粘贴的两份统一到这里） ───
RULE_SIGNALS = [
    '必须', '禁止', '不能', '不要', '应该', '永远',
    '只允许', '不得', '严禁', '务必', '记住',
    '以后', '规定', '要求', '执行', '规则',
    '决定', '方案', '做法', '习惯', '原则',
    'must', 'never', 'always', 'shall', 'rule',
]
CHAT_MARKERS = [
    '**用户：**', '**Claude：**', '**用户:**', '**Claude:**',
    '用户：', 'Claude：', '> "', 'user:', 'assistant:',
]
META_DESC_PATTERNS = [
    r'定义了.{0,10}规则', r'定义了.{0,10}系统', r'定义了.{0,10}协议',
    r'讨论了.{0,10}方案', r'讨论了.{0,10}问题', r'讨论了.{0,10}筛选',
    r'描述了.{0,10}文档', r'描述了.{0,10}内容',
    r'整理了.{0,10}清单', r'整理了.{0,10}方案',
    r'记录了.{0,10}流程', r'记录了.{0,10}步骤',
    r'该文档.{0,10}规则', r'本文档.{0,10}规则',
    r'该文档.{0,10}系统', r'本文档.{0,10}系统',
    r'该文档为.{0,15}索引', r'本文档为.{0,15}索引',
    r'列出了.{0,10}文档', r'列出.{0,10}标题',
]

# ─── Path fallback 常量 ───
_PATH_RULES = [
    (r"/skill", DocumentLabel.META_RULE, "P1", "AI专属类", "Skill开发"),
    (r"skill.md", DocumentLabel.META_RULE, "P1", "AI专属类", "Skill开发"),
    (r"claude.md", DocumentLabel.META_RULE, "P0", "AI专属类", "Agent配置"),
    (r"rules.md", DocumentLabel.META_RULE, "P0", "AI专属类", "Agent配置"),
    (r"constitution", DocumentLabel.META_RULE, "P0", "AI专属类", "Agent配置"),
    (r"/agent", DocumentLabel.META_RULE, "P1", "AI专属类", "Agent配置"),
    (r"mcp", DocumentLabel.META_RULE, "P1", "AI专属类", "工具链"),
    (r"prompt", DocumentLabel.META_RULE, "P1", "AI专属类", "Prompt工程"),
    (r"plan", DocumentLabel.PLANNING_DOC, "P1", "流程类", "工作流"),
    (r"design", DocumentLabel.PLANNING_DOC, "P1", "流程类", "工作流"),
    (r"/spec", DocumentLabel.PLANNING_DOC, "P1", "业务类", "规格"),
    (r"architecture", DocumentLabel.PLANNING_DOC, "P1", "技术类", "架构"),
    (r"self.improve", DocumentLabel.SELF_IMPROVE_LEARN, "P1", "AI专属类", "调试经验"),
    (r"learn", DocumentLabel.SELF_IMPROVE_LEARN, "P2", "知识类", ""),
    (r"memory", DocumentLabel.MEMORY_LAYER, "P2", "参考类", ""),
    (r"license", DocumentLabel.META_RULE, "P0", "参考类", ""),
    (r"readme", DocumentLabel.COMPACT_ARCHIVE, "P3", "参考类", ""),
]
_MEMORY_PATH_PATTERNS = [
    "/memory/", "/learnings/", "/projects/", "/chat-history/",
    "/.codex/", "/.claude/projects/", "/.claude/plans/",
    "/agent-hub/",
]

# ─── 硬性过滤常量（V10 新增：扩展名白名单 + 排除路径 + 排除文件名） ───
_ALLOWED_EXTENSIONS = {
    ".md", ".txt", ".yaml", ".yml", ".json", ".py", ".js", ".toml",
    ".cfg", ".conf", ".ini", ".bat", ".ps1", ".sh", ".sql", ".csv",
    ".xml", ".psm1", ".psd1",
}
_EXCLUDED_PATH_SEGMENTS = {
    "/temp/", "/backup/", "/node_modules/", "/.git/", "__pycache__/",
    "/dist/", "/build/", "/venv/", "/.env/",
}
_EXCLUDED_NAME_PATTERNS = [
    "_old", "-bak", "backup",
]
_MAX_FILE_SIZE = 500 * 1024  # 500KB


def step_hard_filter(ctx: PipelineContext, storage=None) -> None:
    """硬性准入过滤（V10 新增，在 prefilter 之前执行）

    检查：扩展名白名单、文件大小、排除路径、隐藏文件/备份文件、classification_exclusions。
    命中任一 → reject。
    """
    fp = ctx.filepath
    fname = os.path.basename(fp)
    fpath_norm = fp.replace("\\", "/").lower()

    # 1. 扩展名白名单
    ext = os.path.splitext(fp)[1].lower()
    if ext not in _ALLOWED_EXTENSIONS:
        ctx.prefilter_reject = f"ext_not_allowed:{ext}"
        return

    # 2. 文件大小（content 长度近似）
    if len(ctx.content.encode("utf-8", errors="replace")) > _MAX_FILE_SIZE:
        ctx.prefilter_reject = "file_too_large"
        return

    # 3. 排除路径
    for seg in _EXCLUDED_PATH_SEGMENTS:
        if seg in fpath_norm:
            ctx.prefilter_reject = f"excluded_path:{seg}"
            return

    # 4. 隐藏文件（以 . 开头，排除 .env/ 目录本身）
    if fname.startswith(".") and not fpath_norm.endswith("/.env/"):
        ctx.prefilter_reject = f"hidden_file:{fname}"
        return

    # 5. 备份文件名
    fname_lower = fname.lower()
    for pat in _EXCLUDED_NAME_PATTERNS:
        if pat in fname_lower:
            ctx.prefilter_reject = f"backup_file:{pat}"
            return

    # 6. classification_exclusions（用户积累的排除规则）
    if storage:
        try:
            exclusion = storage.sqlite.check_exclusion(fp, ctx.content)
            if exclusion:
                ctx.prefilter_reject = f"exclusion_rule:{exclusion.get('rule_value', '')}"
                return
        except Exception as e:
            logger.debug("Exclusion check failed: %s", e)


# ═══════════════════════════════════════════════════════════
# PipelineContext
# ═══════════════════════════════════════════════════════════

@dataclass
class PipelineContext:
    """管线单文件处理的完整上下文。每个 step 读/写本对象。"""
    doc_id: int
    content: str
    filepath: str

    prefilter_reject: str = ""
    keyword_hint: Optional[tuple] = None
    llm_result: Optional[tuple] = None

    label: DocumentLabel = DocumentLabel.UNKNOWN
    importance: str = "P2"
    category: str = ""
    sub_category: str = ""
    depth: str = ""
    content_type: str = ""
    knowledge_type: str = ""
    applicability: str = ""
    summary: str = ""
    weight: int = 50
    entities: list = None

    def __post_init__(self):
        if self.entities is None:
            self.entities = []

    def should_skip(self) -> bool:
        return bool(self.prefilter_reject)

    def final_tier(self):
        return LABEL_TO_TIER.get(self.label, LABEL_TO_TIER[DocumentLabel.UNKNOWN])

    def to_set_classification_kw(self) -> dict:
        return dict(
            doc_id=self.doc_id,
            label=self.label,
            tier=self.final_tier(),
            weight=self.weight,
            importance=self.importance,
            compact_content=self.summary,
            content_category=self.category,
            sub_category=self.sub_category,
            depth=self.depth,
            tags=json.dumps([self.content_type, self.knowledge_type]),
        )

    def is_global_rule_candidate(self) -> bool:
        if not (self.applicability == "通用规则" and self.importance in ("P0", "P1") and self.summary):
            return False
        proj_patterns = [
            r'[A-Z]:\\[^\s]{10,}', r'~/\.\w+/\w+/\w+',
            r'\.claude/projects/', r'\.codex/', r'agent-hub/',
        ]
        return not any(re.search(p, self.summary) for p in proj_patterns)


# ═══════════════════════════════════════════════════════════
# ClassifyResult — 导出判定统一入口
# ═══════════════════════════════════════════════════════════

_USELESS_PREFIXES = [
    '该文档是', '本文档是', '本文描述了', '该文档描述了',
    '本文档描述了', '本文记录了', '该文档记录了',
    '本文档是一个', '本文是一份',
    '这是一篇', '这是一份', '该文档包含', '本文档包含',
    '文档是', '文档记录了', '本文记录了', '该文档记录了',
    '这篇文章', '本文档提供了', '记忆分类系统完整方案存档',
    '记录了', '---\nname:', '---\nname',
]
_META_VERBS = [
    '讨论了', '描述了', '整理了', '记录了', '介绍了',
    '阐述了', '概述了', '总结了', '列举了', '分析了',
    '提出了', '探讨了', '讲述了', '说明了',
    '概括了', '展示了', '明确了', '定义了', '论述了',
    '梳理了', '归纳了', '回顾了', '分享了',
    '讨论构建', '讨论', '描述', '整理', '记录',
    '介绍', '阐述', '概述', '总结', '列举', '分析',
    '提出', '探讨', '讲述', '说明', '概括', '展示',
    '明确', '定义', '论述', '梳理', '归纳', '回顾',
]
_META_DESC_EXPORT_PATTERNS = [
    r'定义了.{0,15}规则', r'定义了.{0,15}系统', r'定义了.{0,15}协议',
    r'讨论了.{0,15}方案', r'讨论了.{0,15}问题',
    r'该文档.{0,15}规则', r'本文档.{0,15}规则',
    r'该文档为.{0,20}索引', r'本文档为.{0,20}索引',
    r'列出了.{0,15}文档', r'列出.{0,15}标题',
    r'涵盖.{0,10}规则.{0,10}配置',
]


@dataclass
class ClassifyResult:
    """分类结果统一数据模型。export 只问 result.exportable。

    V7 新增字段（MW LLM Wiki 重构）：
    - related_memories: 交叉引用列表，供 /mw-ingest 写入 memory_cross_ref
    - fuse_target_id: 融合目标 doc_id，非0表示融合到已有记忆
    """
    doc_id: int
    file_path: str
    label: str
    importance: str
    weight: int
    summary: str
    category: str = ""
    sub_category: str = ""
    memory_tier: str = ""
    tags: str = ""
    create_time: str = ""
    namespace: str = "default"
    # V7 新增：交叉引用列表，格式 [{"related_doc_id": 12, "relation_type": "extend", "note": "..."}]
    related_memories: list = field(default_factory=list)
    # V7 新增：融合目标 doc_id，0 表示不融合（insert），非0表示融合到已有记忆（fuse）
    fuse_target_id: int = 0
    # V8 新增：行为进化层级 hot/warm/cold/archive，与 importance 正交
    # 默认 warm，Agent 通过 /mw-evolve 确认后才实际变更
    evolution_tier: str = "warm"

    @property
    def exportable(self) -> bool:
        if self.label in ('unknown', '') or not self.label:
            return False
        if self.importance not in ('P0', 'P1', 'P2'):
            return False
        if self.weight < 20:
            return False
        s = (self.summary or '').strip()
        if not s:
            return False
        return not is_useless_summary(s)

    def effective_summary(self, read_original_fn=None) -> str:
        """规则/计划类没摘要时尝试读原文件。"""
        s = (self.summary or '').strip()
        if s:
            return s
        if self.label in ('meta_rule', 'planning_doc') and read_original_fn:
            s = read_original_fn(self.file_path)
        return s or ''


def is_useless_summary(text: str) -> bool:
    """废话摘要黑名单：描述文档本身的内容不是可执行规则。"""
    if not text:
        return True
    t = text.strip()
    if len(t) < 15:
        return True
    for p in _USELESS_PREFIXES:
        if t.startswith(p):
            return True
    for v in _META_VERBS:
        if t.startswith(v):
            return True
    if t.startswith('name:') or t.startswith('description:'):
        return True
    for p in _META_DESC_EXPORT_PATTERNS:
        if re.search(p, t[:200]):
            return True
    return False


# ═══════════════════════════════════════════════════════════
# Steps
# ═══════════════════════════════════════════════════════════

def step_prefilter(ctx: PipelineContext) -> None:
    """短内容 / JSON日志 / 纯聊天 / 元描述 → reject。V10：阈值 50→10"""
    content = ctx.content
    filepath = ctx.filepath
    stripped = content.strip()

    if len(stripped) < 10:
        ctx.prefilter_reject = "content_too_short"
        return

    if stripped.startswith('{') and stripped.endswith('}'):
        try:
            json.loads(stripped)
            ctx.prefilter_reject = "json_log"
            return
        except (json.JSONDecodeError, ValueError):
            pass

    if stripped.startswith('[') and stripped.endswith(']'):
        try:
            json.loads(stripped)
            ctx.prefilter_reject = "json_array_log"
            return
        except (json.JSONDecodeError, ValueError):
            pass

    has_chat = any(m in content for m in CHAT_MARKERS)
    has_rule = any(s in content for s in RULE_SIGNALS)
    is_chat_history = "/chat-history/" in filepath.replace("\\", "/")
    if has_chat and not has_rule and not is_chat_history:
        ctx.prefilter_reject = "chat_no_rule_signal"
        return

    first_200 = content[:200]
    if any(re.search(p, first_200) for p in META_DESC_PATTERNS):
        if len(stripped) < 300:
            ctx.prefilter_reject = "meta_description"
            return


def step_keyword_hint(ctx: PipelineContext, use_keyword_filter: bool) -> None:
    """DynamicClassifier 关键词快速分类。"""
    if not use_keyword_filter:
        return
    try:
        from ..classifier import DynamicClassifier
        fast = DynamicClassifier()
        result = fast.classify(ctx.content, ctx.filepath)
        if result["confidence"] < 0.6 or result.get("needs_review"):
            return
        cat = result["category"]
        sub = result["sub_category"]
        kw_label = None
        if "Skill" in sub:
            kw_label = (DocumentLabel.META_RULE, "P1", cat, sub)
        elif "Agent" in sub or "规则" in cat or "规则" in sub:
            kw_label = (DocumentLabel.META_RULE, "P1", cat, sub)
        elif "工作流" in sub or "流程" in cat:
            kw_label = (DocumentLabel.PLANNING_DOC, "P1", cat, sub)
        elif "配置" in sub or "工具链" in sub or "安装" in sub:
            kw_label = (DocumentLabel.CONFIG_INVENTORY, "P1", cat, sub)
        elif "调试" in sub:
            kw_label = (DocumentLabel.SELF_IMPROVE_LEARN, "P1", cat, sub)
        elif "记忆" in sub or "参考" in cat:
            kw_label = (DocumentLabel.MEMORY_LAYER, "P2", cat, sub)
        elif "娱乐" in cat or "健康" in cat or "购物" in cat or "旅行" in cat:
            kw_label = (DocumentLabel.MEMORY_LAYER, "P2", cat, sub)
        if kw_label:
            ctx.keyword_hint = kw_label
            logger.info("Keyword hint: %s -> %s (conf=%.1f)", cat, kw_label[0], result["confidence"])
    except Exception:
        pass


def step_resolve_label(ctx: PipelineContext) -> None:
    """keyword hint 命中 → 直接作为分类结果（V10：无 LLM）。"""
    if ctx.label != DocumentLabel.UNKNOWN:
        return
    if ctx.keyword_hint:
        ctx.label, ctx.importance, ctx.category, ctx.sub_category = ctx.keyword_hint
        if not ctx.applicability:
            ctx.applicability = "场景知识"
        if not ctx.content_type:
            ctx.content_type = "知识文档"
        if not ctx.knowledge_type:
            ctx.knowledge_type = "通用参考"
        ctx.summary = (ctx.content or "")[:200]  # keyword 命中：原文前 200 字当 summary
        ctx.weight = 50  # keyword 命中 weight=50
        logger.info("Keyword classify: %s -> %s (conf=keyword)", ctx.doc_id, ctx.label)


def step_path_fallback(ctx: PipelineContext) -> None:
    """keyword 未命中时，用路径规则兜底。"""
    if ctx.label != DocumentLabel.UNKNOWN:
        return
    fp = ctx.filepath.lower().replace("\\", "/")
    ext = os.path.splitext(ctx.filepath)[1].lower()
    if ext in {".jsonl", ".json", ".log", ".lock", ".exe", ".bin"}:
        return

    is_memory_path = any(p in fp for p in _MEMORY_PATH_PATTERNS)
    if is_memory_path:
        for pattern, label, importance, category, sub_category in _PATH_RULES:
            if re.search(pattern, fp):
                ctx.label = label
                ctx.importance = importance
                ctx.category = category
                ctx.sub_category = sub_category
                logger.info("Path rule fallback: %s -> %s", ctx.filepath, label)
                return

    if fp.endswith(".jsonl"):
        ctx.label = DocumentLabel.CHAT_LOG
        ctx.importance = "P3"
        ctx.category = "AI专属类"
        ctx.sub_category = "工具链"


def step_domain_normalize(ctx: PipelineContext, domain_normalizer) -> None:
    """领域归一化（V10：domain_normalizer 可能为 None）"""
    if ctx.category and domain_normalizer:
        try:
            ctx.category, _ = domain_normalizer.normalize(ctx.category)
        except Exception as e:
            logger.debug("Domain normalize failed: %s", e)


def step_resolve_weight(ctx: PipelineContext) -> None:
    if ctx.applicability == "通用规则":
        ctx.weight = 95
    elif ctx.applicability == "场景知识":
        ctx.weight = 50
    else:
        ctx.weight = 20


# ═══════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════

class ClassifyPipeline:
    def __init__(self, llm_manager, storage, domain_normalizer=None, config=None):
        self.llm = llm_manager
        self.storage = storage
        self.domain_normalizer = domain_normalizer
        self.config = config
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="pipeline")
        self._splitter = DocumentSplitter(max_size=8000, min_size=50, overlap=200)

    def process_one(self, doc_id: int, content: str, filepath: str = "",
                    fast_lane: bool = False) -> Optional[PipelineContext]:
        ctx = PipelineContext(doc_id=doc_id, content=content, filepath=filepath)
        self._run_steps(ctx, fast_lane=fast_lane)
        if ctx.should_skip():
            logger.info("Prefilter reject: %s, doc_id=%s", ctx.prefilter_reject, doc_id)
            return ctx
        self._store(ctx, fast_lane=fast_lane)
        return ctx

    def process_batch(self, pending: list[tuple[int, str, str]],
                      fast_lane: bool = False) -> list[PipelineContext]:
        """V10 批量处理：hard_filter → prefilter → keyword → resolve_label → path_fallback → store"""
        if not pending:
            return []

        need_llm: list[PipelineContext] = []
        prefilter_hit = 0
        for doc_id, content, filepath in pending:
            # 大文档自动截取前 8000 字做分类（避免超长内容卡住关键词匹配）
            classify_content = content
            if len(content) > 8000:
                chunks = self._splitter.split(content)
                if chunks:
                    classify_content = "\n\n".join(c.content for c in chunks[:3])  # 取前 3 个 chunk
            ctx = PipelineContext(doc_id=doc_id, content=classify_content, filepath=filepath)
            step_hard_filter(ctx, self.storage)
            if ctx.should_skip():
                prefilter_hit += 1
                continue
            step_prefilter(ctx)
            if ctx.should_skip():
                prefilter_hit += 1
                continue
            need_llm.append(ctx)

        if prefilter_hit:
            logger.info("Batch filter: %d/%d skipped, %d remain",
                        prefilter_hit, len(pending), len(need_llm))
        if not need_llm:
            logger.info("Batch: all %d docs filtered", len(pending))
            return []

        # V10：keyword 分类（不再调 LLM）
        use_kw = (self.config and self.config.llm.classify.use_keyword_filter) if self.config else False
        for ctx in need_llm:
            if not fast_lane:
                step_keyword_hint(ctx, use_kw)

        # keyword 没命中的走 resolve_label + path_fallback（不再需要 LLM 步骤）
        results = []
        for ctx in need_llm:
            # 如果 keyword 没命中，用原文前 200 字当 summary，weight=20
            if not ctx.keyword_hint:
                ctx.summary = (ctx.content or "")[:200]
                ctx.weight = 20  # 通过预筛但没匹配到任何分类 → weight=20

            self._run_post_llm(ctx)

            # 如果 keyword 命中，summary 已在 step_resolve_label 中设置
            if ctx.summary:
                old_cls = self.storage.sqlite._conn.execute(
                    "SELECT compact_content FROM memory_classify WHERE doc_id=?", (ctx.doc_id,)
                ).fetchone()
                if old_cls and old_cls[0]:
                    ctx.summary = old_cls[0]
                    logger.info("Empty summary for doc %d, kept previous", ctx.doc_id)

            self._store(ctx, fast_lane=fast_lane)
            results.append(ctx)

        logger.info("Batch complete: %d/%d classified (prefilter=%d, keyword=%d)",
                    len(results) + prefilter_hit, len(pending), prefilter_hit,
                    sum(1 for r in results if r.keyword_hint))
        return results

    def _run_steps(self, ctx: PipelineContext, fast_lane: bool = False) -> None:
        """V10 管线：hard_filter → prefilter → keyword → resolve_label → path_fallback → resolve_weight"""
        step_hard_filter(ctx, self.storage)
        if ctx.should_skip():
            return
        step_prefilter(ctx)
        if ctx.should_skip():
            return
        if not fast_lane:
            use_kw = (self.config and self.config.llm.classify.use_keyword_filter) if self.config else False
            step_keyword_hint(ctx, use_kw)
        self._run_post_llm(ctx)

    def _run_post_llm(self, ctx: PipelineContext) -> None:
        """V10：keyword → resolve_label → path_fallback → resolve_weight（无 LLM/domain）"""
        step_resolve_label(ctx)
        step_path_fallback(ctx)
        step_resolve_weight(ctx)

    def _store(self, ctx: PipelineContext, fast_lane: bool = False) -> None:
        kw = ctx.to_set_classification_kw()
        self.storage.sqlite.set_classification(**kw)

        # V10 Phase 2: weight=20 的文档自动入队审核
        if ctx.weight == 20:
            reason = "keyword_miss" if not ctx.keyword_hint else "low_confidence"
            self.storage.sqlite.enqueue_for_review(ctx.doc_id, reason)

        if not fast_lane and ctx.is_global_rule_candidate():
            index_hint = _build_index_hint(ctx.summary, ctx.category or "")
            scores = _infer_rule_scores(
                ctx.applicability, ctx.importance, ctx.depth,
                ctx.category or "", ctx.sub_category or "",
            )

            # ── P5.1 冲突检测（V10：需要 LLM，暂跳过） ──
            conflict_ids = []
            complement_ids = []
            evolution_note = ""
            parent_rule_id = None

            new_rule_id = self.storage.sqlite.add_global_rule(
                rule_text=ctx.summary,
                category=ctx.category or "rule",
                sub_category=ctx.sub_category or "behavior",
                scope="global",
                priority="high" if ctx.importance == "P0" else "normal",
                source_doc_id=ctx.doc_id,
                max_tokens_budget=ctx.weight,
                index_hint=index_hint,
                rule_type=scores["rule_type"],
                score_universality=scores["uni"],
                score_cost=scores["cost"],
                score_actionable=scores["actionable"],
                score_timeliness=scores["timeliness"],
                conflict_with=json.dumps(conflict_ids),
                complements=json.dumps(complement_ids),
                parent_rule_id=parent_rule_id,
                skip_gate=True,
            )
            self.storage.sqlite.set_memory_scope(
                ctx.doc_id, scope="global", rule_text=ctx.summary,
            )

            # ── 双向冲突写入 ──
            if conflict_ids and new_rule_id > 0:
                for cid in conflict_ids:
                    self.storage.sqlite._conn.execute(
                        "UPDATE global_rules SET conflict_with = json_insert(conflict_with, '$[#]', ?) WHERE id = ?",
                        (new_rule_id, cid),
                    )
                self.storage.sqlite._conn.commit()

            # ── P5.2 任务生成 ──
            if not conflict_ids and new_rule_id > 0:
                evidence_count = self.storage.sqlite._conn.execute(
                    "SELECT COUNT(*) FROM memory_classify c "
                    "WHERE c.content_category LIKE ? AND c.compact_content != ''",
                    (f"%{ctx.category}%",),
                ).fetchone()[0]

                if evidence_count < 3 and ctx.importance in ("P1", "P2"):
                    self.storage.sqlite.create_task(
                        task_type="verify_rule",
                        source_rule_id=new_rule_id,
                        source_doc_id=ctx.doc_id,
                        description=f"验证规则：{ctx.summary[:100]}",
                        min_evidence=3,
                    )

            # ── P5.2 证据匹配 ──
            if ctx.is_global_rule_candidate() and ctx.summary:
                pending = self.storage.sqlite.get_pending_tasks(task_type="verify_rule")
                for task in pending:
                    if task["source_rule_id"]:
                        rule = self.storage.sqlite._conn.execute(
                            "SELECT category FROM global_rules WHERE id=?", (task["source_rule_id"],),
                        ).fetchone()
                        if rule and rule["category"] == ctx.category:
                            result = self.storage.sqlite.update_task_evidence(task["id"], ctx.doc_id)
                            if result["upgraded"]:
                                logger.info("Task %d auto-confirmed (%d docs, conf=%.1f)",
                                            task["id"], result["evidence_count"], result["confidence"])

        if ctx.category:
            self.storage.sqlite.increment_domain_count(ctx.category)

        if self.llm and self.llm.has_embed_model:
            try:
                vector = self.llm.embed(truncate_tokens(ctx.content, 8000))
                if vector:
                    row = self.storage.sqlite.get_file_path_and_time(ctx.doc_id)
                    if row:
                        self.storage.vector.upsert(
                            doc_id=ctx.doc_id,
                            file_path=row["file_path"],
                            vector=vector,
                            label=ctx.label.value,
                            memory_tier=ctx.final_tier().value,
                            create_utc=row["create_time"],
                        )
            except Exception as e:
                logger.debug("Vector upsert failed for doc %d: %s", ctx.doc_id, e)

        if ctx.entities:
            self.storage.sqlite.save_entities(ctx.doc_id, ctx.entities)

        # V11: 入库后自动关联——同分类 + 共享实体 → memory_cross_ref
        if ctx.category and not ctx.should_skip():
            try:
                entity_names = [e.get("name", "") for e in (ctx.entities or []) if e.get("name")]
                self.storage.sqlite.auto_cross_ref(
                    ctx.doc_id,
                    category=ctx.category,
                    entity_names=entity_names if entity_names else None,
                    top_k=3,
                )
            except Exception as e:
                logger.debug("auto_cross_ref failed for doc %d: %s", ctx.doc_id, e)

        if ctx.summary:
            self.storage.sqlite._conn.execute(
                "INSERT INTO memory_fts(doc_id, title, summary, content_category, sub_category) VALUES (?, ?, ?, ?, ?)",
                (ctx.doc_id, (ctx.summary or "")[:200], ctx.summary or "", ctx.category or "", ctx.sub_category or ""),
            )


# ═══════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════

def _build_index_hint(rule_text: str, category: str) -> str:
    words = re.findall(r'[一-鿿]{2,}|[a-zA-Z]{3,}', rule_text)
    top_words = list(dict.fromkeys(words))[:8]
    return json.dumps({
        "keywords": top_words,
        "category": category,
        "load_strategy": "keyword" if len(top_words) >= 3 else "general",
    })


def _infer_rule_scores(applicability: str, importance: str, depth: str,
                       category: str, sub_category: str) -> dict:
    scores = {"uni": 3, "cost": 2, "actionable": 3, "timeliness": 3, "rule_type": "knowledge"}
    if applicability == "通用规则":
        scores["uni"] = 4 if importance in ("P0",) else 3
        scores["cost"] = 3
        scores["actionable"] = 4
        scores["rule_type"] = "standard"
    elif applicability == "场景知识":
        scores["uni"] = 3
        scores["cost"] = 2
        scores["actionable"] = 3
    else:
        scores["uni"] = 2
        scores["actionable"] = 2
        scores["timeliness"] = 2

    if "行为规则" in category or "规则" in sub_category:
        scores["actionable"] = max(scores["actionable"], 4)
        scores["rule_type"] = "standard"
    elif "Skill" in sub_category or "Skill" in category:
        scores["actionable"] = max(scores["actionable"], 3)
        scores["uni"] = max(scores["uni"], 3)
        scores["rule_type"] = "domain"
    elif "Agent配置" in sub_category or "Agent配置" in category:
        scores["rule_type"] = "index"
    elif "参考" in category or "通用" in category:
        scores["actionable"] = min(scores["actionable"], 2)
        scores["rule_type"] = "index"

    if importance in ("P3", "P4"):
        scores["timeliness"] = 2

    return scores
