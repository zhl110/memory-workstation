# Changelog

## 1.1.0 — 2026-07-10

- **Skill 全面重构**：268行→166行，渐进式披露（SKILL.md 路由 + references/ 详情）
- **新增 link 命令**：`mw link <source_id> <target_id> --weight 2.0` 创建加权记忆关联
- **加权图谱遍历**：cross_ref 新增 weight 列，graph-traverse 优先走权重高的链接
- **记忆思维指南**：什么值得记/不值得记 + 分类决策树 + 搜索优先协议 + 质量标准
- **反思框架**：四步法（回顾→提炼→整理→展望）+ 质量检查清单
- **references/ 新增**：import-guide.md、query-guide.md、reflection-framework.md、memory-taxonomy.md
- **过滤冗余功能**：scene/emotion/session 不再暴露给 Agent（桌面端专用）
- **保留知识图谱**：cross_ref + --graph 搜索，Agent 显式关联提升链接质量

## 0.22.2 — 2026-07-10

- **加权 RRF**：search_rrf/search_hybrid 支持三路权重（config_.weights 生效），默认 FTS5=0.2/Entity=0.2/Vector=0.6
- **BM25 归一化**：fts_search 返回分数归一化到 [0,1]，与 entity/vector 分数量纲一致
- **搜索截断修正**：explain → scene 过滤 → top_k 截断，图谱展开结果不被误丢
- **访问记录统一**：search() 两条路径（有/无向量）统一在 Python 层调用 record_access_batch
- **C++ schema 补齐**：9 个缺失字段（stability/confidence/source 等）幂等迁移
- **线程安全**：audit.py 加 threading.Lock；embedding_engine loaded_ 改为 atomic<bool>；hnsw_ 数据竞争修复
- **空指针防护**：sqlite3_prepare_v2 返回值检查（storage_evolution/storage_ingest）
- **Python 清理**：删除 4 个未使用 import + 2 个重复 local import + sync.py 连接复用 + scene.py 延迟导入
- **CLI 修复**：crossref 跳过逻辑改为 UNION 出边+入边；reorganize 优先用 content_category 推断 label
- **Skill 修复**：mw-sdk-smoketest mode='legacy'→'rrf'，API 调用修正

## 0.22.1 — 2026-07-10

- **title 字段**：memory_classify 新增 title 列，ingest 时必须提供 `--title`（一句话概括核心内容，不是截取开头）
- **导出 frontmatter 补全**：新增 keywords/summary/memory_tier/memory_type/stability/confidence/project 字段
- **导出去重**：正文第一行和标题重复时自动去掉标题行
- **C++ ingest title 提取**：优先提取 `**内容**：` 后面文本，跳过通用标签（规则/决策/配置...）
- **记忆质量治理**：删除测试数据/空壳记忆，补全不完整规则，修复标题正文重复
- **会话总结**：新增第五节"会话总结"，定义自动从对话上下文总结改动/经验/决策并结构化写入 MW 的流程
- **操作路由**：新增第零节"操作路由"，明确读/写/总结/维护四种操作的触发信号和执行路径

## 0.14.0 — 2026-07-07

- **搜索扩面**：`mw search` 新增 `--extra` 参数，支持额外关键词列表（OR 语义扩大覆盖）
- **Dedup 修复**：修复 dedup 跨调用污染 bug（连续搜索同一关键词不再丢失结果）
- **场景→关联词映射**：新增扩展关键词使用指南，覆盖部署/调试/架构/数据库/配置等场景
- **搜索模式提示**：新增 --graph 使用场景表（全面了解/结果少于3条时启用）
- **防抖机制**：自动触发信号 5 分钟内同模式只记录一次，避免重复记忆
- **权重冲突规则**：同层级同重要性时以最后一次为准，保留旧记录供追溯
- **写入前脱敏**：mw ingest 前检查密钥模式（AKIA/sk-/Bearer/ghp_），替换为 [REDACTED]

## 0.13.0 — 2026-06-30

- **v0.7.0 SDK 同步**：模块清单新增 `graph.py`（Dijkstra/BFS/图谱遍历/健康度）、`vector.py`（sentence-transformers/向量索引/预加载）、`migration.py`（v1→v2迁移）
- **统一搜索融合**：搜索方法新增 `enable_vector` 和 `enable_graph` 参数，支持四路融合（FTS5+Entity+Vector+Graph）
- **搜索模式**：新增 `mode="rrf"`（RRF融合）和 `mode="hybrid"`（RRF+Ebbinghaus遗忘曲线）
- **CLI 新增命令**：`graph-traverse`（图谱遍历）、`vector-build`（构建向量索引）、`vector-preload`（预加载模型）、`migrate`（数据迁移）
- **搜索引擎内部机制更新**：权重公式从 `bm25×0.7+entity×0.3` 升级为 `bm25×0.5+entity×0.3+vector×0.2`

## 0.12.0 — 2026-06-29

