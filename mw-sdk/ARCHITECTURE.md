# MW-SDK 技术架构

_MW-SDK 的整体架构、搜索引擎内部机制、分类体系、安全功能等技术参考。_

## 架构总览

```
Agent (Claude/Codex/MiMo)     ← 有脑子：classify / rerank
    │ import
    ▼
mw-sdk
    ├── cpp/                    ← C++ 引擎（全部核心逻辑）
    │   ├── Storage             ← 核心：生命周期/schema/stats/embedding引擎/健康检查
    │   │   ├── storage.cpp           ← 构造/析构/事务/schema DDL/stats/health_check
    │   │   ├── storage_search.cpp    ← FTS5/LIKE/Entity 搜索
    │   │   ├── storage_ingest.cpp    ← CRUD + 实体/交叉引用/batch_ingest/access/权重/embedding
    │   │   └── storage_evolution.cpp ← 纠正/进化/always_load/cleanup/FTS5维护/候选
    │   ├── SearchEngine        ← 四路融合搜索（BM25+Entity+Vector+Graph）
    │   ├── GraphEngine         ← BFS/Dijkstra 图遍历
    │   ├── Rules               ← 规则查询 + 关联 + scan_mentions
    │   └── HNSWIndex           ← 向量索引
    │
    └── mw_sdk/                 ← Python 包装层（Mixin 架构）
        ├── client.py           ← API 入口（继承 Mixin，委托 C++）
        ├── cli.py              ← CLI 命令调度（命令注册模式）
        ├── scene.py            ← SceneMixin：场景/规则/情绪/对话状态
        ├── tier.py             ← TierMixin：分层/归档/软删除/时序/实体解析
        ├── graph.py            ← GraphMixin：BFS/Dijkstra/路径/图统计
        ├── evolution.py        ← EvolutionMixin：权重衰减/候选/纠正/进化统计
        ├── stats.py            ← StatsMixin：统计/健康检查/Agent 注册
        ├── schema.py           ← 数据库 DDL + 模板（SCHEMA_SQL/INDEX_TEMPLATE 等）
        ├── types.py            ← 类型定义
        ├── utils.py            ← 公共工具（cpp_to_dict 等）
        └── _core/mw_core.pyd  ← pybind11 编译产物（7 个绑定函数）
```

**C++ 覆盖**：搜索/写入/关联/图谱/向量/进化系统/always_load/健康检查/清理/FTS5维护/候选扫描 — 所有核心逻辑。

**Python 保留**：CLI 解析和显示、`crawl_cross_ref`/`rebuild_links`（编排层）、导出（MD/JSONL/备份）、大池子连接管理、Agent 注册表。

**数据库**：三个 Agent 共用 `meta_agents.sqlite`。

## 搜索引擎内部机制

### 搜索流程总览

