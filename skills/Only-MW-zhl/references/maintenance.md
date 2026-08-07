# 维护操作

> 低频操作，按需查阅。

## 健康检查

```bash
mw health
```

返回（完整结构）：

```
database: {status, fts5_entries, fts5_behind}
pool: {status, detail}            # 连接池
c_engine: {status, detail}         # C++ 引擎
vector: {status, detail}           # HNSW 向量索引
graph: {status, nodes, edges, orphan_rate}
```

```bash
mw stats              # 知识库进化统计
mw index              # 记忆路由表（分类统计）
mw index -c "安全类"   # 按分类过滤
```

## 进化系统

```bash
mw evolve                          # 全量：衰减+冷热候选+纠正检测
mw evolve --apply                  # 自动应用升降级和固化纠正
mw evolve --tier-only              # 只做层级变更，不检测纠正
mw evolve --cold-days 30           # 冷候选天数阈值（默认30）
mw evolve --cold-max-weight 30     # 冷候选最大权重（默认30）
mw evolve --hot-min-weight 80      # 热候选最小权重（默认80）
mw evolve --pattern "正则"         # 只处理指定模式
```

流程：`decay_weights()`（30天×0.8）→ 冷热候选 → 待确认纠正 → `--apply` 自动升降级+固化

层级：hot / warm（默认） / cold

## 单独衰减

```bash
mw decay                     # 衰减长期未访问的记忆，默认0.8
mw decay --factor 0.9        # 自定义衰减系数
mw decay --min-weight 10     # 最低权重（默认10）
mw decay --decay-days 30     # 衰减周期天数（默认30）
```

## 纠正反射

```bash
mw reflect "重复犯的错" "纠正总结"           # pattern/summary 是 positional args
mw reflect "总是忘加错误处理" "写前先加 try" --context "Python 代码"
```

同 pattern 累加 count，count ≥ 3 时提示固化为规则。

## 知识库整理

```bash
mw rebuild-links               # 重建孤立记忆的关联（无 cross_ref 的）
mw rebuild-links --full        # 全量重建所有关联
mw rebuild-links --dry-run     # 预览模式

mw crawl                       # 扫描未链接提及，自动建 cross_ref
mw crawl --full                # 全量扫描

mw reorganize                  # 自动整理旧记忆（Agent风格分类规划）
mw reorganize --limit 100      # 处理数量（默认50）
mw reorganize --all            # 处理所有
mw reorganize --dry-run        # 预览模式

mw promote                     # 符合条件的 project 记忆晋升为 global
mw promote --min-weight 100    # 最小权重（默认100）
mw promote --min-access 10     # 最小访问次数（默认10）
mw promote --dry-run           # 预览模式

mw rebuild-fts5                # 重建 FTS5 索引
```

## 清理

```bash
mw cleanup --test              # 清理测试数据
mw cleanup --stale             # 清理过期记忆
mw cleanup --all               # 清理全部
mw cleanup --hard              # 物理删除（默认软删除）
mw cleanup --dry-run           # 预览模式
```

## 向量索引

```bash
mw vector-build                # 构建/刷新 HNSW 向量索引
mw vector-status               # 查看向量索引状态
mw vector-preload              # 预加载向量模型
```

## 图谱维护

```bash
mw graph-stats                 # 图谱健康度统计
mw graph-traverse <doc_id>     # BFS 遍历图谱
mw export-dot                  # 导出 DOT 格式（可视化用）
```

## 备份与同步

```bash
mw backup                      # 备份数据库
mw sync                        # SQLite ↔ MD/JSON 双向同步
mw sync -d sqlite_to_md        # 仅 SQLite → MD
mw sync -d md_to_sqlite        # 仅 MD → SQLite
```

## 日志

```bash
mw log                         # 查看全部进化日志
mw log --type correction       # 只看纠正记录
mw log --type evolution        # 只看进化事件
mw log --type tier             # 只看层级变更
```

## 清理规则

遇上记忆膨胀时手动执行：

1. `mw evolve --apply` — 自动升降级+固化纠正
2. `mw cleanup --stale` — 清理过期记忆
3. `mw rebuild-links` — 修复断链
4. `mw rebuild-fts5` — 重建搜索索引
