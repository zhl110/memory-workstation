# Only-MW-zhl

MW（Memory Workstation）—— Agent 的长期记忆脑区。

> **SDK 是存储引擎，你是记忆的大脑。** 记不记、记什么、怎么分类、什么时候读、读了之后怎么用，全部由 Agent 按 Skill 规范判断执行。

## 核心操作

| 操作 | 命令 | 说明 |
|------|------|------|
| 搜索 | `mw search "关键词"` | 四路融合搜索（FTS5+Entity+Vector+Graph） |
| 写入 | `mw ingest "内容" --label ...` | 分类 + 结构化写入 |
| 反思 | `mw reflect "模式" "描述"` | 记录反思模式 |
| 进化 | `mw evolve` | 权重衰减 + 冷热升降级 |
| 关联 | `mw link <src> <tgt> --weight 2.0` | 创建加权记忆关联 |
| 健康 | `mw health` | 组件健康检查 |
| 统计 | `mw stats` | 知识库统计 |
| 备份 | `mw backup` | 备份数据库 |

## 架构

```
Agent（你）← 分类 / 合并判断 / 相关性排序 / 反思
    │ 调用 mw CLI
    ▼
mw-sdk ← SQLite 存取 + FTS5 + 向量 + 图谱
```

## 关键特性

- **加权图谱遍历**：Agent 显式关联（weight=2.0）比自动发现（weight=1.0）优先级更高
- **搜索优先协议**：写入前必须先搜，决定是合并还是新建
- **反思四步法**：回顾→提炼→整理→展望，附质量检查清单
- **渐进式披露**：SKILL.md 精简路由 + references/ 按需读取详情

## 版本

当前版本：v1.1.0（2026-07-10）

详见 [SKILL.md](./SKILL.md) 和 [CHANGELOG.md](./CHANGELOG.md)。