- **结构重组**：分为"第一部分：如何使用"+"第二部分：技术参考"，实用在前理论在后
- **精简 35%**：655 行 → 427 行，去掉冗余重复，保留所有核心内容
- **分类速查表**：新增 label/importance/applicability 参考标准 + 举例
- **注意事项修正**："不要只搜一次"改为"搜不到就降级"
- **快速参考修正**：统一用 mw ingest，移除旧方法引用
- **备份功能补全**：维护命令新增 m.backup()
- **自动加载机制**：新增"每次会话必须执行"协议 — `get_always_load(5)` 加载核心记忆 + `search_rules_by_intent(intent)` 按意图加载全局规则，合并到上下文
- **特殊功能补充**：新增大池子自动补、scan_mentions 隐式链接、correction→evolve→promote 自我学习闭环、Obsidian 知识图谱导出、多 Agent 独立数据库
- **搜索结果使用指南**：新增"搜索结果怎么用"章节，4 种场景（直接引用/展开关联/跳过/降级）
- **缺失 CLI 命令补全**：新增 mw list/mw rules/mw entities/mw rules-search/mw reindex/mw rebuild-fts5
- **mw ingest 完整参数**：列出全部 12 个参数及默认值
- **完整使用场景**：新增 6 个端到端示例（记住/问问题/展开关联/降级/反思/自动加载）
- **搜索决策树精简**：Agent 自由选择搜索方法（search/get_all_related/get_linked），不再强制从普通搜索开始；禁止自己写 SQL；search 不够直接降级图谱
- **自动降级机制**：普通搜索 search 连续 3 组关键词无好结果时，自动升级到 get_all_related 图谱搜索，顺着关联找间接记忆
- **多轮检索策略**：3 组关键词逐组尝试，score 阈值判断（>0.5 有用 / >0.3 部分有用 / <0.3 换词），连续失败告知用户
- **全面重写 SKILL.md**：基于 SDK 源码逐行分析，每个方法的参数、返回值、内部流程完全对齐
- **新增完整方法清单**：50+ 公开方法全部列出，含 `insert_classified`/`update_memory`/`bulk_insert_classified`/`auto_cross_ref`/`scan_mentions`/`crawl_cross_ref`/`export_jsonl`/`backup`/`get_stats`/`register_agent`/`set_always_load` 等
- **搜索机制精确描述**：BM25 权重配置（title=1.0, summary=5.0, content_category=3.0, sub_category=2.0），Entity OR 组合搜索，7天 boost ×1.3，大池子 LIKE fallback
- **交叉引用系统完整说明**：auto_cross_ref 两路数据源、scan_mentions 机制、relation_type 7 种类型、_find_cross_ref_candidates 两种策略
- **分类格式完整定义**：classification 字典所有字段、label 7 种值、applicability→weight 映射
- **写入流程内部细节**：insert_classified 8 步、ingest_full 8 步、安全/审计/编码校验链路
- **13 张表完整列出**：document_files/memory_classify/memory_entity/memory_fts/memory_cross_ref/lint_log/global_rules/memory_access_record/evolution_log/correction_log/tier_history/system_meta
- **健康度检测逻辑精确**：每项检测的具体判定条件（SequenceMatcher ratio>0.85、正则+共同词汇>5、180天过期）
- **导出功能详细说明**：Obsidian 格式结构（frontmatter+双链+MOC+.obsidian）、JSONL 格式

## 0.10.0 — 2026-06-29

- **SKILL.md 重构**：568 行精简至 ~200 行，提升可读性和使用体验
- **搜索描述修正**：从"FTS5+Agent Rerank"更正为"FTS5+Entity 融合（BM25×0.7 + Entity×0.3）"
- **知识图谱能力补全**：补充 get_linked/scan_mentions/auto_cross_ref 说明
- **新增快速参考表**：顶部 10 秒上手指南
- **触发词整合**：所有触发词集中到末尾速查表
- **架构图简化**：用更清晰的文本图展示 Agent ↔ SDK ↔ SQLite 关系
- **冗余内容清理**：移除重复的 API 说明，详细内容保留在 references/

## 0.9.0 — 2026-06-29

- **全局规则自动加载**：新增 `search_rules_by_intent()`，意图驱动规则搜索（code/deploy/config/architecture/debug/general）
- **知识库整理**：新增 `rebuild_links()` 重建关联 + `cleanup_memories()` 清理测试数据
- **双防线编码修复**：新增 `validate_utf8()` + `safe_truncate()`，修复5处截断风险+3处写入校验
- **CLI 新增命令**：`mw stats`（进化统计）、`mw rebuild-links`（重建关联）、`mw cleanup`（清理数据）、`mw rules-search`（规则搜索）
- **Bug修复**：cleanup 缩进错误导致误标记188条记录，已修复并恢复
- **表名修正**：memory_access_log → memory_access_record
- **SKILL.md 更新**：操作零流程新增 [3.5] 自动加载全局规则，版本升至 v0.9.0