```
用户输入 query
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  查询预处理                                               │
│  ├── 清理特殊字符（引号、括号、星号等）                       │
│  ├── 查询扩展（_expand_query）                             │
│  │   └── 同义词/关联词扩展（如"个人网页"→"portfolio 作品集"）  │
│  └── 提取关键词候选                                        │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  三路并行搜索（同时进行）                                   │
│                                                         │
│  ┌─────────────────────────────────────────────────────┐│
│  │ FTS5 搜索（权重 0.4）                                 ││
│  │  ├── 输入：原始 query（未扩展）                        ││
│  │  ├── 匹配：memory_fts 全文索引                        ││
│  │  │   └── 7列加权 BM25 打分                           ││
│  │  │       title=5.0, summary=3.0, compact_content=4.0 ││
│  │  │       content_category=2.0, sub_category=2.0      ││
│  │  │       keywords=6.0（最高）                         ││
│  │  ├── tokenizer: trigram（至少3字符）                  ││
│  │  └── 无结果 → 降级 LIKE 模糊匹配                      ││
│  └─────────────────────────────────────────────────────┘│
│                                                         │
│  ┌─────────────────────────────────────────────────────┐│
│  │ 实体搜索（权重 0.2）                                  ││
│  │  ├── 输入：原始 query                                ││
│  │  ├── 匹配：memory_entity（entity_name + entity_type）││
│  │  ├── 逻辑：OR 组合（任一匹配即命中）                   ││
│  │  └── weight 归一化到 [0,1]                           ││
│  └─────────────────────────────────────────────────────┘│
│                                                         │
│  ┌─────────────────────────────────────────────────────┐│
│  │ 向量语义搜索（权重 0.4）                               ││
│  │  ├── 输入：扩展后的 query（_expand_query 结果）        ││
│  │  ├── 模型：all-MiniLM-L6-v2（384维）                 ││
│  │  ├── 索引：C++ HNSW 近似最近邻                        ││
│  │  ├── 相似度：cosine similarity                       ││
│  │  └── 可选：--no-vector 关闭                           ││
│  └─────────────────────────────────────────────────────┘│
│                                                         │
│  三路结果汇合                                             │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  结果判断                                                 │
│  ├── 有结果 → 继续融合                                    │
│  └── 无结果 → 图谱降级查找（--no-graph 时关闭）           │
│      ├── 提取 query 中的关键词                            │
│      ├── 用关键词搜 memory_entity（LIKE 模糊匹配）        │
│      ├── 找到相关实体 → 用图谱展开关联记忆                  │
│      └── 仍无结果 → 返回空                                │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  融合模式选择（mode）                                     │
└─────────────────────────────────────────────────────────┘
    │
    ├───────────────┐
    ▼               ▼
┌──────────┐  ┌──────────┐
│   rrf    │  │  hybrid  │
│ 排名倒数  │  │ RRF+遗忘 │
│   融合    │  │   曲线   │
│score =   │  │score =   │
│Σ1/(k+    │  │RRF score │
│ rank_i)  │  │×E(t)     │
│k=60      │  │          │
└──────────┘  └──────────┘
    │               │
    └───────────────┼───────────────┘
                    ▼
┌─────────────────────────────────────────────────────────┐
│  后处理                                                   │
│  ├── 访问 boost：7天内访问 × 1.3                         │
│  ├── 权重递增：每条命中 weight+5（上限100）                │
│  └── 大池子补：自己库不够时 LIKE 查询共享知识库             │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  返回 top_k 结果                                          │
│  ├── 同一 doc_id 去重（score 累加）                       │
│  └── explain=True 时返回匹配详情和信号贡献度               │
└─────────────────────────────────────────────────────────┘
```

### 导入记忆流程（mw ingest）

