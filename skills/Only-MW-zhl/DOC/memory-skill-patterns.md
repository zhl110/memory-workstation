# 记忆 Skill 设计模式总结

> 背景参考文档，非操作参考。基于 58 个本地 skill + Claude Code 官方文档 + 市面方案的综合分析。

---

## 一、市场上的记忆方案对比

### 1.1 Claude Code 官方（CLAUDE.md + Auto Memory）

**架构**：
- CLAUDE.md：用户写的持久指令（编码标准、工作流、架构决策）
- Auto Memory：Claude 自己写的笔记（构建命令、调试洞察、偏好）

**核心设计**：
- MEMORY.md 作为索引，前 200 行/25KB 加载到每次会话
- 详细笔记放独立主题文件（debugging.md、patterns.md），按需读取
- 每个项目独立目录：`~/.claude/projects/<project>/memory/`
- 所有 worktree 共享同一个 memory 目录

**写入决策**：
> "Claude doesn't save something every session. It decides what's worth remembering based on whether the information would be useful in a future conversation."

**关键启示**：
- 索引 + 详情分离（MEMORY.md 索引，主题文件详情）
- 按需加载（不是全量加载）
- 用户可审计/编辑（纯 markdown）

### 1.2 Claude Memory Pro（本地 skill）

**架构**：5 层记忆
1. 热记忆（当前对话）
2. 会话记忆（session_YYYY-MM-DD.md）
3. 长期记忆（typed/user/feedback/project/reference）
4. 实体银行（entities/procedures）
5. 反思层（reflections/）

**核心设计**：
- 4 类记忆分类：user / feedback / project / reference
- Token 预算管理（75% 警告、90% 完成检测）
- 边际收益检测（continuationCount >= 3 && delta < 500）

**写入决策树**：
```
用户给反馈/纠正
├─ 关于用户本身 → typed/user/
├─ 关于工作方式 → typed/feedback/
├─ 关于项目 → typed/project/
└─ 关于外部系统 → typed/reference/
```

**不保存清单**：
- 代码模式（可从代码推导）
- Git 历史（git log 权威）
- 临时任务状态（用 tasks）
- 已文档化的内容（CLAUDE.md）
- 未经证实的推断
- 整段对话记录
- 大段代码

### 1.3 Self-Improvement（本地 skill）

**架构**：3 个日志文件
- LEARNINGS.md（LRN-）：纠正、洞察、知识缺口
- ERRORS.md（ERR-）：命令失败、集成错误
- FEATURE_REQUESTS.md（FEAT-）：用户请求的功能

**核心设计**：
- 结构化条目格式（Priority / Status / Area / Metadata）
- 检测触发器（什么信号触发什么类型的记录）
- 状态生命周期（pending → resolved/in_progress/wont_fix/promoted）
- 晋升路径（本地学习 → CLAUDE.md/AGENTS.md 全局规则）

**检测触发器**：
| 信号 | 记录到 | 类型 |
|------|--------|------|
| "No, that's not right..." | LEARNINGS.md | correction |
| "Can you also..." | FEATURE_REQUESTS.md | feature |
| 命令返回非零 | ERRORS.md | error |
| 用户提供了你不知道的信息 | LEARNINGS.md | knowledge_gap |

### 1.4 Proactive Agent（本地 skill）

**核心设计**：
- WAL 协议：关键细节先写再回复（Write-Ahead Logging）
- 工作缓冲区：上下文丢失时的恢复机制
- 增长循环：自我改进的闭环

**WAL 协议**：
> 在响应用户之前，先把关键信息写入文件。这样即使上下文被压缩，信息也不会丢失。

### 1.5 Self-Improving（本地 skill）

**架构**：6 文件记忆
- memory.md（热记忆）
- index.md（索引）
- corrections.md（纠正记录）
- projects/（项目记忆）
- domains/（领域记忆）
- archive/（归档）

**核心设计**：
- 模式晋升：同一模式出现 3 次后晋升为规则
- 自我反思协议：结构化日志格式
- 心跳系统：定期维护

---

## 二、关键设计模式

### 2.1 索引 + 详情分离

**模式**：一个小索引文件加载到每次会话，详细内容放独立文件按需读取。

**为什么重要**：
- 索引小 → 消耗少 → 遵循率高
- 详情按需 → 不浪费上下文
- 用户可审计 → 透明可控

**MW 现状**：✅ `mw index` + `memory_index_agents.md`

### 2.2 信号驱动自动捕获

**模式**：定义明确的触发信号，Agent 看到信号自动记录，不需要用户说"记住"。

**为什么重要**：
- 用户不会每次都记得说"记住这个"
- 纠正/偏好是最有价值的记忆，必须捕获
- 自动化减少用户负担

**MW 现状**：✅ 信号表（纠正/偏好/经验）+ 不保存清单

### 2.3 分类决策树

**模式**：给 Agent 一个清晰的决策树，看到内容后自动判断分类。

**为什么重要**：
- 分类影响搜索效率（错误分类 = 搜不到）
- 分类影响作用域（global vs project）
- 决策树比规则表更易执行

**MW 现状**：✅ `memory-taxonomy.md` 有四、分类决策树

### 2.4 不保存清单

**模式**：明确列出什么不值得记，防止记忆膨胀。

**为什么重要**：
- 记忆太多 = 搜索噪音
- 有些信息已有权威来源（git log、CLAUDE.md）
- 一次性信息不值得持久化

**MW 现状**：✅ `import-guide.md` 的过滤条件中有"不需要保存"小节

### 2.5 搜索优先协议

**模式**：写入前必须先搜索，决定是合并还是新建。

**为什么重要**：
- 防止重复记忆
- 保持记忆质量（合并 > 新建）
- 减少搜索噪音

**MW 现状**：✅ SDK 内置 `_dedup_check`（基于 FTS5 + embedding 相似度自动去重）

### 2.6 质量标准

**模式**：定义"什么是好记忆"的标准，Agent 写入时对照检查。

**为什么重要**：
- 差的记忆 = 搜不到 + 占空间
- 好的记忆 = 可搜索 + 有上下文 + 可操作

**MW 现状**：✅ `import-guide.md` 质量自检清单 + 写入强制规则

### 2.7 晋升路径

**模式**：本地学习可以晋升为全局规则。

**为什么重要**：
- 重复出现的模式 = 应该成为规则
- 规则比经验更有约束力
- 防止同一错误反复出现

**MW 现状**：✅ `mw promote`（project→global）+ `mw evolve --apply`（识别稳定模式后自动晋升）

---

## 三、设计差异（MW vs 纯文件方案）

与基于纯 markdown 文件的记忆方案（Claude Code Auto Memory、Self-Improvement 等）相比：

| 维度 | 纯文件方案 | MW（SQLite + FTS5 + Vector） |
|------|-----------|------------------------------|
| 搜索 | grep / Read 逐个文件 | FTS5 全文搜索 + Entity 权重 + Vector 语义 |
| 去重 | 手动判断 | `_dedup_check` 自动合并 |
| 分类 | 目录结构 | label/scope/importance 结构化字段 |
| 关联 | 手动维护 | `` `cross_ref` `` 自动关联 |
| 晋升 | 手动 copy | `mw promote` 命令 |
| 维护 | 手动整理 | `mw evolve` / `mw decay` / `mw cleanup` |
