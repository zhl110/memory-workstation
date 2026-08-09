from __future__ import annotations

import json
import logging
import os
import re
import signal
import sys
import threading
import time
from pathlib import Path

from .core.config import Config, _MEMORY_HOME, load_config
from .core.process_guard import ProcessGuard
from .scanner.scanner import FileScanner
from .storage.manager import StorageManager
from .llm.manager import LLMManager
from .pipeline import ClassifyPipeline, ClassifyResult
from .scheduler.resource_scheduler import ResourceScheduler
from .gateway.mcp_server import MCPServer
from .gateway.http_server import create_app
from .tray.tray_app import TrayApp
from .utils.crash_dump import write_crash_dump
from .utils.frontmatter import build_frontmatter

logger = logging.getLogger(__name__)


class AppContext:
    def __init__(self):
        self.config = load_config()
        self.storage: StorageManager = None
        self.scanner: FileScanner = None
        self.llm: LLMManager = None
        self.scheduler: ResourceScheduler = None
        self.optimizer = None
        self.tray: TrayApp = None
        self.guard: ProcessGuard = None
        self._mcp_thread: threading.Thread = None
        self._http_thread: threading.Thread = None
        self._running = False
        self.domain_normalizer = None
        self.pipeline: ClassifyPipeline = None
        # 进度追踪
        self.scan_progress: dict = {"phase": "idle", "count": 0, "total": 0, "pending": 0}
        self.optimize_progress: dict = {"running": False, "phase": "", "detail": ""}

    def start(self):
        self._running = True
        self._setup_logging()
        self._migrate_old_data()

        logger.info("=== Memory Workstation starting ===")

        self.storage = StorageManager(
            db_path=self.config.storage.db_path,
            vector_path=self.config.storage.vector_path,
            snapshot_dir=self.config.storage.snapshot_dir,
            max_snapshots=self.config.storage.max_snapshot_count,
            backup_interval_h=self.config.storage.backup_interval_h,
            enable_wal=self.config.storage.enable_wal,
        )
        self.storage.init()
        self.storage._ctx = self

        self.llm = LLMManager(self.config)
        # V10：不再加载 classify 模型（exe 不依赖外部 LLM）
        # 只加载嵌入模型（本地GGUF，用于向量辅助：去重/搜索/归一化）
        # 失败不影响分类功能，仅禁用向量辅助
        self.llm.load_embed_model()

        from .core.domain_normalizer import DomainNormalizer
        self.domain_normalizer = DomainNormalizer(self.storage.sqlite, None)  # V10: llm=None（无 classify 后端）

        self.pipeline = ClassifyPipeline(
            llm_manager=self.llm,
            storage=self.storage,
            domain_normalizer=self.domain_normalizer,
            config=self.config,
        )

        self.scheduler = ResourceScheduler()
        self.scheduler.set_process_fn(self._classify_and_embed)
        self.scheduler.start()

        from .optimizer import MemoryOptimizer
        self.optimizer = MemoryOptimizer(self.storage, self.config, llm_manager=self.llm)
        self.optimizer.start(interval_h=24)

        self.scanner = FileScanner(self.config, self.storage)
        self.scanner.set_classify_callback(self._on_classify_needed)

        self.tray = TrayApp(self)
        self.tray.start()

        # pystray 后台线程可能随时关闭控制台句柄（竞态），用 SafeStream 包装 stdout/stderr
        self._protect_stdio()

        # ProcessGuard：监控进程级crash（信号/未捕获异常），通过signal_handler触发重启
        # 注意：不要用on_crash监控业务逻辑（如LLM状态），那是_start_sleep_monitor的职责
        self.guard = ProcessGuard(
            crash_limit=self.config.global_.reboot_crash_limit,
            cooldown_sec=60,
        )
        self.guard.start_watchdog()

        self._start_cleanup_timer()
        self._start_soft_delete_cleanup()
        self._start_resource_reclaimer()
        self._start_sleep_monitor()

        self._start_mcp()
        self._start_http()

        threading.Thread(target=self._run_two_phase_scan, daemon=True).start()

        self._replenish_unclassified()

        self._start_export_watcher()

        self._update_tray_status()
        self._check_version_update()
        self.tray.show_first_run_guide()
        logger.info("=== Memory Workstation running ===")

    def _check_version_update(self):
        """检测版本更新，首次运行或版本变化时提示"""
        exe_dir = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__))
        version_file = os.path.join(exe_dir, "version.json")
        version_marker = os.path.join(_MEMORY_HOME, ".last_version")

        if not os.path.exists(version_file):
            return

        try:
            with open(version_file) as f:
                data = json.load(f)
            current_ver = data.get("version", "0.0.0")
        except Exception:
            return

        prev_ver = "0.0.0"
        if os.path.exists(version_marker):
            try:
                with open(version_marker) as f:
                    prev_ver = f.read().strip()
            except Exception:
                pass

        if current_ver != prev_ver:
            # 首次运行的欢迎
            if prev_ver == "0.0.0":
                self.tray.show_toast("Memory Workstation", f"欢迎使用 v{current_ver}！数据已自动初始化。")
            else:
                self.tray.show_toast("Memory Workstation", f"已更新到 v{current_ver}，本地数据不受影响。")
            try:
                os.makedirs(os.path.dirname(version_marker), exist_ok=True)
                with open(version_marker, "w") as f:
                    f.write(current_ver)
            except Exception:
                pass

    def _protect_stdio(self):
        """用 SafeStream 包装 sys.stdout/stderr，pystray 关闭控制台后不崩溃"""
        import io

        class _SafeStream(io.TextIOBase):
            def __init__(self, orig):
                self._orig = orig
                self._errors = 'replace'

            def reconfigure(self, **kwargs):
                if self._is_alive():
                    try:
                        self._orig.reconfigure(**kwargs)
                    except Exception:
                        pass

            def _is_alive(self):
                try:
                    return self._orig is not None and not self._orig.closed and self._orig.fileno() >= 0
                except (ValueError, OSError, AttributeError):
                    return False

            def write(self, s):
                if self._is_alive():
                    try:
                        return self._orig.write(s)
                    except (ValueError, OSError):
                        pass
                return len(s or '')

            def flush(self):
                if self._is_alive():
                    try:
                        self._orig.flush()
                    except (ValueError, OSError):
                        pass

            def fileno(self):
                return -1  # 标记为不可 fileno，避免外部检测到 closed

            @property
            def closed(self):
                return False

            def close(self):
                pass

        sys.stdout = _SafeStream(sys.stdout)
        sys.stderr = _SafeStream(sys.stderr)

    def _migrate_old_data(self):
        """将旧路径数据迁移到 ~/.memory-workstation/"""
        import shutil
        old_db = os.path.abspath("./memory_storage/meta.sqlite")
        old_vec = os.path.abspath("./memory_storage/vector.lance")
        new_home = os.path.join(os.path.expanduser("~"), ".memory-workstation")

        if os.path.exists(os.path.join(new_home, "meta.sqlite")):
            self._cleanup_dirty_data(new_home)
            return

        if os.path.exists(old_db):
            logger.info("Migrating old data from %s to %s", os.path.dirname(old_db), new_home)
            os.makedirs(new_home, exist_ok=True)
            try:
                shutil.copytree(os.path.dirname(old_db), new_home, dirs_exist_ok=True)
                logger.info("Data migration complete: %d files", len(os.listdir(new_home)))
            except Exception as e:
                logger.error("Data migration failed: %s", e)

    def _cleanup_dirty_data(self, db_home: str):
        """启动时清理脏数据：重复 hash + 空分类记录（让下轮 LLM 重新分类）"""
        import sqlite3
        db_path = os.path.join(db_home, "meta.sqlite")
        if not os.path.exists(db_path):
            return
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("PRAGMA journal_mode=WAL")

            # 1. 删除重复 hash：每个 hash 只保留 id 最小的那条
            conn.execute("""
                DELETE FROM document_files WHERE id NOT IN (
                    SELECT MIN(id) FROM document_files GROUP BY file_hash
                )
            """)
            deleted_docs = conn.total_changes

            # 2. 清除 memory_classify 中指向已删 doc 的记录
            conn.execute("""
                DELETE FROM memory_classify WHERE doc_id NOT IN (
                    SELECT id FROM document_files
                )
            """)

            # 3. 清除 processing_log 中指向已删 doc 的记录
            conn.execute("""
                DELETE FROM processing_log WHERE doc_id IS NOT NULL AND doc_id NOT IN (
                    SELECT id FROM document_files
                )
            """)

            # 4. 清除 memory_access_record 中指向已删 doc 的记录
            conn.execute("""
                DELETE FROM memory_access_record WHERE doc_id NOT IN (
                    SELECT id FROM document_files
                )
            """)

            # 5. 清理由路径规则入库、但 LLM 未产生分类字段的脏记录
            #    这些 doc 有 memory_classify 行（路径规则写入的），但 content_category/sub_category 为空，
            #    需要清掉让 _replenish_unclassified 重新走批量 LLM 分类
            conn.execute("""
                DELETE FROM memory_classify
                WHERE (content_category IS NULL OR content_category = '')
                  AND (sub_category IS NULL OR sub_category = '')
            """)
            empty_classify = conn.total_changes - (0 if not deleted_docs else deleted_docs)
            tracked = conn.total_changes

            conn.commit()
            conn.close()
            if deleted_docs:
                logger.info("Dirty data cleanup: removed %d duplicate documents", deleted_docs)
            if empty_classify:
                logger.info("Dirty data cleanup: removed %d records with empty category fields (will re-classify on next scan)", empty_classify)
        except Exception as e:
            logger.error("Dirty data cleanup failed: %s", e)

    def _setup_logging(self):
        from logging.handlers import RotatingFileHandler
        from .core.config import _MEMORY_HOME
        log_dir = Path(_MEMORY_HOME) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        root_logger = logging.getLogger()
        root_logger.setLevel(getattr(logging, self.config.log.log_level, logging.INFO))

        fmt = logging.Formatter("%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s: %(message)s",
                                datefmt="%Y-%m-%d %H:%M:%S")

        fh = RotatingFileHandler(
            log_dir / "workstation.log",
            maxBytes=self.config.log.single_file_max_mb * 1024 * 1024,
            backupCount=self.config.log.max_log_file_count,
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        root_logger.addHandler(fh)

        if self.config.log.separate_error_log:
            eh = RotatingFileHandler(
                log_dir / "error.log",
                maxBytes=self.config.log.single_file_max_mb * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            eh.setLevel(logging.ERROR)
            eh.setFormatter(fmt)
            root_logger.addHandler(eh)

    def _on_classify_needed(self, doc_id: int, content: str, filepath: str = ""):
        self.scheduler.submit_classify(doc_id, content, filepath)

    def _classify_and_embed(self, doc_id: int, content: str, filepath: str = ""):
        self.pipeline.process_one(doc_id, content, filepath)


    def _start_cleanup_timer(self):
        def _cleanup_loop():
            while self._running:
                time.sleep(3600)
                try:
                    self.storage.sqlite.cleanup_expired(
                        chat_log_days=self.config.memory_life.chat_log_expire_day,
                        short_days=self.config.memory_life.short_memory_expire_day,
                    )
                except Exception as e:
                    logger.error("Expiry cleanup error: %s", e)
        threading.Thread(target=_cleanup_loop, daemon=True).start()

    def _start_soft_delete_cleanup(self):
        def _soft_delete_loop():
            while self._running:
                time.sleep(86400)
                try:
                    deleted = self.storage.sqlite.cleanup_soft_deleted(days=30)
                    if deleted:
                        logger.info("Soft delete cleanup: %d documents removed", deleted)
                except Exception as e:
                    logger.error("Soft delete cleanup error: %s", e)
        threading.Thread(target=_soft_delete_loop, daemon=True).start()

    def _run_two_phase_scan(self):
        """Phase 1: Gate 快速筛选 → Phase 2: 批量 LLM 精选分类"""
        try:
            self.scan_progress["phase"] = "scanning"
            count, pending = self.scanner.full_scan()
            self.scan_progress.update({"count": count, "pending": len(pending), "total": len(pending)})
            if pending:
                self.scan_progress["phase"] = "classifying"
                self._process_pending_llm(pending)
                self.scan_progress["phase"] = "exporting"

            self._export_memories()
            self.scan_progress["phase"] = "idle"

            if not pending:
                logger.info("Phases 1 complete: %d files, no docs need LLM classify", count)
                return

        except Exception as e:
            self.scan_progress["phase"] = "error"
            logger.error("Two-phase scan failed: %s", e)

    def _process_pending_llm(self, pending: list):
        """Phase 2: 批量 LLM 精选分类（委托 pipeline）"""
        self.pipeline.process_batch(pending)

    def _start_export_watcher(self):
        pass

    def _replenish_unclassified(self):
        """启动时检查 DB 有无已入库但无分类（memory_classify 无对应行）的记录，补跑 LLM"""
        try:
            rows = self.storage.sqlite._conn.execute(
                """SELECT d.id, d.file_path
                   FROM document_files d
                   LEFT JOIN memory_classify c ON d.id = c.doc_id
                   WHERE c.doc_id IS NULL AND d.is_deleted = 0"""
            ).fetchall()
            if not rows:
                return
            logger.info("Found %d unclassified docs, replenishing...", len(rows))
            pending = []
            for r in rows:
                fp = r["file_path"]
                content = self._read_file_content(fp)
                if content:
                    pending.append((r["id"], content, fp))
            if pending:
                self._process_pending_llm(pending)
                self._export_memories()
                logger.info("Replenish done: %d/%d classified", len(pending), len(rows))
        except Exception as e:
            logger.error("Replenish unclassified failed: %s", e)

    @staticmethod
    def _read_file_content(filepath: str) -> str:
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception:
            return ""

    def _export_memories(self):
        try:
            import re
            import json as _json
            from datetime import datetime

            output_dir = Path(_MEMORY_HOME) / "memory_export"
            if output_dir.exists():
                import shutil
                shutil.rmtree(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)

            conn = self.storage.sqlite._conn

            def read_original(file_path: str, max_chars: int = 500) -> str:
                if not file_path or not os.path.exists(file_path):
                    return ''
                try:
                    raw = open(file_path, encoding='utf-8').read().strip()
                except Exception:
                    try:
                        raw = open(file_path, encoding='gbk').read().strip()
                    except Exception:
                        return ''
                if raw.startswith('---'):
                    end = raw.find('---', 3)
                    if end != -1:
                        raw = raw[end + 3:].strip()
                return raw[:max_chars]

            # ─── 查询 + 构造 ClassifyResult → exportable 过滤 ───
            rows = conn.execute('''
                SELECT d.id, d.file_path,
                       c.label, c.importance, c.weight, c.namespace, c.compact_content,
                       c.content_category, c.sub_category, c.tags, c.memory_tier,
                       d.create_time
                FROM document_files d
                JOIN memory_classify c ON d.id = c.doc_id
                WHERE d.is_deleted = 0
                ORDER BY c.content_category, c.sub_category, c.weight DESC
            ''').fetchall()

            results = []
            for r in rows:
                cr = ClassifyResult(
                    doc_id=r['id'],
                    file_path=r['file_path'] or '',
                    label=r['label'] or '',
                    importance=r['importance'] or 'P2',
                    weight=r['weight'] or 0,
                    summary=r['compact_content'] or '',
                    category=r['content_category'] or '',
                    sub_category=r['sub_category'] or '',
                    memory_tier=r['memory_tier'] or '',
                    tags=r['tags'] or '',
                    create_time=r['create_time'] or '',
                    namespace=r['namespace'] or 'default',
                )
                # 规则/计划类没摘要时读原文件
                eff_summary = cr.effective_summary(read_original_fn=read_original)
                if eff_summary and eff_summary != cr.summary:
                    cr = ClassifyResult(
                        doc_id=cr.doc_id, file_path=cr.file_path,
                        label=cr.label, importance=cr.importance,
                        weight=cr.weight, summary=eff_summary,
                        category=cr.category, sub_category=cr.sub_category,
                        memory_tier=cr.memory_tier, tags=cr.tags,
                        create_time=cr.create_time, namespace=cr.namespace,
                    )
                if cr.exportable:
                    results.append(cr)

            if not results:
                logger.info("Export: no exportable documents")
                return

            def safe_filename(name):
                return re.sub(r'[\\/:*?"<>|]', '_', name).strip('_') or '未命名'

            def extract_keywords(text: str, max_len: int = 30) -> str:
                if not text:
                    return "未命名"
                clean = re.sub(r'---.*?---', '', text, flags=re.DOTALL)
                clean = re.sub(r'[*#_\[\]]', '', clean)
                clean = re.sub(r'\s+', ' ', clean).strip()
                if not clean:
                    return "未命名"
                first = clean.split('。')[0] if '。' in clean else clean.split('.')[0]
                first = first.strip()
                if len(first) > max_len:
                    first = first[:max_len]
                return safe_filename(first)

            def parse_tags(tags_json: str) -> tuple:
                if not tags_json:
                    return "", ""
                try:
                    arr = _json.loads(tags_json)
                    if isinstance(arr, list) and len(arr) >= 2:
                        return arr[0] or "", arr[1] or ""
                except (_json.JSONDecodeError, TypeError):
                    pass
                return "", ""

            cat_groups = {}
            for cr in results:
                cat = cr.category or cr.label or '未分类'
                cat_groups.setdefault(cat, []).append(cr)

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            root_index_lines = [
                f'# Memory Workstation 记忆导出\n',
                f'> 导出时间: {now_str} | 总文档: {len(results)}\n\n',
                '## 目录结构\n\n',
            ]

            for category in sorted(cat_groups.keys()):
                items = cat_groups[category]
                cat_dir = output_dir / safe_filename(category)
                cat_dir.mkdir(exist_ok=True)

                sub_groups = {}
                for cr in items:
                    sub = cr.sub_category or '通用'
                    sub_groups.setdefault(sub, []).append(cr)

                sub_index_lines = [
                    f'# {category}\n',
                    f'> 共 {len(items)} 条 | 导出: {now_str}\n\n',
                    '## 子分类\n\n',
                    '| 子分类 | 文件 | 数量 |\n',
                    '|--------|------|------|\n',
                ]

                for sub in sorted(sub_groups.keys()):
                    sub_items = sub_groups[sub]
                    sub_dir = cat_dir / safe_filename(sub)
                    sub_dir.mkdir(exist_ok=True)

                    sub_index_lines.append(f'| {sub} | — | {len(sub_items)} |\n')

                    for i, cr in enumerate(sub_items, 1):
                        label = cr.label
                        imp = cr.importance
                        weight = cr.weight
                        summary = cr.summary or '(无摘要)'
                        ns = cr.namespace
                        ct, kt = parse_tags(cr.tags)

                        # 查交叉引用生成 [[双链]]
                        try:
                            ref_rows = conn.execute(
                                "SELECT related_doc_id, relation_type, note FROM memory_cross_ref WHERE doc_id=?",
                                (cr.doc_id,),
                            ).fetchall()
                            wiki_links = [f"[[{r['related_doc_id']}]]" for r in ref_rows]
                        except Exception:
                            wiki_links = []

                        kw = extract_keywords(summary)
                        filename = f"{i:03d}_{kw}.md"
                        filepath = sub_dir / filename

                        with open(filepath, 'w', encoding='utf-8') as f:
                            fm = build_frontmatter(
                                doc_id=str(cr.doc_id),
                                source='memory_export',
                                create_utc=cr.create_time,
                                auto_label=label,
                                memory_tier=cr.memory_tier,
                                weight=weight,
                            )
                            f.write(fm)
                            f.write(f'# [{imp}] {label}\n\n')
                            f.write(f'{summary}\n\n')
                            meta_parts = []
                            if ct:
                                meta_parts.append(f'内容类型: {ct}')
                            if kt:
                                meta_parts.append(f'知识类型: {kt}')
                            meta_parts.append(f'权重: {weight}')
                            meta_parts.append(f'命名空间: {ns}')
                            f.write(f'{" | ".join(meta_parts)}\n')
                            if wiki_links:
                                f.write(f'\n关联：{", ".join(wiki_links)}\n')

                    with open(sub_dir / '_index.md', 'w', encoding='utf-8') as f:
                        f.write(f'# {category} — {sub}\n\n> 共 {len(sub_items)} 条\n\n')
                        f.write('| # | 文件 | 重要性 | 内容摘要 |\n')
                        f.write('|---|------|--------|--------|\n')
                        for i, cr in enumerate(sub_items[:20], 1):
                            kw = extract_keywords(cr.summary or '')
                            filename = f"{i:03d}_{kw}.md"
                            imp = cr.importance
                            preview = (cr.summary or '')[:50] + '...'
                            f.write(f'| {i} | [{filename}]({filename}) | {imp} | {preview} |\n')

                root_index_lines.append(f'- [{category}]({safe_filename(category)}/{", ".join(sorted(sub_groups.keys()))}) — {len(items)}条\n')

            with open(output_dir / 'INDEX.md', 'w', encoding='utf-8') as f:
                f.writelines(root_index_lines)

            category_stats = {}
            for cat, items in cat_groups.items():
                sub_stats = {}
                for cr in items:
                    sub = cr.sub_category or '通用'
                    sub_stats[sub] = sub_stats.get(sub, 0) + 1
                category_stats[cat] = {
                    "total": len(items),
                    "sub_categories": sub_stats,
                }

            meta = {
                "version": 2,
                "exported_at": datetime.now().isoformat(),
                "total_documents": len(results),
                "categories": sorted(cat_groups.keys()),
                "category_stats": category_stats,
            }
            with open(output_dir / '.export_info.json', 'w', encoding='utf-8') as f:
                _json.dump(meta, f, ensure_ascii=False, indent=2)

            sql_path = output_dir / 'memory_export.sql'
            with open(sql_path, 'w', encoding='utf-8') as f:
                f.write('-- Memory Workstation 导出\n')
                f.write(f'-- 导出时间: {datetime.now().isoformat()}\n')
                f.write(f'-- 总文档: {len(results)}\n\n')
                f.write('CREATE TABLE IF NOT EXISTS memory_export (\n')
                f.write('    id INTEGER,\n')
                f.write('    file_path TEXT,\n')
                f.write('    label TEXT,\n')
                f.write('    importance TEXT,\n')
                f.write('    weight INTEGER,\n')
                f.write('    namespace TEXT,\n')
                f.write('    summary TEXT,\n')
                f.write('    content_category TEXT,\n')
                f.write('    sub_category TEXT,\n')
                f.write('    tags TEXT,\n')
                f.write('    memory_tier TEXT,\n')
                f.write('    create_time TEXT\n')
                f.write(');\n\n')
                for cr in results:
                    def _sql_val(v):
                        return str(v).replace("'", "''").replace('\n', '\\n').replace('\r', '')
                    vals = (
                        cr.doc_id,
                        _sql_val(cr.file_path),
                        _sql_val(cr.label),
                        cr.importance,
                        cr.weight,
                        _sql_val(cr.namespace),
                        _sql_val(cr.summary),
                        _sql_val(cr.category),
                        _sql_val(cr.sub_category),
                        _sql_val(cr.tags),
                        _sql_val(cr.memory_tier),
                        str(cr.create_time) if cr.create_time else '',
                    )
                    f.write(f"INSERT INTO memory_export VALUES ({vals[0]}, '{vals[1]}', '{vals[2]}', "
                            f"'{vals[3]}', {vals[4]}, '{vals[5]}', '{vals[6]}', "
                            f"'{vals[7]}', '{vals[8]}', '{vals[9]}', '{vals[10]}', '{vals[11]}');\n")
            logger.info("SQL export written: %s", sql_path)

            logger.info("Memories exported: %d docs to memory_export/ (%d categories)",
                        len(results), len(cat_groups))

            # ── 同步导出 Obsidian Vault（调 SDK export_md） ──
            try:
                from mw_sdk import MemoryClient
                obsidian_dir = Path(_MEMORY_HOME) / "memory_export_obsidian"
                if obsidian_dir.exists():
                    import shutil
                    shutil.rmtree(obsidian_dir)
                mc = MemoryClient(str(self.config.storage.db_path))
                n = mc.export_md(str(obsidian_dir))
                logger.info("Obsidian vault exported: %d docs to %s", n, obsidian_dir)
            except Exception as e:
                logger.warning("Obsidian vault export skipped: %s", e)
        except Exception as e:
            logger.error("Export failed: %s", e)

    def _start_resource_reclaimer(self):
        def _reclaim_loop():
            while self._running:
                time.sleep(86400)
                try:
                    import gc
                    gc.collect()
                    if self.storage:
                        self.storage.vector.compact()
                    logger.info("24h resource reclamation completed")
                except Exception as e:
                    logger.error("Resource reclamation error: %s", e)
        threading.Thread(target=_reclaim_loop, daemon=True).start()

    def _start_sleep_monitor(self):
        self._last_activity = time.time()
        def _monitor_loop():
            while self._running:
                time.sleep(30)
                elapsed = time.time() - self._last_activity
                if elapsed > 300:
                    self._last_activity = time.time()
                    self._recover_after_sleep()
        threading.Thread(target=_monitor_loop, daemon=True).start()

    def _recover_after_sleep(self):
        try:
            if self.scanner and self.scanner._observer:
                self.scanner.stop()
                self.scanner.start()
            # V10：不再加载 classify 模型
            logger.info("Sleep recovery completed")
        except Exception as e:
            logger.error("Sleep recovery error: %s", e)

    def _start_mcp(self):
        if not self.config.mcp.enable:
            return
        import sys
        if not sys.stdin or sys.stdin.closed:
            logger.warning("MCP server skipped: stdin not available (background thread)")
            return
        try:
            from .gateway.mcp_server import MCPServer
            mcp_server = MCPServer(self.storage)

            async def _run_mcp():
                try:
                    await mcp_server.run_stdio()
                except Exception as e:
                    logger.warning("MCP server stopped: %s", e)

            def _mcp_thread():
                import asyncio
                loop = asyncio.new_event_loop()
                loop.run_until_complete(_run_mcp())

            self._mcp_thread = threading.Thread(target=_mcp_thread, daemon=True)
            self._mcp_thread.start()
            logger.info("MCP server started")
        except Exception as e:
            logger.error("MCP server init failed: %s", e)

    def _start_http(self):
        import uvicorn
        import logging as _logging
        app = create_app(self.config, self.storage, self.llm, tray=self.tray)

        def _run_http():
            try:
                config = uvicorn.Config(
                    app,
                    host=self.config.api.host,
                    port=self.config.api.port,
                    log_config=None,
                )
                server = uvicorn.Server(config)
                server.run()
            except Exception as e:
                logger.error("HTTP server error: %s", e)

        self._http_thread = threading.Thread(target=_run_http, daemon=True)
        self._http_thread.start()
        logger.info("HTTP API started on %s:%d", self.config.api.host, self.config.api.port)

    def _update_tray_status(self):
        if self.tray:
            # V10：关键词分类模式，固定蓝色
            self.tray.set_status("blue")

    def get_status_snapshot(self) -> dict:
        """返回当前系统状态快照，供控制面板展示"""
        classify_progress = self.scheduler.get_progress() if self.scheduler else {}
        doc_count = self.storage.sqlite.total_documents() if self.storage else 0
        queue_size = self.scheduler.classify_queue_size if self.scheduler else 0
        return {
            "version": getattr(self.config, '_version', '0.0.0'),
            "llm_status": "keyword_only",  # V10: 关键词分类模式
            "provider": "none",
            "model": "",
            "api_base_url": "",
            "scan_paths": list(self.config.scan.custom_white_path),
            "agent_paths": list(getattr(self.config.scan, 'agent_paths', [])),
            "blacklist": list(self.config.scan.disk_black_list),
            "doc_count": doc_count,
            "classify_progress": classify_progress,
            "classify_queue": queue_size,
            "scan_progress": self.scan_progress,
            "optimize_progress": self.optimize_progress,
            "lock_model": self.config.global_.lock_model_forever,
            "autostart": self.tray._check_autostart() if self.tray else False,
        }

    def safe_full_scan(self):
        """安全全盘扫描：先打快照，备份 export，再重建"""
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        export_dir = Path(_MEMORY_HOME) / "memory_export"
        # 1. 备份当前 export
        if export_dir.exists():
            bak_name = f"memory_export_bak_{ts}"
            import shutil
            shutil.copytree(str(export_dir), bak_name)
            logger.info("Export backed up to %s", bak_name)
        # 2. 打快照（带标记）
        self.storage.create_snapshot()
        # 给最新快照改名，加 full_scan_before 标记
        from pathlib import Path as P
        snaps = sorted(P(self.config.storage.snapshot_dir).glob("snapshot_*.zip"))
        if snaps:
            latest = snaps[-1]
            tagged = latest.with_name(f"full_scan_before_{ts}.zip")
            latest.rename(tagged)
            logger.info("Tagged snapshot: %s", tagged.name)
        # 3. 清除当前 export
        if export_dir.exists():
            import shutil
            shutil.rmtree(str(export_dir))
        # 4. 执行全盘扫描
        self.scan_progress["phase"] = "scanning"
        count, pending = self.scanner.full_scan()
        self.scan_progress.update({"count": count, "pending": len(pending)})
        if pending:
            self.scan_progress["phase"] = "classifying"
            self.scheduler.reset_progress()
            self._process_pending_llm(pending)
        self.scan_progress["phase"] = "exporting"
        self._export_memories()
        self.scan_progress["phase"] = "idle"
        logger.info("Safe full scan complete: %d files, %d classified", count, len(pending))

    def restore_from_snapshot(self, snap_name: str) -> bool:
        """从指定快照恢复数据"""
        import shutil, zipfile
        snap_path = Path(self.config.storage.snapshot_dir) / snap_name
        if not snap_path.exists():
            return False
        try:
            # 停扫描、卸载模型
            self.scanner.stop()
            self.llm.unload()
            # 解压快照到临时目录
            tmp_dir = Path(self.config.storage.snapshot_dir) / "_restore_tmp"
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir)
            tmp_dir.mkdir(parents=True)
            with zipfile.ZipFile(snap_path, 'r') as zf:
                zf.extractall(tmp_dir)
            # 替换 DB
            db_path = Path(self.config.storage.db_path)
            restored_db = tmp_dir / "meta.sqlite"
            if restored_db.exists():
                self.storage.sqlite.close()
                shutil.copy2(restored_db, db_path)
                self.storage.sqlite.connect()
            # 替换向量库
            vec_path = Path(self.config.storage.vector_path)
            restored_vec = tmp_dir / "vector.lance"
            if restored_vec.exists() and vec_path.exists():
                shutil.rmtree(vec_path)
                shutil.copytree(restored_vec, vec_path)
                self.storage.vector.connect()
            # 清理
            shutil.rmtree(tmp_dir)
            # 恢复扫描（V10：不再加载 classify 模型）
            self.scanner.start()
            self._export_memories()
            logger.info("Restored from snapshot: %s", snap_name)
            return True
        except Exception as e:
            logger.error("Restore from snapshot failed: %s", e)
            return False

    def restart(self):
        self.shutdown()
        time.sleep(1)
        self.start()

    def shutdown(self):
        self._running = False
        logger.info("Shutting down...")
        if self.guard:
            self.guard.stop()
        if self.optimizer:
            self.optimizer.stop()
        if self.scanner:
            self.scanner.stop()
        if self.llm:
            self.llm.unload()
        if self.storage:
            try:
                self.storage.create_snapshot()
            except Exception:
                pass
            self.storage.close()
        if self.tray:
            self.tray.stop()
        logger.info("Shutdown complete")


def main():
    import atexit
    ctx = AppContext()

    def _on_exit():
        try:
            if ctx.storage:
                ctx.storage.create_snapshot()
        except Exception:
            pass

    atexit.register(_on_exit)

    def signal_handler(sig, frame):
        write_crash_dump(Exception(f"Signal {sig} received"))
        ctx.guard.check_and_restart()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    ctx.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        ctx.shutdown()


if __name__ == "__main__":
    main()