```
用户提供内容（文本/URL/文件路径）
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  Step 1: 读取内容                                         │
│  ├── 纯文本 → 直接使用                                    │
│  ├── URL → HTTP 抓取正文                                  │
│  ├── 本地文件 → 读取文件内容                               │
│  └── 输出：content（字符串）                               │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  Step 2: Agent 分类（LLM）                                │
│  ├── 输入：content                                       │
│  ├── 输出：                                              │
│  │   ├── label: meta_rule / rule / planning_doc /        │
│  │   │        self_improve_learn                         │
│  │   ├── importance: P0 / P1 / P2                        │
│  │   ├── category: 一级分类                               │
│  │   ├── sub_category: 二级分类                           │
│  │   ├── summary: 一句话摘要                              │
│  │   └── applicability: 通用规则/场景知识/会话痕迹          │
│  └── 跳过条件：JSON日志 / 聊天记录无规则信号词              │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  Step 3: 关键词提取（_extract_keywords）                  │
│  ├── 来源：category + sub_category + label +             │
│  │        entities + tags + 内容高频词                    │
│  ├── 输出：keywords 字符串（空格分隔）                     │
│  └── 用途：写入 FTS5 keywords 列（权重 6.0）              │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  Step 4: 知识复合判断（fuse or insert）                   │
│  ├── 查询已有记忆：同 label + 同 category + 同主题         │
│  ├── 命中 → fuse（融合更新）                              │
│  │   ├── 更新 summary / importance / weight              │
│  │   ├── 合并 keywords（old + new）                      │
│  │   └── 更新 memory_classify + memory_fts               │
│  └── 未命中 → insert（插入新记忆）                        │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  Step 5: 写入四表                                         │
│  ├── memory_classify（分类表）                            │
│  │   └── doc_id, summary, category, sub_category,       │
│  │       importance, weight, label, applicability,       │
│  │       keywords, workspace_id, memory_type,            │
│  │       evolution_tier, evolution_count                 │
│  │                                                       │
│  ├── memory_fts（全文索引）                               │
│  │   └── title, summary, content_category,               │
│  │       sub_category, compact_content, keywords         │
│  │                                                       │
│  ├── memory_vector（向量表）                              │
│  │   └── doc_id, embedding（JSON/BLOB，384维）            │
│  │                                                       │
│  └── memory_entity（实体表）                              │
│      └── doc_id, entity_name, entity_type, weight        │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  Step 6: 交叉引用（auto_cross_ref）                       │
│  ├── 两路数据源：                                        │
│  │   ├── candidates 参数：上层已搜好的结果                 │
│  │   └── candidates=None：自动找同 entity + 同 category   │
│  ├── relation_type：supplement/refute/extend/premise/    │
│  │                 example/related/mention                │
│  └── 写入 memory_cross_ref                               │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  Step 7: 扫描提及（scan_mentions）                        │
│  ├── C++ 实现：Storage::scan_mentions()                  │
│  ├── 扫描新记忆正文 → 发现提到其他记忆的 entity            │
│  └── 自动建立 mention 关联                               │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  Step 8: 更新索引 + 日志                                  │
│  ├── 更新 index.md（Obsidian 导航索引）                   │
│  ├── 追加 log.md（操作日志）                              │
│  └── 返回：doc_id, fused/inserted, summary               │
└─────────────────────────────────────────────────────────┘
```

### 导入数据流示意

```
content → classify → keywords → fuse/insert → 4表 → cross_ref → scan_mentions → index
   │         │          │           │            │           │              │        │
   │      (LLM)    (提取语义词)   (判重)     (写入)      (关联)       (扫描提及)  (导航)
   │         │          │           │            │           │              │        │
   ▼         ▼          ▼           ▼            ▼           ▼              ▼        ▼
  文本    分类结果    keywords    doc_id     memory_*    cross_ref     mention    index.md
```

### 三种搜索模式

| 模式 | 融合方式 | 公式 | 适用场景 |
|------|---------|------|---------|
| **rrf** | 排名倒数融合 | score = Σ 1/(k+rank_i)，k=60 | **当前默认** |
| **hybrid** | RRF + 遗忘曲线 | RRF score × Ebbinghaus 衰减系数 | 长期记忆 |

### FTS5 全文搜索（权重 0.4）

**原理：** SQLite FTS5 虚拟表，trigram tokenizer

**BM25 打分（各列权重）：**

| 列名 | 权重 | 说明 |
|------|------|------|
| doc_id | 1.0 | UNINDEXED，不影响打分 |
| title | 5.0 | 标题匹配得分最高 |
| summary | 3.0 | 摘要 |
| content_category | 2.0 | 内容分类 |
| sub_category | 2.0 | 子分类 |
| compact_content | 4.0 | 正文内容 |
| **keywords** | **6.0** | 语义关键词（最高权重） |

**关键词来源：**
- ingest 时从分类结果提取（category/sub_category/label + entities + tags）
- 存入 memory_classify.keywords 和 memory_fts.keywords
- FTS5 搜索时自动匹配

**查询处理：**
- 自动清理特殊字符（引号、括号、星号等）
- 支持中文（trigram 分词，至少3字符）
- 无结果时自动降级为 LIKE 模糊匹配
- 支持 `extra_keywords` 参数：额外关键词用 OR 语义合并，扩大覆盖范围

