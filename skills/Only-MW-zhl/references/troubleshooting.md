# 异常处理手册

> 异常场景和修复指引。

---

## 前置依赖问题

### SDK 未安装

**现象：** `ModuleNotFoundError: No module named 'mw_sdk'`

**修复：**

```bash
pip install d:/mycode/memory-workstation/mw-sdk/
```

### SDK 版本过旧

**现象：** 调某个方法时报 `AttributeError`

**修复：**

```bash
pip install --force-reinstall d:/mycode/memory-workstation/mw-sdk/
```

---

## 数据库问题

### meta_agents.sqlite 不存在

**现象：** 第一次使用时找不到数据库

**修复：** 初始化时确保调了 `init_schema()`：

```python
from mw_sdk import MemoryClient
m = MemoryClient("D:/MemoryWorkstation/.memory-workstation/meta_agents.sqlite")
m.init_schema()
```

### init_schema() 重复运行报错 [已修复]

**现象（旧版本）：** `duplicate column name: evolution_tier`

**原因：** `ALTER TABLE memory_classify ADD COLUMN` 不是幂等的

**当前状态：** ✅ 已修复。`init_schema()` 内部通过 `PRAGMA table_info()` 检查列是否存在，直接重复调用不会报错。

### SQLite 文件被锁

**现象：** `database is locked`

**说明：** 其他进程占用，等待后重试即可。`WAL` 模式已启用但多进程同写仍有冲突可能。

### 大池子连不上

静默跳过，不影响功能。

---

## 辅助文件丢失

| 文件 | 影响 | 重建方式 |
|------|------|---------|
| `memory_index_agents.md` | 仅 `mw index` 展示 | 下次 `mw ingest` 自动重建 |
| `log_agents.md` | 不具影响 | 下次操作自动新建 |
| `lint_report_agents.md` | 不具影响 | 下次 `mw health` 自动新建 |

文件是 `_agents` 后缀（多 Agent 共享库），如用旧名 `_claude` 后缀说明指向了过时路径。

---

## 常见卡点

| 操作 | 卡点 | 解决 |
|------|------|------|
| `mw ingest` | 写入后搜不到 | 检查 FTS5 是否写入成功（可能记到 `fts_pending_rebuild`），等数秒或 `mw rebuild-fts5` |
| `mw search` | 结果不相关 | 用 `--explain` 查看各路段分，判断是 FTS5/Entity/Vector 哪路匹配弱；或加 `--extra` 扩大覆盖 |
| `mw search` | 向量搜不出东西 | 检查 `mw vector-status`，HNSW 索引可能未构建，先跑 `mw vector-build` |
| `mw health` | 返回异常 | 检查数据库连接和各组件状态；`pool` 显示 warning 是正常的（未配连接池） |
| `mw index` | 分组变多后导航乱 | 定期跑 `mw health` 检查；用 `mw stats` 看记忆分布 |
| `mw evolve` | 候选摘要不够完整 | 额外调 `get_memory(doc_id)` 展开全文 |
| `mw log` | 过滤条件记混 | correction → `--type correction`，evolution → `--type evolution` |
| `mw cleanup` | 误删重要记忆 | 先用 `--dry-run` / `--test` 预览 |
| `mw rebuild-fts5` | 索引重建后搜索仍异常 | 检查 `fts5_entries` 和 `fts5_behind` 是否正常（`mw health`） |
| `mw crawl` | 长时间不返回 | 记忆量大时正常，可加 `--full` 控制；首次 crawl 较慢 |

---

## 旧命名单对照

| 旧名（已废弃） | 当前命令 |
|---------------|---------|
| `mw reindex --confirm` | `mw rebuild-fts5` |
| `mw lint` | `mw health` |
| `meta_claude.sqlite` | `meta_agents.sqlite` |
| `_claude` 后缀文件 | `_agents` 后缀 |
