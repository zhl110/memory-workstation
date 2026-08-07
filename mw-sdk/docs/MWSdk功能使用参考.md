# MWSdk 功能使用参考图

## 🏗️ 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                      上层（有脑子）                           │
│                                                             │
│  skill (Claude Code)          exe (桌面软件)                 │
│  ├─ Claude 本人 = LLM        ├─ 调远端 LLM API              │
│  ├─ classify / fuse / rerank ├─ classify / fuse / rerank    │
│  ├─ lint / 维护 index.md     ├─ GUI / Tray / 自动扫描       │
│  └─ 存入 meta_claude.sqlite  └─ 存入 meta.sqlite            │
│  Codex / 其他 Agent                                     │
│  ├─ Codex 本人 = LLM                                    │
│  └─ 存入 meta_codex.sqlite                              │
└──────────────┬──────────────────────────────────────────────┘
               │ import
               ▼
┌──────────────────────────────────────────────────────────────┐
│                   sdk（纯数据引擎，没脑子）                     │
│                                                              │
│  读：search() / get_rules() / get_entities()                 │
│  写：update_memory()                                         │
│  管理：init_schema() / export_md() / export_jsonl()          │
│  生命周期：register_agent() / unregister_agent()              │
│           list_agents() / get_agent()                        │
└──────────────────────────────────────────────────────────────┘
```

## 📊 功能模块分类

### 1️⃣ 初始化模块

| 方法 | 功能 | 返回值 | 示例 |
|------|------|--------|------|
| `init_schema()` | 建全部表（幂等） | None | `m.init_schema()` |

### 2️⃣ 读取方法

| 方法 | 功能 | 参数 | 返回值 | 示例 |
|------|------|------|--------|------|
| `search(query, top_k, explain)` | 全文搜索记忆 | query: 关键词<br>top_k: 返回条数(默认10)<br>explain: 是否返回匹配详情 | list[dict] | `results = m.search("打包 部署")` |
| `get_all_related(query, top_k, max_results)` | 获取所有相关记忆（直接+间接） | query: 关键词<br>top_k: 直接搜索返回数量<br>max_results: 最终返回上限 | list[dict] | `memories = m.get_all_related("打包")` |
| `get_memory(doc_id)` | 读单条完整内容 | doc_id: 文档ID | dict 或 None | `memory = m.get_memory(42)` |
| `get_linked(doc_id)` | 读交叉引用 + 顺藤摸瓜 | doc_id: 源文档ID | list[dict] | `linked = m.get_linked(42)` |
| `get_rules(category, limit)` | 读全局规则 | category: 分类过滤<br>limit: 返回上限 | list[dict] | `rules = m.get_rules("打包部署")` |
| `get_entities(name, limit)` | 读实体列表 | name: 实体名过滤<br>limit: 返回上限 | list[dict] | `entities = m.get_entities("打包")` |
| `get_always_load(limit)` | 获取 always_load 记忆 | limit: 返回上限(最多5) | list[dict] | `always = m.get_always_load(5)` |

### 3️⃣ 写入方法

| 方法 | 功能 | 参数 | 返回值 | 示例 |
|------|------|------|--------|------|
| `update_memory(doc_id, summary, importance, weight)` | 融合时更新已有记忆 | doc_id: 文档ID<br>summary: 新摘要<br>importance: 新重要性<br>weight: 新权重 | bool | `m.update_memory(42, "新摘要", "P1", 80)` |
| `insert_cross_refs(doc_id, refs)` | 批量写入交叉引用 | doc_id: 源文档ID<br>refs: 关联列表 | int (成功条数) | `m.insert_cross_refs(42, refs)` |
| `auto_cross_ref(doc_id, candidates, relation_type, top_k, scan_mentions)` | 基于候选列表批量建双向交叉引用 | doc_id: 源文档ID<br>candidates: 候选结果列表<br>relation_type: 关联类型<br>top_k: 关联前几条<br>scan_mentions: 是否启用mention扫描 | int (双向边数) | `n = m.auto_cross_ref(42, candidates)` |
| `crawl_cross_ref(top_k, max_docs, incremental, scan_mentions)` | 批量全量/增量扫描所有记忆 | top_k: 每条记忆关联前几条候选<br>max_docs: 处理上限<br>incremental: 增量模式<br>scan_mentions: 是否启用mention扫描 | dict | `stats = m.crawl_cross_ref()` |
| `set_always_load(doc_id, enabled)` | 设为 always_load | doc_id: 文档ID<br>enabled: 是否启用 | None | `m.set_always_load(42, True)` |
| `clear_always_load(doc_id)` | 清除 always_load | doc_id: 文档ID(可选) | None | `m.clear_always_load(42)` |

### 4️⃣ 导出方法

| 方法 | 功能 | 参数 | 返回值 | 示例 |
|------|------|------|--------|------|
| `export_md(output_dir)` | 导出标准 Obsidian Vault | output_dir: 输出目录路径 | int (导出文件数) | `count = m.export_md("D:/exports/obsidian")` |
| `export_jsonl(output_file)` | 导出 JSONL 逐行格式 | output_file: 输出文件路径 | int (导出条数) | `count = m.export_jsonl("D:/exports/memories.jsonl")` |

### 5️⃣ 管理方法

| 方法 | 功能 | 参数 | 返回值 | 示例 |
|------|------|------|--------|------|
| `backup(backup_dir)` | 备份当前数据库 | backup_dir: 备份目录路径 | bool | `m.backup("D:/backups")` |
| `get_stats()` | 统计信息 | None | dict | `stats = m.get_stats()` |
| `get_correction_pending(min_count)` | 获取待确认纠正 | min_count: 最小记录次数 | list[dict] | `pending = m.get_correction_pending(3)` |
| `get_own_candidates()` | 获取冷热候选 | None | dict | `candidates = m.get_own_candidates()` |
| `apply_tier_change(doc_id, new_tier)` | 应用层级变更 | doc_id: 文档ID<br>new_tier: 新层级 | None | `m.apply_tier_change(42, "cold")` |
| `increment_correction(pattern, summary, context)` | 记录纠正模式 | pattern: 模式<br>summary: 摘要<br>context: 上下文 | None | `m.increment_correction("打包前未验证语法", "第2次记录", "打包流程")` |
| `list_corrections(limit)` | 列出纠正记录 | limit: 返回上限 | list[dict] | `corrections = m.list_corrections(20)` |
| `get_evolution_log(event_type, limit)` | 获取进化事件 | event_type: 事件类型<br>limit: 返回上限 | list[dict] | `events = m.get_evolution_log("tier", 20)` |
| `get_tier_history(doc_id, limit)` | 获取层级变更历史 | doc_id: 文档ID<br>limit: 返回上限 | list[dict] | `history = m.get_tier_history(42, 20)` |

### 6️⃣ Agent 生命周期管理

| 方法 | 功能 | 参数 | 返回值 | 示例 |
|------|------|------|--------|------|
| `register_agent(agent_name, agent_type)` | 注册新 Agent | agent_name: Agent名称<br>agent_type: Agent类型 | dict | `result = m.register_agent("mimo", "skill")` |
| `unregister_agent(agent_name, keep_data)` | 注销 Agent | agent_name: Agent名称<br>keep_data: 是否保留数据 | None | `m.unregister_agent("test_agent", True)` |
| `list_agents()` | 列出所有已注册 Agent | None | list[dict] | `agents = m.list_agents()` |
| `get_agent(agent_name)` | 查询单个 Agent | agent_name: Agent名称 | dict | `agent = m.get_agent("claude")` |

### 7️⃣ 安全与审计

| 方法 | 功能 | 参数 | 返回值 | 示例 |
|------|------|------|--------|------|
| `detect_secrets(content)` | 检测API密钥 | content: 待检测内容 | list[dict] | `secrets = detect_secrets(content)` |
| `redact_secrets(content)` | 脱敏处理 | content: 待脱敏内容 | str | `clean = redact_secrets(content)` |

## 🔄 典型工作流

### 1. 摄入知识流程 (`/mw-ingest`)

```
[1] 读取源文件
[2] Agent 分类 → JSON：label / importance / category / ...
[3] m.search(keywords) → 保存到变量 search_results
[4] 执行：mw ingest 或 update_memory()
[5] 写交叉引用 → m.auto_cross_ref(doc_id, scan_mentions=True)
[6] 更新 index.md（路由表）
[7] 追加 log_claude.md
```

### 2. 检索知识流程 (`/mw-query`)

```
[1] 读 index.md 定位分组
[2] FTS5+entity 粗筛 Top-20（标记访问）
[3] Agent Rerank（Agent自己语义排序）→ Top-5
[4] m.get_linked() 顺 cross_ref 展开关联 + 溯源返回
```

### 3. 知识健康度检查 (`/mw-lint`)

```
[1] 孤立检测：SQL 查零关联
[2] 重复检测：Claude 扫同category摘要
[3] 矛盾检测：Claude 扫
[4] 过期检测：Claude 判
[5] 断链检测：cross_ref外键
```

## 📁 数据库结构

### 核心表

| 表名 | 功能 | 主要字段 |
|------|------|----------|
| `document_files` | 文档文件元数据 | id, file_path, file_hash, create_time |
| `memory_classify` | 分类结果 | doc_id, label, importance, weight, compact_content |
| `memory_entity` | 实体关联 | doc_id, entity_name, entity_type, weight |
| `memory_fts` | FTS5全文索引 | doc_id, title, summary, content_category |
| `memory_cross_ref` | 交叉引用 | doc_id, related_doc_id, relation_type, note |
| `lint_log` | 健康检查日志 | doc_id, check_type, status, details |
| `global_rules` | 全局规则 | id, rule_text, category, priority, confidence |
| `system_meta` | 系统元数据 | key, value, updated_at |

### 配套辅助文件

| 文件 | 功能 | 命名规则 |
|------|------|----------|
| `memory_index_<agent>.md` | 路由表 | memory_index_claude.md |
| `log_<agent>.md` | 操作日志 | log_claude.md |
| `lint_report_<agent>.md` | 体检报告 | lint_report_claude.md |

## 🎯 分类格式 (classification)

```python
classification = {
    "label": "meta_rule",           # 七个 label 之一
    "importance": "P1",             # P0/P1/P2/P3/P4
    "category": "打包部署",         # 内容分类
    "sub_category": "验证流程",     # 子分类
    "summary": "打包前必须运行 python -c \"import src.main\" 验证语法",
    "knowledge_type": "行为规则",   # 行为规则/项目上下文/技术决策/踩坑记录/配置信息/规则/会话痕迹
    "applicability": "通用规则",    # 通用规则/场景知识/会话痕迹
    "depth": "概述",               # 概述/详细/深入
    "content_type": "规则",         # 规则/知识/踩坑/决策/配置
    "entities": [                   # 实体列表
        {"name": "打包", "type": "concept"},
        {"name": "import验证", "type": "concept"}
    ]
}
```

## 🔗 关联类型 (relation_type)

| 类型 | 说明 | 示例 |
|------|------|------|
| `supplement` | 补充关系 | 两条规则互为补充 |
| `refute` | 反驳关系 | 新规则推翻旧规则 |
| `extend` | 扩展关系 | 新规则是旧规则的扩展 |
| `premise` | 前提关系 | A是B的前提条件 |
| `example` | 示例关系 | A是B的具体示例 |
| `related` | 相关关系 | 通用相关关联 |
| `mention` | 提及关系 | 正文中提到了对方的实体 |

## 📊 权重规则

| 应用性 | 初始权重 | 说明 |
|--------|----------|------|
| 通用规则 | 95 | 通用规则权重最高 |
| 场景知识 | 50 | 场景知识中等权重 |
| 会话痕迹 | 20 | 会话痕迹权重最低 |

**权重自动进化**：
- `search()` 每次命中自动 `weight+5`（上限100）
- `decay_weights()` 在 `/mw-evolve` 时对长期未访问的记忆乘 0.8（不低于10）

## 🚀 快速开始

```python
from mw_sdk import MemoryClient
from mw_sdk.utils import get_agent_db