### 实体搜索（权重 0.2）

**原理：** 在 memory_entity 表中搜索匹配的实体

**匹配方式：**
- 搜索 entity_name 和 entity_type
- OR 组合（任一匹配即算命中）
- weight 归一化到 [0,1]

**实体来源：**
- ingest 时由 Agent（LLM）识别提取
- 存入 memory_entity 表
- 同一实体多次出现 weight+1

### 向量语义搜索（权重 0.4）

**原理：** C++ HNSW（Hierarchical Navigable Small World）近似最近邻索引

**模型：** all-MiniLM-L6-v2（384维向量）

**相似度计算：** cosine similarity

**加速机制：**
- ONNX session 进程常驻（Python 侧初始化一次）
- HNSW 索引预构建
- 支持 `--no-vector` 关闭向量搜索

**向量来源：**
- ingest 时由模型生成 embedding
- 存入 memory_vector 表
- 支持 JSON text 或 BLOB 格式

### 图谱展开（默认开启，--no-graph 关闭）

**原理：** 从搜索结果出发，展开关联记忆

**两种使用场景：**

1. **有结果时展开**：对搜索结果的 Top-N 进行图谱关联展开
2. **无结果时降级**：当三路搜索都无结果时，用图谱遍历查找相关记忆

**图谱降级流程：**
```
三路搜索无结果
    │
    ▼
用 LIKE 搜索 memory_classify（比 FTS5 更宽泛）
  WHERE summary LIKE '%query%' 
     OR compact_content LIKE '%query%'
     OR category LIKE '%query%'
     OR sub_category LIKE '%query%'
    │
    ├── 找到相关记忆 → 用图谱展开关联记忆
    │
    └── 仍无结果 → 返回空
```

**遍历方式：**
- BFS（广度优先搜索）
- Dijkstra（最短路径）
- 支持最大跳数限制（graph_max_hops）

**展开范围：**
- 对前 N 个结果展开（graph_expand_top）
- 展开的记忆加入候选集

### RRF 融合详解

**公式：** `score = Σ w_i × 1/(k + rank_i)`，k=60，w_i 为三路权重

**权重（可配置）：**
- FTS5 BM25: w0（默认 0.4）
- Entity: w1（默认 0.2）
- Vector: w2（默认 0.4）
- 权重在运行时归一化（w0+w1+w2=1）

**示例（默认权重）：**
```
记忆 A：FTS5排名第1，实体排名第3，向量排名第1
score = 0.4×1/(60+1) + 0.2×1/(60+3) + 0.4×1/(60+1)
      = 0.0066 + 0.0032 + 0.0066 = 0.0164

记忆 B：FTS5排名第5，实体排名第1，向量排名第10
score = 0.4×1/(60+5) + 0.2×1/(60+1) + 0.4×1/(60+10)
      = 0.0062 + 0.0033 + 0.0057 = 0.0152

→ A 排名更高（FTS5 与向量共同主导）
```

**分数归一化：** BM25 分数在 fts_search 中归一化到 [0,1]，与 entity/vector 分数量纲一致

### Hybrid 模式（RRF + 艾宾浩斯）

**遗忘曲线公式：** `R = e^(-t/S)`
- R: 记忆保留率
- t: 距离上次访问的时间
- S: 稳定性系数（由 access_count 和 weight 决定）

**效果：**
- 刚访问过的记忆得分高
- 长期未访问的记忆得分衰减
- 高频访问的记忆更稳定

### 其他机制

| 机制 | 说明 |
|------|------|
| 访问 boost | 7天内有访问记录 × RECENCY_BOOST |
| 大池子补 | 自己库不够时自动从共享知识库补 |
| 权重递增 | 每条命中自动 weight+5（上限100） |
| 图谱降级 | 无结果时用 LIKE 搜 memory_classify，再图谱展开 |
| explain 调试 | explain=True 返回匹配详情和信号贡献度 |
| 访问记录 | Python 层统一调用 record_access_batch（两条路径行为一致） |
| 结果截断 | explain → scene 过滤 → top_k 截断（图谱展开结果不被误丢） |

