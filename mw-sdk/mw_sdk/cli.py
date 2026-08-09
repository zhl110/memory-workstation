"""CLI 入口: mw search / mw list / mw export / mw rules / mw entities / mw ingest

所有 MW 操作统一走 mw CLI，不调脚本、不写 raw SQL、不暴露数据库路径。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path


def get_agent_db(agent_id: str | None = None) -> str:
    """获取数据库路径

    三个Agent（Claude/MiMo/Codex）共用 meta_agents.sqlite。
    agent_id 参数保持向后兼容，但不再影响数据库选择。
    """
    from .utils import _DEFAULT_DATA_DIR
    base = os.environ.get("MW_DATA_DIR", _DEFAULT_DATA_DIR)
    return os.path.join(base, "meta_agents.sqlite")


# === 命令组函数 ===

def _cmd_search(client, args):
    """搜索记忆（SQLite + 可选 MD 文件）"""
    if getattr(args, "mode", None) and args.mode != "rrf":
        client.set_mode(args.mode)
    results = client.search(args.query, args.top_k, explain=args.explain,
                            enable_vector=not args.no_vector, enable_graph=not args.no_graph,
                            extra_keywords=args.extra or None)
    print(json.dumps(results, ensure_ascii=False, indent=2))

    if args.include_md:
        md_dir = Path(client._db_path).parent / "memory_export_all"
        if not md_dir.is_dir():
            print(f"\n⚠️  MD 导出目录不存在: {md_dir}", file=sys.stderr)
            return
        md_matches = _search_md_files(md_dir, args.query, args.top_k)
        if md_matches:
            print("\n--- MD 文件匹配 ---")
            for fname, lineno, matched in md_matches:
                print(f"  {fname}:{lineno}  {matched[:120]}")


def _search_md_files(md_dir: Path, query: str, top_k: int) -> list[tuple[str, int, str]]:
    """搜索 MD 导出目录，优先用外部工具（rg/findstr），回退 Python 逐行。"""
    import subprocess
    try:
        if sys.platform == "win32":
            cmd = ["findstr", "/S", "/I", "/N", query, "*.md"]
            proc = subprocess.run(cmd, cwd=md_dir, capture_output=True, timeout=10)
            proc.stdout = proc.stdout.decode("utf-8", errors="replace")
        else:
            cmd = ["rg", "-i", "-n", "--", query, "--include", "*.md"]
            proc = subprocess.run(cmd, cwd=md_dir, capture_output=True, text=True, timeout=10)
        if proc.returncode in (0, 1):
            lines = [l for l in proc.stdout.splitlines() if l.strip()][:top_k]
            results = []
            for line in lines:
                parts = line.split(":", 2)
                if len(parts) == 3:
                    results.append((parts[0].strip(), int(parts[1]), parts[2].strip()))
                elif len(parts) >= 2:
                    results.append((parts[0].strip(), int(parts[1]), ""))
            if results:
                return results
    except Exception:
        pass
    # Fallback: Python 逐行读取
    results = []
    q = query.lower()
    for fpath in sorted(md_dir.glob("*.md")):
        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if q in line.lower():
                results.append((fpath.name, i, line.strip()))
                if len(results) >= top_k:
                    return results
    return results


def _cmd_search_links(client, args):
    """知识图谱搜索"""
    results = client.get_all_related(args.query, args.top_k, max_results=args.max_results)
    print(json.dumps(results, ensure_ascii=False, indent=2))


def _cmd_rules_search(client, args):
    """根据意图搜索全局规则"""
    rules = client.search_rules_by_intent(args.intent, top_k=args.top_k)
    print(f"\n📖 意图 '{args.intent}' 相关规则\n")
    if not rules:
        print("未找到匹配的规则")
    else:
        for r in rules:
            priority = r.get("priority", r.get("importance", "?"))
            summary = r.get("rule_text", r.get("summary", ""))[:80]
            confidence = r.get("confidence", 0)
            print(f"  [{priority}] (置信度:{confidence:.1f}) {summary}")


def _cmd_list(client, args):
    """列出所有记忆"""
    _cmd_list_impl(client, args.category, args.limit)


def _cmd_list_impl(client, category: str, limit: int):
    """列出所有记忆，按分类分组，人类可读"""
    conn = client._conn

    if category:
        rows = conn.execute(
            "SELECT doc_id, label, importance, weight, compact_content, content_category, sub_category "
            "FROM memory_classify "
            "WHERE content_category LIKE ? "
            "ORDER BY weight DESC, doc_id DESC LIMIT ?",
            (f"%{category}%", limit),
        ).fetchall()
        groups = {"": [dict(r) for r in rows]}
    else:
        rows = conn.execute(
            "SELECT doc_id, label, importance, weight, compact_content, content_category, sub_category "
            "FROM memory_classify "
            "ORDER BY content_category, weight DESC, doc_id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        groups: dict[str, list] = {}
        for r in rows:
            d = dict(r)
            cat = d["content_category"] or "未分类"
            groups.setdefault(cat, []).append(d)

    print(f"\n📋 记忆列表（共 {len(rows)} 条）\n")
    for cat, items in groups.items():
        print(f"  ── {cat} ({len(items)}条) ──")
        for item in items:
            summary = (item["compact_content"] or "")[:80]
            print(f"    #{item['doc_id']:>5}  [{item['importance']}/{item['weight']}] {item['label']}")
            print(f"           {summary}")
        print()


def _cmd_export(client, args):
    """导出为 Markdown 文件"""
    output_dir = args.output_dir
    if not output_dir:
        from .utils import _DEFAULT_DATA_DIR
        output_dir = str(Path(_DEFAULT_DATA_DIR) / "memory_export_all")
    count = client.export_md(output_dir)
    print(f"✅ 已导出 {count} 条记忆到 {output_dir}")
    typora = f"{output_dir}/INDEX.md"
    print(f"   打开 {typora} 用 Typora/Obsidian 查看分类导航")


def _cmd_import(client, args):
    """从 Markdown 文件夹导入记忆"""
    count = client.import_md(args.folder, dry_run=args.dry_run)
    if args.dry_run:
        print(f"\n🔍 DRY RUN 模式，预览导入 {count} 条记忆")
    else:
        print(f"\n✅ 已导入 {count} 条记忆")


def _cmd_update(client, args):
    """更新记忆字段"""
    updates = []
    if args.scope:
        updates.append(("scope", args.scope))
    if args.category:
        updates.append(("content_category", args.category))
    if args.keywords:
        updates.append(("keywords", args.keywords))

    if not updates:
        print("请指定要更新的字段：--scope, --category, --keywords")
        sys.exit(1)

    for field, value in updates:
        client.update_classify_field(args.doc_id, field, value)
    print(f"✅ 已更新 #{args.doc_id}: {', '.join(f'{f}={v}' for f, v in updates)}")


def _cmd_ingest(client, args, db_path):
    """全流程写入记忆"""
    from .cli_ingest import run_ingest
    args.db = db_path
    run_ingest(args)


def _cmd_crossref(client, args):
    """存量记忆批量建双向关联"""
    _cmd_crossref_impl(client, args.top_k, args.max_docs, args.dry_run)


def _cmd_crossref_impl(client, top_k: int, max_docs: int, dry_run: bool):
    """存量记忆批量建双向关联（entity 共享 + 同 category）"""
    conn = client._conn

    ids = conn.execute(
        "SELECT doc_id FROM memory_classify WHERE compact_content != '' ORDER BY doc_id"
    ).fetchall()
    all_ids = [r["doc_id"] for r in ids]
    if max_docs > 0:
        all_ids = all_ids[:max_docs]

    total_new = 0
    skip = 0
    for i, doc_id in enumerate(all_ids):
        candidates = client._find_cross_ref_candidates(doc_id, top_k)
        if not candidates:
            skip += 1
            continue

        if dry_run:
            print(f"  #[{i+1}/{len(all_ids)}] #{doc_id} → {len(candidates)} 条: {[c['doc_id'] for c in candidates]}")
        else:
            n = client.auto_cross_ref(doc_id, candidates=candidates, top_k=top_k)
            total_new += n

        if (i + 1) % 20 == 0:
            print(f"  进度: [{i+1}/{len(all_ids)}] 累计新增 {total_new} 条边")

    print(f"\n{'🔍 DRY RUN' if dry_run else '✅ 已完成'}")
    print(f"  处理记忆: {len(all_ids)} 条")
    print(f"  跳过（无候选）: {skip} 条")
    print(f"  新增双向边: {total_new} 条")
    n_total = conn.execute("SELECT COUNT(*) FROM memory_cross_ref").fetchone()[0]
    print(f"  cross_ref 总计: {n_total} 条")


def _cmd_crawl(client, args):
    """知识图谱扫描"""
    result = client.crawl_cross_ref(
        top_k=args.top_k,
        incremental=not args.full,
        scan_mentions=not args.no_mentions,
    )
    print(f"✅ 知识图谱扫描完成")
    print(f"  处理: {result['processed']} 条记忆")
    print(f"  新增边: {result['new_edges']} 条")
    print(f"  跳过: {result['skipped']} 条")
    print(f"  cross_ref 总计: {result['total_edges']} 条")


def _cmd_rebuild_links(client, args):
    """重建知识图谱关联"""
    print("\n🔄 知识图谱重建\n")
    result = client.rebuild_links(full=args.full, dry_run=args.dry_run)
    print(f"扫描记忆: {result['total']} 条")
    print(f"{'预览' if args.dry_run else '处理'}: {result['processed']} 条")
    print(f"新增边: {result['new_edges']} 条")
    print(f"跳过: {result['skipped']} 条")
    if args.dry_run:
        print("\n🔍 DRY RUN 模式，未写入任何数据")
    else:
        print("\n✅ 完成")


def _cmd_graph_stats(client, args):
    """图谱健康度统计"""
    graph_stats = client.get_graph_stats()
    if args.format == "json":
        output = json.dumps(graph_stats, ensure_ascii=False, indent=2)
    else:
        output = _format_graph_stats(graph_stats)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"✅ 图谱统计已写入 {args.output}")
    else:
        print(output)


def _format_graph_stats(stats: dict) -> str:
    """格式化图谱统计为人类可读文本"""
    lines = [
        "\n📊 图谱健康度统计\n",
        f"节点总数: {stats['total_nodes']}",
        f"边总数:   {stats['total_edges']}",
        f"平均度数: {stats['avg_degree']}",
        f"孤立节点: {stats['orphan_count']} ({stats['orphan_rate']*100:.1f}%)",
        "\n边类型分布:",
    ]
    for rel_type, count in stats.get('edge_type_distribution', {}).items():
        lines.append(f"  {rel_type}: {count}")
    return "\n".join(lines)


def _cmd_export_dot(client, args):
    """导出图谱为 DOT 格式"""
    dot_content = _export_dot(client, args.max_nodes)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(dot_content)
        print(f"✅ DOT 文件已导出到 {args.output}")
    else:
        print(dot_content)


def _export_dot(client, max_nodes: int = 100) -> str:
    """导出图谱为 DOT 格式（无向图，双向关联）"""
    conn = client._conn

    nodes = conn.execute(
        "SELECT doc_id, compact_content, importance FROM memory_classify "
        "WHERE compact_content != '' ORDER BY doc_id LIMIT ?",
        (max_nodes,)
    ).fetchall()

    node_ids = {n["doc_id"] for n in nodes}

    edge_limit = max_nodes * 3
    edges = conn.execute(
        "SELECT doc_id, related_doc_id, relation_type FROM memory_cross_ref "
        "WHERE doc_id < related_doc_id LIMIT ?",
        (edge_limit,)
    ).fetchall()

    lines = ["graph MemoryGraph {", "  rankdir=LR;", "  node [shape=box];", ""]

    for node in nodes:
        label = (node["compact_content"] or "")[:30].replace('"', '\\"')
        color = {"P0": "red", "P1": "orange", "P2": "yellow", "P3": "lightgray"}.get(
            node["importance"], "white"
        )
        lines.append(f'  {node["doc_id"]} [label="{label}" fillcolor={color} style=filled];')

    lines.append("")

    for edge in edges:
        if edge["doc_id"] in node_ids and edge["related_doc_id"] in node_ids:
            rel_type = edge["relation_type"]
            lines.append(f'  {edge["doc_id"]} -- {edge["related_doc_id"]} [label="{rel_type}"];')

    lines.append("}")
    return "\n".join(lines)


def _cmd_graph_traverse(client, args):
    """BFS 图遍历"""
    if args.by_hop:
        by_hop = client.bfs_by_hop(args.doc_id, args.hops, args.relation or "")
        print(f"\n🔍 BFS 图遍历（节点 {args.doc_id}，最大 {args.hops} 跳）\n")
        for hop in sorted(by_hop.keys()):
            items = by_hop[hop]
            doc_ids = [str(item["doc_id"]) for item in items]
            print(f"  Hop {hop}: [{', '.join(doc_ids)}]")
    else:
        result = client.bfs_traverse(args.doc_id, args.hops, args.relation or "")
        print(f"\n🔍 BFS 图遍历（节点 {args.doc_id}，最大 {args.hops} 跳）\n")
        if not result:
            print("  无可达节点")
        else:
            for item in result:
                path_str = " → ".join(str(p) for p in item["path"])
                print(f"  Hop {item['hop']}: #{item['doc_id']} (via {item['relation_type']}) [{path_str}]")


def _cmd_vector_search(client, args):
    """向量搜索"""
    if not client.vector_available:
        print("⚠️  向量搜索不可用（HNSW 索引未构建，请先执行 mw vector-build）")
        sys.exit(1)
    results = client.vector_search(args.query, args.top_k)
    print(json.dumps(results, ensure_ascii=False, indent=2))


def _cmd_vector_status(client, args):
    """查看向量搜索状态"""
    print("\n📊 向量搜索状态\n")
    has_index = client.vector_available
    print(f"可用: {'✅ 是' if has_index else '❌ 否'}")
    if has_index:
        print(f"引擎: C++ HNSW")
        stats = client.get_vector_stats()
        print(f"已索引: {stats.get('indexed', 0)} 条")
    else:
        print("引擎: C++ HNSW（未构建索引）")


def _cmd_vector_build(client, args):
    """构建向量索引"""
    if args.stats:
        stats = client.get_vector_stats()
        print(f"\n📊 向量索引统计\n")
        print(f"已索引: {stats.get('indexed', 0)} 条")
    else:
        stats = client.get_vector_stats()
        if stats.get('indexed', 0) == 0:
            print("⚠️  没有嵌入向量数据（请先执行 mw ingest 导入记忆）")
            sys.exit(1)

        def progress_cb(current, total, msg):
            print(f"  [{current}/{total}] {msg}")

        print("\n🔨 构建向量索引...\n")
        result = client.build_vector_index(callback=progress_cb)
        print(f"\n✅ 完成：构建 {result['built']}，跳过 {result['skipped']}，失败 {result['errors']}")


def _cmd_vector_preload(client, args):
    """预加载向量模型"""
    client.preload_vector_model()


def _cmd_decay(client, args):
    """衰减权重"""
    n = client.decay_weights(args.factor, args.min_weight, args.decay_days)
    print(f"✅ 已衰减 {n} 条记忆的权重")


def _cmd_evolve(client, args):
    """进化：冷热候选 + 纠正检测"""
    cold, hot, pending = [], [], []

    if not args.pattern:
        n = client.decay_weights()
        print(f"✅ 权重衰减完成：{n} 条记忆受影响")

    if not args.pattern:
        candidates = client.get_candidates(
            scope="own",
            cold_days=args.cold_days,
            cold_max_weight=args.cold_max_weight,
            hot_min_weight=args.hot_min_weight,
        )
        cold = candidates.get("cold", [])
        hot = candidates.get("hot", [])
        print(f"\n❄ 冷候选：{len(cold)} 条（weight<={args.cold_max_weight}，{args.cold_days}天未访问）")
        for c in cold[:5]:
            print(f"  #{c['doc_id']} {c.get('label', '')[:30]} (weight={c.get('weight', 0)})")
        print(f"\n🔥 热候选：{len(hot)} 条（weight>={args.hot_min_weight}，P0/P1）")
        for c in hot[:5]:
            print(f"  #{c['doc_id']} {c.get('label', '')[:30]} (weight={c.get('weight', 0)})")

    if not args.tier_only:
        pending = client.get_correction_pending(min_count=3)
        print(f"\n📝 待确认纠正：{len(pending)} 条")
        for p in pending[:5]:
            print(f"  - {p.get('pattern', '')}（出现 {p.get('count', 0)} 次）")

    if args.apply:
        print("\n🔄 应用建议...")
        for c in cold:
            client.apply_tier_change(c["doc_id"], "warm", "cold", "自动降级: 长期未访问")
        for c in hot:
            client.apply_tier_change(c["doc_id"], "warm", "hot", "自动升级: 高频高权重")
        for p in pending:
            client.promote_correction(p.get("pattern", ""))
            client.log_event("correction", "evolve:apply", detail=p.get("pattern", ""), certainty=1.0)
        print(f"✅ 完成：降级{len(cold)}条 | 升级{len(hot)}条 | 固化{len(pending)}条")
    else:
        print("\n提示：用 --apply 应用升降级和固化纠正")


def _cmd_reflect(client, args):
    """记录纠正模式"""
    result = client.increment_correction(args.pattern, args.summary, args.context)
    count = result.get("count", 1)
    is_new = result.get("is_new", False)
    client.log_event("correction", "reflect", detail=args.summary, certainty=1.0)
    if is_new:
        print(f"✅ 已记录新模式：{args.pattern}")
    else:
        print(f"✅ 已更新模式：{args.pattern}（第 {count} 次）")
    if count >= 3:
        print(f"⚠️  此模式已出现 {count} 次，建议 mw evolve --apply 固化")


def _cmd_promote(client, args):
    """晋升记忆"""
    if args.dry_run:
        candidates = client.get_promotion_candidates(args.min_weight, args.min_access)
        print(f"\n🔍 DRY RUN 模式")
        print(f"晋升条件: weight >= {args.min_weight}, access >= {args.min_access}")
        print(f"可晋升: {len(candidates)} 条")
        for c in candidates[:10]:
            content = (c.get("compact_content") or "")[:50]
            print(f"  #{c['doc_id']}: weight={c['weight']}, access={c['access_count']}, {content}...")
    else:
        count = client.promote_to_global(args.min_weight, args.min_access)
        print(f"\n✅ 已晋升 {count} 条记忆为 global")


def _cmd_reindex(client, args):
    """重建 FTS5 索引"""
    if not args.confirm:
        print("⚠️  重建 FTS5 索引会删除旧索引并全量重建。")
        print("   确认请加 --confirm 参数")
        sys.exit(0)
    print("  重建 FTS5 索引（DROP + re-create + populate）...")
    n = client.rebuild_fts5_index()
    print(f"✅ 重建完成：{n} 条记忆已索引")


def _cmd_rebuild_fts5(client, args):
    """重建 FTS5 索引"""
    if client._rebuild_fts5():
        print("FTS5重建成功")
    else:
        print("FTS5重建失败")


def _cmd_cleanup(client, args):
    """清理测试数据"""
    mode = "all"
    if args.test:
        mode = "test"
    elif args.stale:
        mode = "stale"
    elif not args.cleanup_all:
        print("请指定清理模式: --test, --stale, 或 --all")
        sys.exit(1)

    print(f"\n🧹 清理{'测试数据' if mode == 'test' else '过期记忆' if mode == 'stale' else '所有问题'}\n")
    result = client.cleanup_memories(mode=mode, hard=args.hard, dry_run=args.dry_run)
    print(f"测试数据: {result['test_count']} 条")
    print(f"过期记忆: {result['stale_count']} 条")
    print(f"{'预览' if args.dry_run else '已清理'}: {result['deleted']} 条")
    if args.dry_run:
        print("\n🔍 DRY RUN 模式，未写入任何数据")
    elif args.hard:
        print("\n⚠️ 已物理删除，数据不可恢复")
    else:
        print("\n✅ 已软删除（标记 is_deleted=1）")


def _cmd_lint(client, args):
    """健康度检查（已废弃）"""
    print("❌ lint 功能需要 Python fallback 模块（已移除）")
    print("   请使用 C++ 版本或手动检查数据库")
    sys.exit(1)


def _cmd_health(client, args):
    """检查各组件健康状态"""
    health = client.health_check()
    status_icon = {"ok": "✅", "warning": "⚠️", "error": "❌"}
    print("\n🔍 MW 组件健康检查\n")
    for component, info in health.items():
        icon = status_icon.get(info.get("status", "error"), "❓")
        detail = info.get("detail", info.get("status", ""))
        print(f"  {icon} {component}: {detail}")
        for k, v in info.items():
            if k not in ("status", "detail"):
                print(f"       {k}: {v}")
    print()


def _cmd_stats(client, args):
    """显示知识库进化统计"""
    stats = client.get_evolution_stats()
    total_mem = client.get_total_count()
    has_content = client.get_content_count()
    print("\n📊 知识库统计\n")
    print(f"总记忆数: {total_mem} 条")
    print(f"有内容:   {has_content} 条")
    print(f"\n进化统计:")
    print(f"  纠正记录: {stats['corrections_total']} 条")
    print(f"  待确认:   {stats['corrections_pending']} 条 (出现≥3次，建议固化)")
    print(f"  已固化:   {stats['corrections_promoted']} 条")
    print(f"  进化事件: {stats['evolution_events']} 次")
    print(f"  层级变更: {stats['tier_changes']} 次")
    print(f"\n层级分布:")
    for tier, count in stats.get('by_tier', {}).items():
        print(f"  {tier}: {count} 条")


def _cmd_log(client, args):
    """进化日志"""
    if args.type == "correction":
        logs = client.list_corrections(limit=args.limit)
    elif args.type == "evolution":
        logs = client.get_evolution_log(event_type="evolution", limit=args.limit)
    elif args.type == "tier":
        logs = client.get_tier_history(limit=args.limit)
    else:
        corrections = client.list_corrections(limit=args.limit // 2)
        evolution = client.get_evolution_log(limit=args.limit // 2)
        logs = corrections + evolution

    print(f"\n📋 进化日志（最近 {len(logs)} 条）\n")
    print(f"{'类型':<12} | {'详情':<40} | {'时间'}")
    print("-" * 80)
    for log in logs:
        log_type = log.get("event_type", log.get("pattern", "unknown"))
        detail = log.get("detail", log.get("summary", ""))[:40]
        time = log.get("event_time", log.get("created_at", ""))
        print(f"{log_type:<12} | {detail:<40} | {time}")


def _cmd_index(client, args):
    """记忆路由表"""
    cat_stats = client.get_category_stats(args.category)
    total = sum(s["count"] for s in cat_stats)
    print(f"\n📋 记忆路由表（共 {total} 条）\n")

    for stat in cat_stats:
        cat = stat["category"]
        cnt = stat["count"]
        print(f"{cat} ({cnt}条):")
        items = client.get_category_items(cat, limit=5)
        for item in items:
            print(f"  {item['importance']} | #{item['doc_id']} {item['label']}")
        print()

    print("分类统计：")
    for stat in cat_stats:
        print(f"  {stat['category']}: {stat['count']}")
    print(f"\n  总计: {total}")

    importance_stats = client.get_importance_stats()
    print("\n重要性分布：")
    for stat in importance_stats:
        print(f"  {stat['importance']}: {stat['count']}条")


def _cmd_sync(client, args):
    """SQLite ↔ MD/JSON 双向同步"""
    from .sync import MemorySync
    from .utils import _DEFAULT_DATA_DIR

    # 获取导出目录
    base = os.environ.get("MW_DATA_DIR", _DEFAULT_DATA_DIR)
    export_dir = os.path.join(base, "memory_export_all")

    sync = MemorySync(get_agent_db(), export_dir)

    print(f"\n🔄 执行双向同步...")
    print(f"  数据库: {get_agent_db()}")
    print(f"  导出目录: {export_dir}")
    print()

    results = sync.sync_all(direction=args.direction)

    print(f"\n✅ 同步完成:")
    print(f"  SQLite → MD: {results['sqlite_to_md']} 条")
    print(f"  MD → SQLite: {results['md_to_sqlite']} 条")


def _cmd_reorganize(client, args):
    """整理旧记忆"""
    _cmd_reorganize_impl(client, args.limit, args.reorganize_all, args.dry_run)


def _cmd_reorganize_impl(client, limit: int, reorganize_all: bool, dry_run: bool):
    """整理旧记忆（Agent自己分类规划）"""
    conn = client._conn

    if reorganize_all:
        rows = conn.execute(
            "SELECT doc_id, label, importance, weight, compact_content, content_category, "
            "sub_category, scope, scene, emotion "
            "FROM memory_classify "
            "WHERE compact_content != '' "
            "ORDER BY doc_id"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT doc_id, label, importance, weight, compact_content, content_category, "
            "sub_category, scope, scene, emotion "
            "FROM memory_classify "
            "WHERE compact_content != '' "
            "ORDER BY doc_id DESC LIMIT ?",
            (limit,)
        ).fetchall()

    if not rows:
        print("没有需要整理的记忆")
        return

    print(f"\n整理旧记忆（共 {len(rows)} 条）\n")
    if dry_run:
        print("[预览模式] 不会实际修改\n")

    stats = {
        "total": len(rows),
        "updated": 0,
        "skipped": 0,
        "label_changed": 0,
        "scene_added": 0,
        "emotion_added": 0,
    }

    # content_category → label 映射（优先级高于关键词匹配）
    category_to_label = {
        "安全类": "规则", "执行规范": "规则", "沟通规范": "规则",
        "工作流程": "经验", "踩坑记录": "经验", "优化经验": "经验",
        "Bug修复": "bug-fix", "错误排查": "bug-fix",
        "架构设计": "架构决策", "技术选型": "架构决策",
        "项目记录": "项目记录", "版本记录": "项目记录",
    }

    label_rules = {
        "规则": ["必须", "禁止", "不允许", "规定", "规范", "要求", "不要", "别再", "严禁", "强制"],
        "经验": ["踩坑", "经验", "教训", "发现", "优化", "改进", "解决", "总结", "反思"],
        "bug-fix": ["bug", "修复", "fix", "错误", "报错", "异常", "崩溃", " traceback", "error"],
        "架构决策": ["选型", "架构", "框架", "方案", "设计", "决定", "trade-off", "对比", "评估"],
        "项目记录": ["完成", "进度", "里程碑", "版本", "发布", "上线", "交付"],
    }

    scene_rules = {
        "code": ["代码", "编程", "开发", "函数", "类", "模块", "API", "bug", "fix", "编译", "构建", "部署"],
        "design": ["设计", "UI", "UX", "界面", "样式", "布局", "配色", "字体", "组件", "交互"],
        "planning": ["计划", "规划", "任务", "进度", "里程碑", "排期", "需求", "排期"],
        "debug": ["调试", "排查", "错误", "报错", "异常", "日志", "定位", "复现"],
        "config": ["配置", "环境", "设置", "参数", "密钥", "token", "环境变量"],
    }

    for row in rows:
        doc_id = row["doc_id"]
        content = row["compact_content"] or ""
        old_label = row["label"] or ""
        old_scene = row["scene"] or ""
        old_emotion = row["emotion"] or ""
        category = row["content_category"] or ""

        # label：只填补空/默认值，不覆盖已有有效 label
        new_label = old_label
        if not old_label or old_label in ("unknown", "session", ""):
            # 优先用 content_category 推断
            if category in category_to_label:
                new_label = category_to_label[category]
            else:
                # 回退到关键词匹配
                for label, keywords in label_rules.items():
                    for kw in keywords:
                        if kw in content:
                            new_label = label
                            break
                    if new_label != old_label:
                        break

        # scene：只填补空值
        new_scene = old_scene
        if not new_scene:
            for scene, keywords in scene_rules.items():
                for kw in keywords:
                    if kw in content:
                        new_scene = scene
                        break
                if new_scene:
                    break

        # emotion：只填补空值
        new_emotion = old_emotion
        if not new_emotion:
            positive_words = ["好的", "不错", "很好", "完美", "成功", "搞定", "通过", "修复"]
            negative_words = ["烦", "麻烦", "头疼", "错误", "失败", "报错", "崩溃", "卡住"]
            for word in positive_words:
                if word in content:
                    new_emotion = "positive"
                    break
            for word in negative_words:
                if word in content:
                    new_emotion = "negative"
                    break

        updates = []
        if new_label != old_label:
            updates.append(("label", new_label))
            stats["label_changed"] += 1
        if new_scene and new_scene != old_scene:
            updates.append(("scene", new_scene))
            stats["scene_added"] += 1
        if new_emotion and new_emotion != old_emotion:
            updates.append(("emotion", new_emotion))
            stats["emotion_added"] += 1

        if updates:
            if dry_run:
                print(f"  #{doc_id}: {', '.join(f'{f}={v}' for f, v in updates)}")
            else:
                for field, value in updates:
                    client.update_classify_field(doc_id, field, value)
                print(f"  #{doc_id}: {', '.join(f'{f}={v}' for f, v in updates)}")
            stats["updated"] += 1
        else:
            stats["skipped"] += 1

    print(f"\n--- 整理完成 ---\n")
    print(f"  总计: {stats['total']} 条")
    print(f"  更新: {stats['updated']} 条")
    print(f"  跳过: {stats['skipped']} 条")
    print(f"  label变更: {stats['label_changed']} 条")
    print(f"  scene添加: {stats['scene_added']} 条")
    print(f"  emotion添加: {stats['emotion_added']} 条")


# === 场景管理 ===

def _cmd_scene(client, args):
    """场景管理"""
    if args.scene_action == "set":
        ok = client.set_scene(args.scene_id, args.name,
                              parent_scene=args.parent or "",
                              description=args.description or "")
        print(f"{'✅' if ok else '❌'} 场景 {args.scene_id}: {args.name}")
    elif args.scene_action == "get":
        r = client.get_scene(args.scene_id)
        if r:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        else:
            print(f"未找到场景: {args.scene_id}")
    elif args.scene_action == "list":
        scenes = client.list_scenes()
        if not scenes:
            print("暂无场景")
        else:
            for s in scenes:
                print(f"  {s.get('scene_id','?'):20s} {s.get('name','?')}")


# === 情绪管理 ===

def _cmd_emotion(client, args):
    """情绪管理"""
    if args.emotion_action == "set":
        ok = client.set_emotion(args.doc_id, args.emotion_type,
                                emotion_detail=args.detail or "",
                                intensity=args.intensity)
        print(f"{'✅' if ok else '❌'} #{args.doc_id} 情绪: {args.emotion_type}")
    elif args.emotion_action == "get":
        r = client.get_emotion(args.doc_id)
        if r:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        else:
            print(f"#{args.doc_id} 无情绪记录")


# === 层级管理 ===

def _cmd_tier(client, args):
    """层级管理"""
    if args.tier_action == "set":
        ok = client.set_tier(args.doc_id, args.tier, reason=args.reason or "")
        print(f"{'✅' if ok else '❌'} #{args.doc_id} 层级: {args.tier}")
    elif args.tier_action == "get":
        tier = client.get_tier(args.doc_id)
        print(f"#{args.doc_id} 层级: {tier or '(未设置)'}")


# === 归档 ===

def _cmd_archive(client, args):
    """归档记忆"""
    ok = client.archive_memory(args.doc_id, reason=args.reason or "")
    print(f"{'✅' if ok else '❌'} #{args.doc_id} 已归档")


# === 删除 ===

def _cmd_forget(client, args):
    """删除记忆"""
    if not args.confirm:
        print(f"⚠️  确认删除 #{args.doc_id}？加 --confirm 确认")
        sys.exit(1)
    ok = client.forget_memory(args.doc_id, reason=args.reason or "")
    print(f"{'✅' if ok else '❌'} #{args.doc_id} 已删除")


# === 始终加载 ===

def _cmd_always_load(client, args):
    """始终加载管理"""
    import json as _json
    if args.always_action == "set":
        row = client._conn.execute(
            "SELECT meta FROM memory_classify WHERE doc_id = ?", (args.doc_id,)
        ).fetchone()
        if not row:
            print(f"❌ #{args.doc_id} 不存在")
            sys.exit(1)
        meta = _json.loads(row[0] or "{}")
        meta["always_load"] = 1
        client._conn.execute(
            "UPDATE memory_classify SET meta = ? WHERE doc_id = ?",
            (_json.dumps(meta), args.doc_id)
        )
        client._conn.commit()
        print(f"✅ #{args.doc_id} 已设为始终加载")
    elif args.always_action == "get":
        rows = client._conn.execute(
            """SELECT doc_id, summary FROM memory_classify
               WHERE json_extract(meta, '$.always_load') = 1
               AND compact_content != ''"""
        ).fetchall()
        if not rows:
            print("无始终加载记忆")
        else:
            for r in rows:
                print(f"  #{r[0]:4d}  {(r[1] or '')[:60]}")
    elif args.always_action == "unset":
        row = client._conn.execute(
            "SELECT meta FROM memory_classify WHERE doc_id = ?", (args.doc_id,)
        ).fetchone()
        if not row:
            print(f"❌ #{args.doc_id} 不存在")
            sys.exit(1)
        meta = _json.loads(row[0] or "{}")
        meta["always_load"] = 0
        client._conn.execute(
            "UPDATE memory_classify SET meta = ? WHERE doc_id = ?",
            (_json.dumps(meta), args.doc_id)
        )
        client._conn.commit()
        print(f"✅ #{args.doc_id} 已取消始终加载")


# === 备份 ===

def _cmd_backup(client, args):
    """备份数据库"""
    ok = client.backup(args.dir)
    print(f"{'✅' if ok else '❌'} 备份到: {args.dir}")


# === 会话状态 ===

def _cmd_session(client, args):
    """会话状态管理"""
    if args.session_action == "save":
        ok = client.save_session_state(
            args.agent_name,
            session_id=args.session_id or "",
            last_topic=args.topic or "",
            unfinished_tasks=args.tasks or "[]",
            emotion_state=args.emotion or "",
        )
        print(f"{'✅' if ok else '❌'} 会话状态已保存: {args.agent_name}")
    elif args.session_action == "get":
        r = client.get_session_state(args.agent_name, args.session_id or "")
        if r:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        else:
            print(f"未找到会话状态: {args.agent_name}")


# === 有效期 ===

def _cmd_valid_time(client, args):
    """设置记忆有效期"""
    ok = client.set_valid_time(args.doc_id,
                               valid_from=args.from_date or "",
                               valid_until=args.until or "")
    print(f"{'✅' if ok else '❌'} #{args.doc_id} 有效期: {args.from_date or '*'} ~ {args.until or '*'}")


# === 关联管理 ===

def _cmd_link(client, args):
    """创建记忆关联"""
    conn = client._conn

    # 确保 weight 列存在
    cols = {row[1] for row in conn.execute("PRAGMA table_info(memory_cross_ref)").fetchall()}
    if "weight" not in cols:
        conn.execute("ALTER TABLE memory_cross_ref ADD COLUMN weight REAL DEFAULT 1.0")
        conn.commit()

    # 验证两个 doc_id 都存在
    for did in [args.source_id, args.target_id]:
        row = conn.execute("SELECT doc_id FROM memory_classify WHERE doc_id = ?", (did,)).fetchone()
        if not row:
            print(f"❌ #{did} 不存在")
            sys.exit(1)

    # 检查是否已有关联
    existing = conn.execute(
        "SELECT weight FROM memory_cross_ref WHERE doc_id = ? AND related_doc_id = ?",
        (args.source_id, args.target_id)
    ).fetchone()

    if existing:
        # 更新权重
        conn.execute(
            "UPDATE memory_cross_ref SET weight = ?, note = ? WHERE doc_id = ? AND related_doc_id = ?",
            (args.weight, args.note or "", args.source_id, args.target_id)
        )
        action = "更新"
    else:
        # 插入新关联
        conn.execute(
            "INSERT INTO memory_cross_ref (doc_id, related_doc_id, relation_type, weight, note) VALUES (?, ?, 'related', ?, ?)",
            (args.source_id, args.target_id, args.weight, args.note or "")
        )
        action = "创建"

    conn.commit()
    print(f"✅ 已{action}关联: #{args.source_id} → #{args.target_id} (权重: {args.weight})")


# === 命令注册表 ===

COMMANDS = {
    # 搜索域
    "search": _cmd_search,
    "search-links": _cmd_search_links,
    "rules-search": _cmd_rules_search,
    # CRUD 域
    "list": _cmd_list,
    "export": _cmd_export,
    "import": _cmd_import,
    "update": _cmd_update,
    "ingest": _cmd_ingest,
    # 图谱域
    "cross-ref": _cmd_crossref,
    "crawl": _cmd_crawl,
    "rebuild-links": _cmd_rebuild_links,
    "graph-stats": _cmd_graph_stats,
    "export-dot": _cmd_export_dot,
    "graph-traverse": _cmd_graph_traverse,
    # 向量域
    "vector-search": _cmd_vector_search,
    "vector-status": _cmd_vector_status,
    "vector-build": _cmd_vector_build,
    "vector-preload": _cmd_vector_preload,
    # 进化域
    "decay": _cmd_decay,
    "evolve": _cmd_evolve,
    "reflect": _cmd_reflect,
    "promote": _cmd_promote,
    "reorganize": _cmd_reorganize,
    # 记忆管理域
    "scene": _cmd_scene,
    "emotion": _cmd_emotion,
    "tier": _cmd_tier,
    "archive": _cmd_archive,
    "forget": _cmd_forget,
    "always-load": _cmd_always_load,
    "backup": _cmd_backup,
    "session": _cmd_session,
    "valid-time": _cmd_valid_time,
    "link": _cmd_link,
    # 维护域
    "reindex": _cmd_reindex,
    "rebuild-fts5": _cmd_rebuild_fts5,
    "cleanup": _cmd_cleanup,
    "lint": _cmd_lint,
    "health": _cmd_health,
    "stats": _cmd_stats,
    "log": _cmd_log,
    "index": _cmd_index,
    "sync": _cmd_sync,
}


def main():
    parser = argparse.ArgumentParser(prog="mw", description="Memory Workstation CLI")
    parser.add_argument("--db", default=None, help="meta.sqlite 路径（默认由 --agent 或环境变量决定）")
    parser.add_argument("--agent", default=None, help="Agent ID（claude/mimo/codex），决定使用哪个数据库")
    sub = parser.add_subparsers(dest="cmd")

    # 搜索域
    p_search = sub.add_parser("search", help="搜索记忆")
    p_search.add_argument("query", help="搜索关键词")
    p_search.add_argument("-n", "--top-k", type=int, default=5)
    p_search.add_argument("--explain", action="store_true", help="显示匹配详情")
    p_search.add_argument("--no-vector", action="store_true", help="关闭向量语义搜索（默认启用）")
    p_search.add_argument("--no-graph", action="store_true", help="关闭图谱关联展开（默认启用）")
    p_search.add_argument("--extra", nargs="*", default=[], help="额外关键词列表，OR 语义扩大覆盖")
    p_search.add_argument("--include-md", action="store_true", help="同时搜索导出的 MD 文件（原文精确匹配）")
    p_search.add_argument("--mode", choices=["rrf", "hybrid"], default="rrf",
                          help="搜索模式：rrf（默认，稳定）或 hybrid（权重参与排序，新/高权重记忆更易浮出）")

    p_search_links = sub.add_parser("search-links", help="知识图谱搜索（返回所有关联记忆）")
    p_search_links.add_argument("query", help="搜索关键词")
    p_search_links.add_argument("-n", "--top-k", type=int, default=5)
    p_search_links.add_argument("--max-results", type=int, default=20, help="最大返回数量")

    p_rules_search = sub.add_parser("rules-search", help="根据意图搜索全局规则")
    p_rules_search.add_argument("intent", help="意图: code/deploy/config/architecture/debug/general")
    p_rules_search.add_argument("-n", "--top-k", type=int, default=5)

    # CRUD 域
    p_list = sub.add_parser("list", help="列出所有记忆（按分类分组）")
    p_list.add_argument("-c", "--category", default="", help="只看某个分类")
    p_list.add_argument("-n", "--limit", type=int, default=50)

    p_export = sub.add_parser("export", help="导出为 Markdown 文件")
    p_export.add_argument("output_dir", default="", nargs="?",
                          help="输出目录（默认导出到 D:/MemoryWorkstation/.memory-workstation/memory_export_all/）")

    p_import = sub.add_parser("import", help="从 Markdown 文件夹导入记忆")
    p_import.add_argument("folder", help="Markdown 文件夹路径")
    p_import.add_argument("--dry-run", action="store_true", help="预览模式（不写入数据库）")

    p_update = sub.add_parser("update", help="更新记忆的 scope/category/keywords")
    p_update.add_argument("doc_id", type=int, help="要更新的文档 ID")
    p_update.add_argument("--scope", choices=["global", "project", "session"], help="新的 scope")
    p_update.add_argument("--category", help="新的 category")
    p_update.add_argument("--keywords", help="新的 keywords")

    p_ingest = sub.add_parser("ingest", help="全流程写入记忆")
    p_ingest.add_argument("content", nargs="?", help="写入内容（支持管道输入）")
    p_ingest.add_argument("--keywords", help="搜索关键词")
    p_ingest.add_argument("--category", default="工具类", help="分类")
    p_ingest.add_argument("--sub-category", default="工作流程", help="子分类")
    p_ingest.add_argument("--importance", default="P1", choices=["P0", "P1", "P2"], help="重要性")
    p_ingest.add_argument("--label", default="rule", help="标签")
    p_ingest.add_argument("--entities", help="逗号分隔的实体名")
    p_ingest.add_argument("--tags", help="逗号分隔的标签")
    p_ingest.add_argument("--summary", help="摘要（一句话概括核心内容，不超过60字）")
    p_ingest.add_argument("--source", default="cli:mw_ingest", help="来源标记")
    p_ingest.add_argument("--workspace", default="default", help="工作空间ID（默认default）")
    p_ingest.add_argument("--memory-type", default="session", choices=["session", "project", "global", "cc"], help="记忆类型")
    p_ingest.add_argument("--scope", default="global", choices=["global", "project", "session"],
                          help="记忆范围（默认global）")
    p_ingest.add_argument("--confirm", action="store_true", help="确认写入（跳过预览）")
    p_ingest.add_argument("--agent", default=None, help="Agent ID（claude/mimo/codex）")
    p_ingest.add_argument("--crawl", action="store_true", help="写入后自动增量扫描图谱")

    # 图谱域
    p_crossref = sub.add_parser("cross-ref", help="存量记忆批量建双向关联")
    p_crossref.add_argument("-k", "--top-k", type=int, default=3, help="每条记忆关联前几条，默认 3")
    p_crossref.add_argument("-m", "--max-docs", type=int, default=0, help="处理上限，0=全部，默认 0")
    p_crossref.add_argument("--dry-run", action="store_true", help="只预览不写入")

    p_crawl = sub.add_parser("crawl", help="批量扫描未链接提及，建 cross_ref")
    p_crawl.add_argument("--full", action="store_true", help="全量扫描所有记忆（默认增量）")
    p_crawl.add_argument("-k", "--top-k", type=int, default=3, help="每条记忆关联前几条，默认 3")
    p_crawl.add_argument("--no-mentions", action="store_true", help="不启用 mention 扫描（只做 entity 共享）")

    p_rebuild_links = sub.add_parser("rebuild-links", help="重建知识图谱关联")
    p_rebuild_links.add_argument("--full", action="store_true", help="全量扫描所有记忆")
    p_rebuild_links.add_argument("--dry-run", action="store_true", help="预览模式")

    p_graph_stats = sub.add_parser("graph-stats", help="图谱健康度统计")
    p_graph_stats.add_argument("--format", choices=["json", "text"], default="text", help="输出格式")
    p_graph_stats.add_argument("-o", "--output", help="输出文件路径")

    p_dot_export = sub.add_parser("export-dot", help="导出图谱为 DOT 格式")
    p_dot_export.add_argument("-o", "--output", help="输出文件路径（默认输出到 stdout）")
    p_dot_export.add_argument("--max-nodes", type=int, default=100, help="最大节点数")

    p_graph_traverse = sub.add_parser("graph-traverse", help="BFS 图遍历")
    p_graph_traverse.add_argument("doc_id", type=int, help="起始节点 doc_id")
    p_graph_traverse.add_argument("--hops", type=int, default=3, help="最大跳数（默认3）")
    p_graph_traverse.add_argument("--relation", help="边类型过滤")
    p_graph_traverse.add_argument("--by-hop", action="store_true", help="按跳数分组显示")

    # 向量域
    p_vector_search = sub.add_parser("vector-search", help="向量搜索（需要 sentence-transformers）")
    p_vector_search.add_argument("query", help="搜索关键词")
    p_vector_search.add_argument("-n", "--top-k", type=int, default=5)

    p_vector_status = sub.add_parser("vector-status", help="查看向量搜索状态")

    p_vector_build = sub.add_parser("vector-build", help="构建向量索引")
    p_vector_build.add_argument("--stats", action="store_true", help="仅显示统计信息")

    p_vector_preload = sub.add_parser("vector-preload", help="预加载向量模型")

    # 进化域
    p_decay = sub.add_parser("decay", help="衰减长期未访问的记忆权重")
    p_decay.add_argument("-f", "--factor", type=float, default=0.8, help="衰减系数，默认 0.8")
    p_decay.add_argument("-m", "--min-weight", type=int, default=10, help="最低权重，默认 10")
    p_decay.add_argument("-d", "--decay-days", type=int, default=30, help="衰减周期（天），默认 30")

    p_evolve = sub.add_parser("evolve", help="进化：冷热候选 + 纠正检测")
    p_evolve.add_argument("--cold-days", type=int, default=30, help="冷候选天数阈值（默认30）")
    p_evolve.add_argument("--cold-max-weight", type=int, default=30, help="冷候选最大权重（默认30）")
    p_evolve.add_argument("--hot-min-weight", type=int, default=80, help="热候选最小权重（默认80）")
    p_evolve.add_argument("--apply", action="store_true", help="自动应用升降级和固化纠正")
    p_evolve.add_argument("--tier-only", action="store_true", help="只做层级变更，不检测纠正")
    p_evolve.add_argument("--pattern", help="只处理指定模式（正则）")

    p_reflect = sub.add_parser("reflect", help="记录纠正模式")
    p_reflect.add_argument("pattern", help="模式描述（如：'总是忘记加错误处理'）")
    p_reflect.add_argument("summary", help="纠正总结")
    p_reflect.add_argument("--context", default="", help="上下文")

    p_promote = sub.add_parser("promote", help="将符合条件的 project 记忆晋升为 global")
    p_promote.add_argument("--min-weight", type=int, default=100, help="最小权重（默认 100）")
    p_promote.add_argument("--min-access", type=int, default=10, help="最小访问次数（默认 10）")
    p_promote.add_argument("--dry-run", action="store_true", help="预览模式（不写入数据库）")

    p_reorganize = sub.add_parser("reorganize", help="整理旧记忆（Agent自己分类规划）")
    p_reorganize.add_argument("-n", "--limit", type=int, default=50, help="处理记忆数量（默认50）")
    p_reorganize.add_argument("--all", dest="reorganize_all", action="store_true", help="处理所有记忆")
    p_reorganize.add_argument("--dry-run", action="store_true", help="预览模式（不实际修改）")

    # 维护域
    p_reindex = sub.add_parser("reindex", help="重建 FTS5 索引")
    p_reindex.add_argument("--confirm", action="store_true", help="确认重建")

    p_rebuild_fts5 = sub.add_parser("rebuild-fts5", help="重建FTS5索引")

    p_cleanup = sub.add_parser("cleanup", help="清理测试数据")
    p_cleanup.add_argument("--test", action="store_true", help="清理测试数据")
    p_cleanup.add_argument("--stale", action="store_true", help="清理过期记忆")
    p_cleanup.add_argument("--all", dest="cleanup_all", action="store_true", help="清理所有")
    p_cleanup.add_argument("--hard", action="store_true", help="物理删除（默认软删除）")
    p_cleanup.add_argument("--dry-run", action="store_true", help="预览模式")

    p_lint = sub.add_parser("lint", help="健康度检查（已废弃，请用 health）")

    p_health = sub.add_parser("health", help="检查各组件健康状态")

    p_stats = sub.add_parser("stats", help="显示知识库进化统计")

    p_log = sub.add_parser("log", help="进化日志")
    p_log.add_argument("-t", "--type", choices=["correction", "evolution", "tier", "all"], default="all", help="日志类型")
    p_log.add_argument("-n", "--limit", type=int, default=20, help="显示条数")

    p_index = sub.add_parser("index", help="记忆路由表（分类统计）")
    p_index.add_argument("-c", "--category", help="按分类过滤")

    p_sync = sub.add_parser("sync", help="SQLite ↔ MD/JSON 双向同步")
    p_sync.add_argument("-d", "--direction", choices=["both", "sqlite_to_md", "md_to_sqlite"],
                        default="both", help="同步方向（默认 both）")

    # 记忆管理域
    # scene
    p_scene = sub.add_parser("scene", help="场景管理")
    scene_sub = p_scene.add_subparsers(dest="scene_action")
    p_scene_set = scene_sub.add_parser("set", help="设置场景")
    p_scene_set.add_argument("scene_id", help="场景 ID（如 code / deploy / debug）")
    p_scene_set.add_argument("name", help="场景名称")
    p_scene_set.add_argument("--parent", help="父场景 ID")
    p_scene_set.add_argument("--description", help="场景描述")
    p_scene_get = scene_sub.add_parser("get", help="查看场景")
    p_scene_get.add_argument("scene_id", help="场景 ID")
    scene_sub.add_parser("list", help="列出所有场景")

    # emotion
    p_emotion = sub.add_parser("emotion", help="情绪管理")
    emotion_sub = p_emotion.add_subparsers(dest="emotion_action")
    p_emo_set = emotion_sub.add_parser("set", help="设置情绪")
    p_emo_set.add_argument("doc_id", type=int, help="记忆 doc_id")
    p_emo_set.add_argument("emotion_type", help="情绪类型（positive/negative/neutral）")
    p_emo_set.add_argument("--detail", help="情绪细节")
    p_emo_set.add_argument("--intensity", type=float, default=0.5, help="情绪强度 0-1")
    p_emo_get = emotion_sub.add_parser("get", help="查看情绪")
    p_emo_get.add_argument("doc_id", type=int, help="记忆 doc_id")

    # tier
    p_tier = sub.add_parser("tier", help="层级管理")
    tier_sub = p_tier.add_subparsers(dest="tier_action")
    p_tier_set = tier_sub.add_parser("set", help="设置层级")
    p_tier_set.add_argument("doc_id", type=int, help="记忆 doc_id")
    p_tier_set.add_argument("tier", choices=["hot", "warm", "cold", "frozen"], help="层级")
    p_tier_set.add_argument("--reason", help="变更原因")
    p_tier_get = tier_sub.add_parser("get", help="查看层级")
    p_tier_get.add_argument("doc_id", type=int, help="记忆 doc_id")

    # archive
    p_archive = sub.add_parser("archive", help="归档记忆")
    p_archive.add_argument("doc_id", type=int, help="记忆 doc_id")
    p_archive.add_argument("--reason", help="归档原因")

    # forget
    p_forget = sub.add_parser("forget", help="删除记忆（软删除）")
    p_forget.add_argument("doc_id", type=int, help="记忆 doc_id")
    p_forget.add_argument("--confirm", action="store_true", help="确认删除")
    p_forget.add_argument("--reason", help="删除原因")

    # always-load
    p_always = sub.add_parser("always-load", help="始终加载管理")
    always_sub = p_always.add_subparsers(dest="always_action")
    p_always_set = always_sub.add_parser("set", help="设为始终加载")
    p_always_set.add_argument("doc_id", type=int, help="记忆 doc_id")
    p_always_unset = always_sub.add_parser("unset", help="取消始终加载")
    p_always_unset.add_argument("doc_id", type=int, help="记忆 doc_id")
    always_sub.add_parser("get", help="查看始终加载列表")

    # backup
    p_backup = sub.add_parser("backup", help="备份数据库")
    p_backup.add_argument("--dir", default="backups", help="备份目录（默认 backups）")

    # session
    p_session = sub.add_parser("session", help="会话状态管理")
    session_sub = p_session.add_subparsers(dest="session_action")
    p_sess_save = session_sub.add_parser("save", help="保存会话状态")
    p_sess_save.add_argument("agent_name", help="Agent 名称（claude/mimo/codex）")
    p_sess_save.add_argument("--session-id", help="会话 ID")
    p_sess_save.add_argument("--topic", help="最后话题")
    p_sess_save.add_argument("--tasks", help="未完成任务（JSON 数组）")
    p_sess_save.add_argument("--emotion", help="情绪状态")
    p_sess_get = session_sub.add_parser("get", help="查看会话状态")
    p_sess_get.add_argument("agent_name", help="Agent 名称")
    p_sess_get.add_argument("--session-id", help="会话 ID")

    # valid-time
    p_valid = sub.add_parser("valid-time", help="设置记忆有效期")
    p_valid.add_argument("doc_id", type=int, help="记忆 doc_id")
    p_valid.add_argument("--from-date", dest="from_date", help="生效日期（YYYY-MM-DD）")
    p_valid.add_argument("--until", help="失效日期（YYYY-MM-DD）")

    # link
    p_link = sub.add_parser("link", help="创建记忆关联")
    p_link.add_argument("source_id", type=int, help="源记忆 doc_id")
    p_link.add_argument("target_id", type=int, help="目标记忆 doc_id")
    p_link.add_argument("--weight", type=float, default=1.0, help="关联权重（默认 1.0，Agent 显式关联用 2.0）")
    p_link.add_argument("--note", help="关联说明")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        sys.exit(1)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    db_path = args.db or get_agent_db(args.agent)

    from .client import MemoryClient
    client = MemoryClient(db_path)

    try:
        cmd_func = COMMANDS.get(args.cmd)
        if cmd_func:
            if args.cmd == "ingest":
                cmd_func(client, args, db_path)
            else:
                cmd_func(client, args)
        else:
            print(f"未知命令: {args.cmd}")
            parser.print_help()
            sys.exit(1)
    finally:
        client.close()