# 1. 初始化
m = MemoryClient(get_agent_db())
m.init_schema()

# 2. 搜索记忆
results = m.search("打包 部署", top_k=5)

# 3. 摄入新记忆
classification = {
    "label": "meta_rule",
    "importance": "P1",
    "category": "打包部署",
    "summary": "打包前必须验证语法",
    "applicability": "通用规则"
}
# 通过 mw ingest 写入（内部调用 _insert_classified）
import subprocess
subprocess.run(["python", "-m", "mw_sdk.cli", "ingest", "打包前必须验证语法",
                "--label", "rule", "--importance", "P1", "--category", "打包部署"])

# 4. 获取关联记忆
linked = m.get_linked(doc_id)

# 5. 导出为Obsidian
m.export_md("D:/exports/obsidian")
```

## 📝 注意事项

1. **数据库隔离**：每个 Agent 各自 SQLite，互不干扰
2. **大池子共享**：`meta.sqlite` 是共享知识库，所有 Agent 都能搜
3. **幂等操作**：`init_schema()`、`auto_cross_ref()` 都是幂等的
4. **安全特性**：自动检测并脱敏 API 密钥（sk-/AKIA 等）
5. **审计日志**：所有写入操作记录到 `audit_*.log`
6. **性能优化**：批量写入每100条提交一次

> 生成时间：2026-06-29  
> 基于 MW SDK v8.0