## 交叉引用系统

**auto_cross_ref 两路数据源**：
1. `candidates` 参数：上层已搜好的结果
2. `candidates=None`：自动用 entity 共享 + 同 category 查找

**relation_type**：`supplement` / `refute` / `extend` / `premise` / `example` / `related` / `mention`

**scan_mentions**：C++ `Storage::scan_mentions()` 实现，扫描正文发现提到其他记忆的 entity → 自动建 mention 关联

**link 命令（v1.0.0）**：`mw link <source_id> <target_id> --weight 2.0 --note "说明"`
- Agent 显式关联：weight=2.0（强链接）
- crawl 自动发现：weight=1.0（弱链接）
- graph-traverse 优先走权重高的链接

## 分类体系（双轴）

### 轴1：内容类型（content_category）

| 分类 | 含义 | 举例 |
|------|------|------|
| 安全类 | 禁硬编码密钥、禁高危命令 | "禁止硬编码密钥" |
| 执行类 | MW完整流程、先搜记忆、先确认 | "用户问问题只回答不执行" |
| 沟通类 | 输出规范、术语加白话 | "报错精准定位" |
| 代码类 | 代码规范、质量标准 | "只修Bug不擅优化" |
| 工具类 | CLI、脚本、工具链 | "mw search 用法" |
| 设计类 | UI/UX、前端设计 | "配色方案" |
| 系统架构 | 架构、选型、平台 | "选择 FTS5" |
| 测试规范 | 测试策略、冒烟测试 | "pytest 用法" |
| 项目记录 | 项目进展、版本记录 | "v0.18 发布" |
| 项目专属规则 | 项目特有约定 | "MW项目的代码风格" |
| 踩坑经验 | 排查过程、根因分析 | "线程竞争 bug 修复" |
| 架构决策 | 方案对比、选择理由 | "选 FTS5 不选 Elasticsearch" |
| 经验 | 非强制的操作技巧 | "调试技巧" |
| 用户信息 | 基础信息、偏好习惯 | "用户偏好 CLI" |

### 轴2：所属域（scope + project）

| 域 | scope 值 | project 值 | 含义 |
|----|----------|-----------|------|
| 全局通用 | `global` | 空 | 所有项目都适用 |
| 某项目专属 | `project` | 项目名 | 只在该项目上下文有效 |
| 会话追踪 | `session` | 空 | 30天后降权 |

### 晋升机制

| 条件 | 说明 |
|------|------|
| weight >= 100 | 权重达到满分 |
| access_count >= 10 | 被访问 10 次以上 |
| 两个条件都满足 | 自动晋升为 global |

## 特殊功能

- **大池子自动补**：search 不够时自动从共享知识库补
- **scan_mentions**：C++ 实现，扫描正文发现 entity 提及 → 自动建 mention 关联
- **correction→evolve→promote**：C++ 实现，犯错→记录→3次自动变规则
- **C++ 全核心**：进化系统/always_load/健康检查/清理/候选扫描/FTS5重建 — 全部由 C++ 实现

## 安全功能

| 模块 | 功能 |
|------|------|
| `security.py` | 自动检测 API 密钥（AKIA/sk-/token/password），写入时脱敏 |
| `audit.py` | JSON 行格式审计日志，10MB 自动轮转，threading.Lock 线程安全 |
| `utils.py` | `validate_utf8()` 编码校验 + `safe_truncate()` 多字节安全截断 |

## 数据库 Schema

### memory_classify（分类表）

