# MW 架构升级 + 进化模块抽取方案

## 问题

当前三端（exe、Claude、Codex）各有各的数据库，但 exe 扫描出来的数据（文件扫描、冷热候选）只有 exe 自己能看到，Agent 用不上。"大池子没人喝"。

同时 SKILL.md 719 行中 238 行的进化逻辑是 raw SQL + Agent 指令，无法复用、无法测试。

---

## 新架构：大池子互通

```
exe（大池子 meta.sqlite）
├─ 文件扫描、分类（exe 写）
├─ MemoryOptimizer 跑冷热候选 + weight 衰减 + 去重（exe 写）
├─ 所有 Agent 可读（但不可写）
└─ 保持 24h 自动运行

Claude 小本本（meta_claude.sqlite）        Codex 小本本（meta_codex.sqlite）
├─ 从大池子拉数据 → 自己去重/融合/归类     ├─ 同理
├─ 用户直接说的知识（"记住这个"）          ├─ 同理
├─ 自己的 correction_log / 反思记录        ├─ 同理
├─ 自己的进化 tier / 历史                  ├─ 同理
└─ 需要时读大池子                         └─ 需要时读大池子
```

### 核心规则

1. exe 只管写大池子，不碰 Agent 的库
2. Agent 可以读大池子，也能读自己的库
3. Agent 拉大池子数据 → 自己 fuse/去重 → 存自己库里
4. 大池子的 evolution_tier 跟 Agent 的 evolution_tier 是两套——Agent 自己决定怎么标
5. 跨库查询有 **优先级**：自己的库优先，大池子需显式指定

---

## 跨库查询优先级（重要）

Agent 读数据时遵循 **先自己、再大池子** 的原则：

```
/mw-query（检索）：
  ├─ /mw-query "规则"           → 只搜自己的库（默认）
  └─ /mw-query --pool "规则"    → 同时搜大池子 + 自己的库（加 --pool）

/mw-evolve（进化）：
  ├─ /mw-evolve                 → 只看自己小本本的冷热候选（默认）
  └─ /mw-evolve --pool          → 自己的候选 + 大池子候选（加 --pool）

从大池子拉数据：
  └─ 永远手动指定，不存在自动拉
       /mw-ingest --from-pool #42   → 把大池子 #42 拉到自己的库里自己整理
```

---

## 具体链路

### /mw-evolve（Agent 进化升降级）

```
Agent 调 /mw-evolve（默认）
  ├─ 读自己的冷热候选：evolve.get_own_candidates()
  │   → 查 meta_claude.sqlite 自己的数据
  │
  └─ 展示给你看 → 你确认 → 写自己的库（不改大池子）

Agent 调 /mw-evolve --pool（需要时）
  ├─ 读自己的候选：evolve.get_own_candidates()
  ├─ 读大池子候选：evolve.get_pool_candidates()
  │   → 查 meta.sqlite 的 evolution_log（MemoryOptimizer 扫出来的）
  │
  ├─ 合并展示给你看
  │   ├─ 【自己的】升温建议 2 条
  │   └─ 【大池子】升温建议 5 条
  │
  └─ 你确认后 → 写入自己的库（不改大池子）
```

### /mw-query（检索）

```
/mw-query "打包规则"
  → 只搜自己的库（meta_claude.sqlite）

/mw-query --pool "打包规则"
  → 搜自己的库 + 同时搜大池子（meta.sqlite）
  → 结果分两栏展示：
     【自己的】3 条
     【池子里的】12 条（可 /mw-ingest --from-pool #xx 拉到自己库）
```

### /mw-ingest P8（纠正检测）

```
用户说："不对，用 tab 不是空格"
  ├─ Agent 写入自己的 correction_log（meta_claude.sqlite）
  ├─ 不写大池子（这是 Agent 自己的学习记录）
  └─ 累计 3 次后问你是否固化 → 写入自己的 memory_classify
```

### /mw-ingest --from-pool（从大池子拉数据）

```
/mw-ingest --from-pool #42
  ├─ Agent 读大池子的 #42 内容
  ├─ 自己 fuse/去重 → 写入自己的库
  └─ 不修改大池子的数据
```

### MemoryOptimizer（exe 内 24h 定时）

```
MemoryOptimizer.run_once()
  ├─ decay_weights()
  ├─ dedup()
  ├─ merge_duplicates()
  └─ _find_evolve_candidates()
       ├─ 冷热候选存入大池子的 evolution_log
       └─ Agent 下次 /mw-evolve --pool 就能看到
```

---

## 删改清单

### 新建

| 文件 | 做什么 |
|------|--------|
| src/evolution/evolve.py | EvolveEngine 类，15 个方法 |
| src/evolution/__init__.py | 导出 EvolveEngine |
| tests/test_evolution.py | 8 个测试场景 |
| agent-hub/skills/mw-llm-wiki/_meta.json | skill 注册用 |
| agent-hub/skills/mw-llm-wiki/evals/ | 测试场景 |

