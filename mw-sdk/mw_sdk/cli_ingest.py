"""MW CLI ingest 子命令 — 从 mw_ingest_full.py 搬入

提供 mw ingest 命令，统一入口。
"""

import re
import sys
import sqlite3
import datetime
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def ingest_full(
    content: str,
    classification: dict,
    keywords: str | None = None,
    source: str = "cli:mw_ingest_full",
    db_path: str | None = None,
    silent: bool = False,
) -> int:
    """MW 写入流程（纯存储，无判断逻辑）

    Args:
        content: 要写入的内容
        classification: 分类结果（必须由 Agent 提供）
        keywords: 搜索关键词。None=从分类字段提取
        source: 来源标记
        db_path: 数据库路径。None=使用 get_agent_db()
        silent: 静默模式（不 print 步骤日志）

    Returns:
        doc_id
    """
    from .utils import get_agent_db
    from .client import MemoryClient

    if db_path is None:
        db_path = get_agent_db()

    with MemoryClient(db_path) as m:
        m.init_schema()

        if not silent:
            print(f"  [2] 分类: {classification.get('category')} / {classification.get('sub_category')}")

        # [3] 搜索交叉引用候选
        if keywords is None:
            keywords = " ".join(filter(None, [
                classification.get("category", ""),
                classification.get("sub_category", ""),
                classification.get("label", ""),
            ]))
        sr = m.search(keywords, top_k=10) if keywords else []
        if not silent:
            print(f"  [3] search('{keywords}'): {len(sr)} 条结果")

        # [4] 写入（纯插入，无合并）
        doc_id = m._insert_classified(content, classification, source=source,
                                       auto_refs=True, ref_candidates=sr, ref_top_k=10)
        refs = len(m.get_linked(doc_id)) if doc_id and doc_id > 0 else 0
        if not silent:
            print(f"  [4] _insert_classified → #{doc_id}")

        # [4.5] 存储关键词（C++ batch_ingest 已写入，以下仅作兜底校验）
        conn = m._conn
        if doc_id and doc_id > 0 and keywords:
            existing = conn.execute(
                "SELECT keywords FROM memory_classify WHERE doc_id = ?",
                (doc_id,)
            ).fetchone()
            if not existing or not existing[0]:
                conn.execute(
                    "UPDATE memory_classify SET keywords = ? WHERE doc_id = ?",
                    (keywords, doc_id),
                )
                try:
                    conn.execute(
                        "UPDATE memory_fts SET keywords = ? WHERE doc_id = ?",
                        (keywords, doc_id),
                    )
                except sqlite3.OperationalError:
                    pass
                conn.commit()

        if not silent:
            print(f"  [5] auto_cross_ref: {refs} 条双向边")

        # [6] export
        export_dir = Path(db_path).parent / "memory_export_all"
        try:
            count = m.export_md(str(export_dir))
            if not silent:
                print(f"  [6] index.md 已更新 ({count} 文件)")
        except OSError as e:
            if not silent:
                print(f"  [6] export_md 警告: {e}")

        # [6.5] sync — SQLite ↔ MD/JSON 双向同步
        try:
            from .sync import MemorySync
            sync = MemorySync(db_path, str(export_dir), conn=m._conn)
            results = sync.sync_all(direction='sqlite_to_md')
            if not silent:
                print(f"  [6.5] sync 完成: MD={results['sqlite_to_md']}条")
        except Exception as e:
            if not silent:
                print(f"  [6.5] sync 警告: {e}")

        # [7] log
        log_path = Path(db_path).parent / "log_agents.md"
        log_entry = f"\n## {_now()} — mw ingest\n**操作：** search({len(sr)}条) → insert(#{doc_id}) → cross_ref({refs}条)\n**内容：** {classification.get('summary', '')[:200]}\n**来源：** {source}\n"
        try:
            with open(str(log_path), "a", encoding="utf-8") as f:
                f.write(log_entry)
        except Exception:
            pass
        if not silent:
            print(f"  [7] log 已追加")
            print(f"\n[OK] 写入完成: #{doc_id}")

    return doc_id


def ingest_simple(
    content: str,
    classification: dict,
    source: str = "sdk:simple",
    db_path: str | None = None,
) -> int:
    """纯插入 — 无判断、无合并、无分类，Agent 直接调用

    Args:
        content: 要写入的内容
        classification: 分类结果（Agent 已完成分类）
        source: 来源标记
        db_path: 数据库路径。None=使用 get_agent_db()

    Returns:
        新插入的 doc_id

    用法：
        doc_id = ingest_simple("禁止硬编码密钥", {
            "label": "规则", "importance": "P0", "category": "安全类",
            "sub_category": "密钥管理", "summary": "禁止硬编码密钥",
            "scope": "global", "applicability": "通用规则",
        })
    """
    from .utils import get_agent_db
    from .client import MemoryClient

    if db_path is None:
        db_path = get_agent_db()

    with MemoryClient(db_path) as m:
        m.init_schema()
        doc_id = m._insert_classified(content, classification, source=source,
                                       auto_refs=True, ref_top_k=3)
    return doc_id


def run_ingest(args: object) -> None:
    """CLI ingest 子命令入口"""
    # 处理管道输入
    if not args.content:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        args.content = sys.stdin.read().strip()
    if not args.content:
        print("请提供内容（参数或管道输入）")
        sys.exit(1)

    # 清除可能残留的 UTF-16 代理字符
    args.content = re.sub(r'[\ud800-\udfff]', '', args.content)

    from .utils import safe_truncate
    summary = args.summary or safe_truncate(args.content.strip(), 60).replace("\n", " ")
    entities = [{"name": n.strip(), "type": "concept"} for n in (args.entities or "").split(",") if n.strip()]
    tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]

    classification = {
        "label": args.label,
        "importance": args.importance,
        "category": args.category,
        "sub_category": args.sub_category,
        "summary": summary,
        "depth": "概述",
        "applicability": "通用规则",
        "entities": entities,
        "tags": tags,
        "scope": getattr(args, 'scope', 'global'),
    }

    db_path = args.db if hasattr(args, 'db') and args.db else None
    doc_id = ingest_full(args.content, classification=classification, keywords=args.keywords,
                         source=args.source, db_path=db_path)

    if getattr(args, 'crawl', False) and doc_id and doc_id > 0:
        from .client import MemoryClient
        with MemoryClient(db_path) as m:
            result = m.crawl_cross_ref(top_k=3, incremental=True, scan_mentions=True)
        print(f"  [8] crawl: {result['new_edges']} 条新边 (总计 {result['total_edges']})")