| 分组 | 字段 | 类型 | 说明 |
|------|------|------|------|
| **主键** | id | INTEGER | 自增主键 |
| | doc_id | INTEGER | 业务文档 ID |
| **分类** | label | TEXT | 标签（规则/经验/架构决策/bug-fix/项目记录 等） |
| | title | TEXT | 标题（从 compact_content 提取或 Agent 提供） |
| | content_category | TEXT | 内容分类（安全类/执行类/代码类/踩坑经验 等） |
| | sub_category | TEXT | 子分类 |
| | importance | TEXT | 重要性（P0/P1/P2） |
| | weight | INTEGER | 权重（0-100，越高越重要） |
| | keywords | TEXT | 搜索关键词（中英文混合，空格分隔） |
| | key_points | TEXT | 关键要点 |
| | summary | TEXT | 摘要 |
| | compact_content | TEXT | 紧凑内容 |
| **范围** | **scope** | TEXT | 记忆所属范围：global / project / session |
| | **project** | TEXT | 项目名称（scope=project 时必填） |
| **层级** | memory_tier | TEXT | 热度层级（hot/warm/cold） |
| | evolution_tier | TEXT | 进化层级 |
| **来源** | source | TEXT | 来源标记（cli:mw_ingest / Agent 标识） |
| | tags | TEXT | 标签列表 |
| | extra_tags | TEXT | 额外标签 |
| | cross_ref | TEXT | 交叉引用 |
| | relate_id | TEXT | 关联 ID |
| **元数据** | workspace_id | TEXT | 工作空间 ID |
| | memory_type | TEXT | 记忆类型（session/project/global/cc） |
| | create_time | TEXT | 创建时间 |
| | meta | TEXT | 扩展属性 JSON（如 always_load） |
| **v0.19.0** | **scene** | TEXT | 场景标签（code/design/planning 等） |
| | **emotion** | TEXT | 情绪标签（positive/neutral/negative） |
| **v0.20.0** | **valid_from** | TEXT | 有效期开始时间 |
| | **valid_until** | TEXT | 有效期结束时间（NULL表示永久有效） |
| | **invalidated_by** | INTEGER | 被哪个doc_id作废（0表示未作废） |
| | **tier** | TEXT | 当前分层（hot/warm/cold） |
| | **tier_updated_at** | TEXT | 分层最后更新时间 |
| **AI 分类** | ai_type | TEXT | AI 分类类型 |
| | daily_type | TEXT | 日常类型 |
| | depth | TEXT | 深度（概述/详细/深入） |
| | stability | TEXT | 稳定性 |
| | confidence | TEXT | 置信度 |
| | classify_record | TEXT | 分类记录 |

### memory_fts（全文索引）
- FTS5 虚拟表，trigram tokenizer
- 字段：title, summary, content_category, sub_category, compact_content, **keywords**
- ingest 时自动提取语义关键词写入 keywords 列

### memory_vector（向量表）
- doc_id, embedding（JSON text 或 BLOB，384维）, content_hash, created_at
- 模型：all-MiniLM-L6-v2

### memory_entity（实体表）
- id, doc_id, entity_name, entity_type, weight, created_at

### memory_cross_ref（交叉引用表）
- id, doc_id, related_doc_id, relation_type, weight (REAL, 默认 1.0), note, created_at

### memory_scene（场景表）— v0.19.0
- scene_id (PK), name, parent_scene, description, create_time

### memory_scene_rule（场景规则表）— v0.19.0
- rule_id (PK), scene_id (FK), rule_type (must/should/prefer), rule_text, priority, create_time

### memory_emotion（情绪表）— v0.19.0
- emotion_id (PK), doc_id (FK), emotion_type (positive/neutral/negative), emotion_detail, intensity, create_time

### memory_session_state（对话状态表）— v0.19.0
- state_id (PK), agent_name, session_id, last_topic, unfinished_tasks (JSON), emotion_state, update_time

### memory_tier_log（分层变更日志）— v0.20.0
- id (PK), doc_id, from_tier, to_tier, reason, created_at

### memory_entity_mention（实体提及记录）— v0.20.0
- id (PK), entity_id (FK), memory_id (FK), context, created_at