### 修改

| 文件 | 做什么 |
|------|--------|
| src/__init__.py | 加一行 from .evolution import EvolveEngine |
| mw-sdk/mw_sdk/client.py | SCHEMA 加 3 张表 + evolution_tier 列；加进化方法；加跨库 set_pool_path() |
| SKILL.md | P8/mw-reflect/mw-evolve/mw-log 四段 raw SQL → evolve.* 调用；加 --pool 逻辑 |

### 不动

- src/storage/sqlite_store.py — P0 schema 已完成
- src/optimizer.py — _find_evolve_candidates() 已存在，继续跑大池子
- src/pipeline/pipeline.py — ClassifyResult.evolution_tier 已有

---

## EvolveEngine 方法清单（15 个）

### 跨库查询
```
get_own_candidates()           → 读自己的冷热候选
get_pool_candidates()          → 读大池子的冷热候选
search_own(query, top_k=10)    → 搜自己的库
search(query, top_k=10)        → C++ 融合搜索（FTS5+Entity+Vector+Graph）
search_all(query, top_k=10)    → 同时搜两边（用于 --pool 模式）
```

### correction_log（写自己的库）
```
get_correction_pending(min_count=3)    → 待确认的纠正
increment_correction(pattern, summary) → 记一次纠正
suppress_correction(pattern)           → 24h 不再问
promote_correction(pattern)            → 已晋升
list_corrections(limit=20)             → 纠正历史
```

### evolution_log（写自己的库）
```
log_event(event_type, trigger, ...)              → 记一次事件
get_evolution_log(event_type, limit=20)          → 查日志
```

### tier_history（写自己的库）
```
apply_tier_change(doc_id, from_tier, to_tier, reason)    → 改层级
get_tier_history(doc_id, limit=20)                        → 查历史
get_evolution_stats()                                      → 统计
```

---

## mw-sdk 改动

MemoryClient 新增方法（Agent 和 exe 通用）：

```python
# 跨库
m.set_pool_path("D:/MemoryWorkstation/.memory-workstation/meta.sqlite")

# 跨库查询
m.get_own_candidates()      # 自己的候选
m.get_pool_candidates()     # 大池子的候选
m.search_own("关键词")       # 搜自己的
m.search("关键词")            # C++ 融合搜索

# 进化操作（默认写自己的库）
m.increment_correction("prefer_tabs", "用 tab 不是空格")
m.get_correction_pending()
m.apply_tier_change(42, "warm", "hot", "高频访问")
```

Claude 和 Codex 代码一样，只是 db_path 不同：

```python
# Claude
m = MemoryClient("D:/MemoryWorkstation/.memory-workstation/meta_claude.sqlite")
m.set_pool_path("D:/MemoryWorkstation/.memory-workstation/meta.sqlite")

# Codex
m = MemoryClient("D:/MemoryWorkstation/.memory-workstation/meta_codex.sqlite")
m.set_pool_path("D:/MemoryWorkstation/.memory-workstation/meta.sqlite")
```

---

## SKILL.md 缩减效果

| 段 | 现在行数 | 缩减后 | 比例 |
|----|---------|--------|------|
| P8 纠正检测 | 21 | 12 | -43% |
| /mw-reflect | 58 | 20 | -66% |
| /mw-evolve（含 --pool） | 121 | 40 | -67% |
| /mw-log | 38 | 13 | -66% |
| /mw-query（加 --pool） | (不动) | +5 | - |
| **合计** | **238** | **85** | **-64%** |

---

## 补全 skill 目录结构

```
agent-hub/skills/mw-llm-wiki/
├── SKILL.md       ← 瘦身后（~340 行）
├── _meta.json     ← 新建
├── README.md      ← 可选
└── evals/         ← 可选，测试场景
```

_meta.json：
```json
{
  "ownerId": "memory-workstation",
  "slug": "mw-llm-wiki",
  "version": "0.4.0",
  "publishedAt": 1719100000000
}
```

---

## 跟原方案的区别

| 项 | 原方案 | 新方案 |
|----|--------|--------|
| Agent 读大池子 | 不读 | 可读（需显式 --pool） |
| MemoryOptimizer 的候选 | 白扫，没人用 | Agent /mw-evolve --pool 能看到 |
| 查询优先级 | 无 | 自己的库优先，大池子需 --pool |
| mw-sdk 跨库 | 不支持 | 加 set_pool_path() |
| evolution_tier | 各管各 | 各自独立，大池子的只是参考 |

---

## 验证方法

```bash
# 1. 模块导入
python -c "from src.evolution import EvolveEngine; print('OK')"

# 2. 测试
python tests/test_evolution.py

# 3. SDK + 跨库
python -c "
from mw_sdk import MemoryClient
m = MemoryClient(':memory:')
m.set_pool_path(':memory:')
m.init_schema()
print('SDK + pool OK')
"

# 4. 不影响旧的
python -c "from src.optimizer import MemoryOptimizer; print('OK')"
```