## 0.7.0 — 2026-06-28

- **Schema升级**：`namespace` 改名为 `workspace_id`（多项目隔离），新增 `memory_type`（session/project/global/cc四层分类）、`create_time` 字段
- **密钥脱敏**：新增 `security.py`，自动检测并脱敏API密钥（sk-/AKIA等格式）
- **写入审计**：新增 `audit.py`，记录所有写入操作，支持日志轮转（默认10MB）
- **批量写入**：新增 `bulk_insert_classified()` 方法，每100条提交一次，提升大批量写入性能
- **类型定义**：新增 `types.py`，提供 `ClassificationDict`、`SearchResult`、`MemoryDict` 类型
- **单元测试**：新增 `tests/` 目录，10个测试用例覆盖CRUD、搜索、边界条件
- **CLI参数**：`mw ingest` 新增 `--workspace` 和 `--memory-type` 参数
- **Rerank改造**：删除 `rerank.py`，`search_with_rerank()` 参数从 `llm_callable` 改为 `rerank_fn`

## 0.6.0 — 2026-06-27

- **FTS5 compact_content 修复**：`insert_classified()` 和 `update_memory()` 补齐 FTS5 写入缺失的 compact_content 列，搜索命中率大幅提升
- **Rerank 语义重排序**：新增 `rerank.py`（SDK Reranker + llm_callable 注入模式），`mw search --rerank` 支持语义排序
- **Lint 五项检测**：新增 `lint.py`，纯 SQL+正则+diff 算法实现孤页/断链/重复/矛盾/过期检测（零 token）
- **CLI 补齐**：`mw reflect`, `mw evolve`（含 --apply/--tier-only/--pattern/--cold-days）, `mw log`（含 --type 过滤）, `mw index`（含 --category 分类统计）
- **大池子搜索统一**：`_search_pool()` 优先 FTS5 MATCH，无 FTS5 则 LIKE fallback
- **文档同步**：所有命令描述从 `/mw-xxx` 改为 `mw xxx`，功能边界表更新 Rerank/Lint 列
- **SKILL.md 更新**：操作说明全部对齐实际命令，版本升至 v0.6.0

## 0.5.0 — 2026-06-26

- **Layer 0 双脑身份**：MW 定义为 Agent 的长期记忆脑区，检索结果格式化"你回忆起…"，`<need_memory>`手势支持
- **操作零**：意图驱动自动检索，不再依赖 Hook 的机械匹配
- **Phase 1 Core Memory**：`set_always_load`/`get_always_load`/`clear_always_load` ≤5硬限制，PostInit Hook 自动加载
- **Phase 2 会话快照**：轮次计数器满10轮 → 📋 DIGEST_OUTDATED，跨 compact 持久化
- **Phase 3 知识自动归档**：compact 后自动检测有价值产出 → ingest
- **Phase 4 星级遗忘自评**：Agent 评估低频记忆（保留/展示/推荐删除）
- **SDK meta 列**：memory_classify 新增 meta JSON 字段，承载 always_load 属性
- **删除误创建 stub 文件**：log.md / memory_index.md / lint_report.md（mw-sdk 目录下）

- **池子重构**：大池子改为自动连接，search() 自动兜底（不够时自动去图书馆补），不再需要 `--pool` 参数和 `set_pool_path()`
- **移除旧方法**：删除 `set_pool_path()` / `search_pool()` / `search_all()`，池子不标记来源
- **CLI 默认库切换**：`mw` CLI 默认库改为 `meta_claude.sqlite`
- **新增 CLI 命令**：`mw list` 按分类列出记忆，`mw export` 导出为 MD 文件
- **改名**：`mw-llm-wiki` → `Only-MW-zhl`

## 0.4.0 — 2026-06-22

- SDK 改为纯数据引擎，剥离所有 LLM 调用
- `add()` → `insert_classified()`，上层 Agent 自己做分类再写入
- 新增 `update_memory()` / `insert_cross_refs()` / `get_linked()` / `export_jsonl()` / `backup()`
- 删除 `SDKLLM` 整类、`llm.py`、`requests` 依赖、`lancedb` 依赖
- schema 新增 `memory_cross_ref` / `lint_log` / `global_rules` 表

## 0.3.0 — 2026-06-20

- V7 知识复合：fuse or insert 决策 + 交叉引用 + 健康度 Lint
- 知识图谱式检索：index.md 路由 + FTS5 粗筛 + Claude Rerank
- V8 行为进化：reflect / evolve / log / 纠正检测
- 7 步 ingest 流水线

## 0.2.0 — 2026-06-15

- V3 架构重构：导入 mw-sdk，SQLite + FTS5 全文索引
- CLI `mw search` 命令
- 三层检索：记忆→规则→实体

## 0.1.0 — 2026-06-10

- 初版发布
- 基础 ingest + search 功能
- 直连 MW 数据库做关键词搜索
