# CLI 命令速查

> 核心流程见 SKILL.md，完整命令列表按需查阅。

## 搜索

| 命令 | 说明 |
|------|------|
| `mw search "关键词"` | 四路融合搜索（FTS5+Entity+Vector+Graph） |
| `mw search "关键词" --graph` | 搜索 + 图谱关联展开 |
| `mw search "关键词" --explain` | 搜索 + 匹配详情（调试用） |
| `mw search "关键词" --no-vector` | 关闭向量搜索 |
| `mw search "关键词" --extra 词1 词2` | 搜索 + 额外关键词（OR 语义扩大覆盖） |
| `mw search-links "关键词"` | 知识图谱关联搜索 |
| `mw rules-search <意图>` | 按意图搜索规则 |

## 写入

| 命令 | 说明 |
|------|------|
| `mw ingest "内容" --category "分类" --importance P1` | 摄入记忆 |
| `echo "内容" \| mw ingest` | 管道输入 |

## 管理

| 命令 | 说明 |
|------|------|
| `mw index` | 知识库概览 |
| `mw list` | 按分类列出记忆 |
| `mw list -c "分类"` | 按分类筛选 |
| `mw stats` | 统计信息 |
| `mw export [目录]` | 导出 Obsidian |
| `mw import <目录>` | 从 Markdown 导入 |
| `mw sync` | SQLite ↔ MD/JSON 双向同步 |
| `mw update <doc_id> --scope project` | 更新 scope/category/keywords |
| `mw promote --min-weight 100 --min-access 10` | 批量将 project 记忆提升为 global |
| `mw reorganize` | 自动整理记忆（Agent 风格规划） |
| `mw health` | 健康度检查 |

## 图谱

| 命令 | 说明 |
|------|------|
| `mw cross-ref <top_k>` | 批量创建双向关联 |
| `mw crawl` | 扫描未链接提及并自动建 cross_ref |
| `mw crawl --full` | 全量扫描 |
| `mw rebuild-links` | 重建知识图谱骨架 |
| `mw graph-traverse <doc_id> --hops 3` | 图谱遍历 |
| `mw graph-stats` | 图谱健康度统计 |
| `mw export-dot` | 导出 DOT 格式 |
| `mw link <src> <tgt> --weight 2.0` | 创建记忆关联（权重可调） |

## 向量

| 命令 | 说明 |
|------|------|
| `mw vector-build` | 构建/刷新向量索引 |
| `mw vector-status` | 查看向量索引统计 |
| `mw vector-search "关键词"` | 纯向量搜索（跳过 FTS5） |
| `mw vector-preload` | 预加载向量模型 |

## 进化

| 命令 | 说明 |
|------|------|
| `mw evolve` | 全量：衰减+候选+纠正 |
| `mw evolve --apply` | 自动应用建议 |
| `mw decay` | 衰减未使用记忆的权重 |
| `mw reflect "模式" "描述"` | 记录反思 |
| `mw log` | 查看全部进化日志 |
| `mw log --type correction` | 查看纠正记录 |
| `mw log --type evolution` | 查看进化事件 |

## 维护

| 命令 | 说明 |
|------|------|
| `mw cleanup --test` | 清理测试数据 |
| `mw cleanup --stale` | 清理过期记忆 |
| `mw cleanup --all` | 清理全部 |
| `mw rebuild-fts5` | 重建 FTS5 索引 |
| `mw backup` | 备份数据库 |

## 记忆管理

| 命令 | 说明 |
|------|------|
| `mw tier set <doc_id> hot/warm/cold/frozen` | 设置记忆层级 |
| `mw tier get <doc_id>` | 查看记忆层级 |
| `mw scene set <scene_id> <name>` | 创建/更新场景 |
| `mw scene get <scene_id>` | 查看场景详情 |
| `mw scene list` | 列出所有场景 |
| `mw emotion set <doc_id> positive/negative/neutral` | 设置情绪标签 |
| `mw emotion get <doc_id>` | 查看情绪标签 |
| `mw archive <doc_id>` | 归档记忆 |
| `mw forget <doc_id> --confirm` | 删除记忆（软删除） |
| `mw always-load set <doc_id>` | 设为始终加载 |
| `mw always-load unset <doc_id>` | 取消始终加载 |
| `mw always-load get` | 查看始终加载列表 |
| `mw session save <agent> --topic "..."` | 保存会话状态 |
| `mw session get <agent>` | 读取会话状态 |
| `mw valid-time <doc_id> --from-date YYYY-MM-DD --until YYYY-MM-DD` | 设置有效期 |

## 已废弃（不会删除，但别用）

| 命令 | 替代 |
|------|------|
| `mw lint` | `mw health` |
| `mw reindex --confirm` | `mw rebuild-fts5` |

## 触发词速查

| 操作 | 触发词 |
|------|--------|
| ingest | 记住 / 记录 / 存档 / 存一下 / 写入记忆 |
| query | 搜一下 / 查一下 / 之前说过 / 有没有关于 |
| index | 知识库概览 / 看看有什么记忆 |
| reflect | 反思 / 复盘 / 又犯了 |
| evolve | 进化扫描 / 升降级 / 权重衰减 |
| log | 进化日志 / 纠正记录 |
| crawl | 扫描关联 / 补关联 |
| cleanup | 清理数据 / 清理过期 |
| stats | 统计 / 知识库多大 |
| export | 导出 / 导出 Obsidian |
| tier | 层级 / 热度 / 冷热 / 升级 / 降级 |
| scene | 场景 / 场景标签 |
| archive | 归档 / 冷冻 / 不再活跃 |
| forget | 删除 / 删掉 / 移除 |
| always-load | 始终加载 / 常驻 / 默认加载 |
| backup | 备份 |
| sync | 同步 / 双向同步 |
| project_status | 项目快照 / 项目进度 / 做到哪了 / 现状 / 继续项目 |
