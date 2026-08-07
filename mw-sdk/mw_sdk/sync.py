"""
MW SDK 同步模块 — SQLite ↔ MD 双向同步

同步范围：memory_export_all/ 目录
同步策略：SQLite 为主源，MD 为可编辑导出
冲突解决：以 SQLite 为准（updated_at 较新者优先）
"""

import hashlib
import os
import re
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Tuple

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    _HAS_WATCHDOG = True
except ImportError:
    _HAS_WATCHDOG = False


class MemorySync:
    """SQLite ↔ MD 双向同步器"""
    
    def __init__(self, db_path: str, export_dir: str, conn: sqlite3.Connection | None = None):
        """
        初始化同步器
        
        Args:
            db_path: SQLite 数据库路径
            export_dir: memory_export_all 目录路径
            conn: 可选的现有数据库连接（复用 MemoryClient 连接避免 WAL 冲突）
        """
        self.db_path = db_path
        self.export_dir = Path(export_dir)
        self.export_dir.mkdir(parents=True, exist_ok=True)
        self._conn = conn
        
    def _get_db_connection(self) -> sqlite3.Connection:
        """获取数据库连接（优先使用传入连接，否则新建）"""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            # 容错：损坏的UTF-8用替换字符代替，与 MemoryClient 一致
            self._conn.text_factory = lambda x: x.decode('utf-8', 'replace')
        return self._conn
    
    def _compute_hash(self, content: str) -> str:
        """计算内容哈希"""
        return hashlib.md5(content.encode('utf-8')).hexdigest()
    
    def _parse_frontmatter(self, md_content: str) -> Tuple[Dict, str]:
        """解析 MD 文件的 frontmatter"""
        if md_content.startswith('---'):
            parts = md_content.split('---', 2)
            if len(parts) >= 3:
                frontmatter_str = parts[1].strip()
                body = parts[2].strip()
                
                # 解析 frontmatter
                frontmatter = {}
                for line in frontmatter_str.split('\n'):
                    if ':' in line:
                        key, value = line.split(':', 1)
                        frontmatter[key.strip()] = value.strip()
                
                return frontmatter, body
        
        return {}, md_content
    
    def _build_frontmatter(self, memory: Dict) -> str:
        """构建 frontmatter"""
        lines = [
            '---',
            f'doc_id: {memory.get("doc_id", "")}',
            f'title: {memory.get("title", memory.get("label", ""))}',
            f'label: {memory.get("label", "")}',
            f'importance: {memory.get("importance", "")}',
            f'category: {memory.get("content_category", "")}',
            f'sub_category: {memory.get("sub_category", "")}',
            f'weight: {memory.get("weight", 0)}',
            f'depth: {memory.get("depth", "概述")}',
            f'scope: {memory.get("scope", "session")}',
            f'tier: {memory.get("tier", "warm")}',
            f'memory_tier: {memory.get("memory_tier", "warm")}',
            f'memory_type: {memory.get("memory_type", "session")}',
            f'stability: {memory.get("stability", "半静态")}',
            f'confidence: {memory.get("confidence", "推测")}',
            f'scene: {memory.get("scene", "")}',
            f'emotion: {memory.get("emotion", "neutral")}',
            f'created: {memory.get("create_time", "")}',
            f'updated: {memory.get("tier_updated_at", "")}',
        ]
        if memory.get("keywords"):
            lines.append(f'keywords: {memory["keywords"]}')
        if memory.get("tags"):
            lines.append(f'tags: [{memory["tags"]}]')
        if memory.get("project"):
            lines.append(f'project: {memory["project"]}')
        lines.extend(['---', ''])
        return '\n'.join(lines)
    
    def _get_md_path(self, memory: Dict) -> Path:
        """根据记忆信息生成 MD 文件路径"""
        category = memory.get('content_category', '') or '未分类'
        doc_id = memory.get('doc_id', 0)
        summary = (memory.get('summary', '') or '')[:50]

        # 清理文件名（移除非法字符和换行符）
        safe_summary = re.sub(r'[<>:"/\\|?*\n\r\t]', '', summary).strip()
        safe_summary = re.sub(r'\s+', ' ', safe_summary)  # 合并多个空格
        if not safe_summary:
            safe_summary = f'memory_{doc_id}'

        # 限制文件名长度
        if len(safe_summary) > 80:
            safe_summary = safe_summary[:80]

        filename = f'{safe_summary}.md'
        return self.export_dir / category / filename
    
    def sync_sqlite_to_md(self) -> int:
        """
        SQLite → MD 同步

        返回：同步的记忆数量
        """
        conn = self._get_db_connection()

        # 获取所有记忆
        memories = conn.execute('''
            SELECT doc_id, summary, compact_content, content_category,
                   sub_category, label, importance, weight, scope,
                   create_time, tier_updated_at, tags, depth, title,
                   memory_tier, memory_type, stability, confidence,
                   keywords, project, scene, emotion, tier
            FROM memory_classify
            WHERE compact_content IS NOT NULL AND compact_content != ''
        ''').fetchall()

        synced_count = 0
        for memory in memories:
            memory_dict = dict(memory)

            # 生成 MD 内容
            frontmatter = self._build_frontmatter(memory_dict)
            content = memory_dict.get('compact_content', '')
            md_content = f"{frontmatter}\n\n{content}"

            # 计算哈希
            content_hash = self._compute_hash(md_content)

            # 检查文件是否已存在且未变化
            md_path = self._get_md_path(memory_dict)
            if md_path.exists():
                existing_content = md_path.read_text(encoding='utf-8')
                existing_hash = self._compute_hash(existing_content)
                if existing_hash == content_hash:
                    continue  # 无变化，跳过

            # 写入 MD 文件
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(md_content, encoding='utf-8')
            synced_count += 1

        return synced_count
    
    def sync_md_to_sqlite(self) -> int:
        """
        MD → SQLite 同步

        返回：同步的记忆数量
        """
        conn = self._get_db_connection()
        synced_count = 0

        # 遍历所有 MD 文件
        for md_file in self.export_dir.rglob('*.md'):
            if md_file.name in ('INDEX.md', '_moc.md'):
                continue

            try:
                md_content = md_file.read_text(encoding='utf-8')
                frontmatter, body = self._parse_frontmatter(md_content)

                if not frontmatter or 'doc_id' not in frontmatter:
                    continue

                doc_id = int(frontmatter['doc_id'])

                # 检查 SQLite 中是否存在
                existing = conn.execute(
                    'SELECT doc_id, tier_updated_at FROM memory_classify WHERE doc_id = ?',
                    (doc_id,)
                ).fetchone()

                if existing:
                    # 检查是否需要更新
                    existing_time = existing['tier_updated_at'] or ''
                    new_time = frontmatter.get('updated', '')

                    if new_time > existing_time:
                        # MD 更新时间更新，同步到 SQLite
                        conn.execute('''
                            UPDATE memory_classify
                            SET compact_content = ?, tier_updated_at = ?
                            WHERE doc_id = ?
                        ''', (body, new_time, doc_id))
                        synced_count += 1
                else:
                    # 新记忆，插入 SQLite
                    conn.execute('''
                        INSERT INTO memory_classify
                        (doc_id, summary, compact_content, content_category,
                         sub_category, label, importance, weight, scope,
                         memory_tier, create_time, tier_updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        doc_id,
                        frontmatter.get('label', ''),
                        body,
                        frontmatter.get('category', ''),
                        frontmatter.get('sub_category', ''),
                        frontmatter.get('label', ''),
                        frontmatter.get('importance', 'P2'),
                        int(frontmatter.get('weight', 50)),
                        frontmatter.get('scope', 'session'),
                        frontmatter.get('tier', 'warm'),
                        frontmatter.get('created', ''),
                        frontmatter.get('updated', '')
                    ))
                    synced_count += 1

                conn.commit()

            except Exception as e:
                print(f"  同步 MD 文件失败 {md_file}: {e}")
                continue
        return synced_count

    def sync_one_to_md(self, doc_id: int) -> bool:
        """同步单条记忆 SQLite → MD（用于 update_memory 后自动更新）

        Args:
            doc_id: 要同步的记忆 doc_id

        Returns:
            是否成功
        """
        conn = self._get_db_connection()
        memory = conn.execute('''
            SELECT doc_id, summary, compact_content, content_category,
                   sub_category, label, importance, weight, scope,
                   create_time, tier_updated_at, tags, depth, title,
                   memory_tier, memory_type, stability, confidence,
                   keywords, project, scene, emotion, tier
            FROM memory_classify
            WHERE doc_id = ?
        ''', (doc_id,)).fetchone()

        if not memory:
            return False

        memory_dict = dict(memory)
        frontmatter = self._build_frontmatter(memory_dict)
        content = memory_dict.get('compact_content', '')
        md_content = f"{frontmatter}\n\n{content}"

        md_path = self._get_md_path(memory_dict)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(md_content, encoding='utf-8')
        return True

    def delete_one_md(self, doc_id: int) -> bool:
        """删除单条记忆对应的 MD 文件（用于 forget_memory 后自动清理）

        Args:
            doc_id: 要删除的记忆 doc_id

        Returns:
            文件是否存在且已删除
        """
        conn = self._get_db_connection()
        memory = conn.execute('''
            SELECT content_category, summary, doc_id
            FROM memory_classify
            WHERE doc_id = ?
        ''', (doc_id,)).fetchone()

        if not memory:
            return False

        memory_dict = dict(memory)
        md_path = self._get_md_path(memory_dict)

        if md_path.exists():
            md_path.unlink()
            return True
        return False

    def sync_all(self, direction: str = 'both') -> Dict[str, int]:
        """
        执行完整同步
        
        Args:
            direction: 同步方向 ('sqlite_to_md', 'md_to_sqlite', 'both')
        
        Returns:
            各步骤同步的数量
        """
        results = {
            'sqlite_to_md': 0,
            'md_to_sqlite': 0,
        }
        
        if direction in ('sqlite_to_md', 'both'):
            print("  同步 SQLite → MD...")
            results['sqlite_to_md'] = self.sync_sqlite_to_md()
            print(f"    完成: {results['sqlite_to_md']} 条")
        
        if direction in ('md_to_sqlite', 'both'):
            print("  同步 MD → SQLite...")
            results['md_to_sqlite'] = self.sync_md_to_sqlite()
            print(f"    完成: {results['md_to_sqlite']} 条")
        
        return results


class MdFileHandler(FileSystemEventHandler):
    """MD 文件变更事件处理器"""

    def __init__(self, db_path: str, export_dir: str, debounce_sec: float = 2.0):
        self.db_path = db_path
        self.export_dir = Path(export_dir)
        self.debounce_sec = debounce_sec
        self._pending: dict[str, float] = {}
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._name_map: dict[str, int] = {}  # filename_stem → doc_id

    def _build_name_map(self):
        """扫描 export 目录构建 filename → doc_id 映射"""
        sync = MemorySync(self.db_path, str(self.export_dir))
        conn = sync._get_db_connection()
        for md_file in self.export_dir.rglob('*.md'):
            if md_file.name in ('_moc.md', 'INDEX.md'):
                continue
            try:
                content = md_file.read_text(encoding='utf-8')
                fm, _ = sync._parse_frontmatter(content)
                if fm and 'doc_id' in fm:
                    self._name_map[md_file.stem] = int(fm['doc_id'])
            except Exception:
                continue

    def on_created(self, event):
        if event.is_directory:
            return
        if not event.src_path.endswith('.md'):
            return
        self._schedule(event.src_path)

    def on_modified(self, event):
        if event.is_directory:
            return
        if not event.src_path.endswith('.md'):
            return
        self._schedule(event.src_path)

    def on_deleted(self, event):
        if event.is_directory:
            return
        if not event.src_path.endswith('.md'):
            return
        self._schedule(event.src_path)

    def on_moved(self, event):
        if event.is_directory:
            return
        if event.dest_path.endswith('.md'):
            self._schedule(event.dest_path)

    def _schedule(self, path: str):
        basename = Path(path).name
        if basename in ('_moc.md', 'INDEX.md', '.obsidian'):
            return

        with self._lock:
            # 首次使用时构建文件名映射
            if not self._name_map:
                self._build_name_map()

            self._pending[path] = time.time()
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce_sec, self._flush)
            self._timer.daemon = True
            self._timer.start()

    _WIKILINK_RE = re.compile(r'\[\[([^\]|]+)(?:\|[^\]]*)?\]\]')

    def _parse_wikilinks(self, body: str) -> list[str]:
        """从 MD 正文提取 [[文件名]] wiki 链接"""
        return self._WIKILINK_RE.findall(body)

    def _sync_wikilinks_to_cross_ref(self, doc_id: int, body: str, conn: sqlite3.Connection):
        """扫描 MD 正文的 [[wikilinks]] 并写入 memory_cross_ref"""
        for target_name in self._parse_wikilinks(body):
            target_doc_id = self._name_map.get(target_name)
            if target_doc_id is None or target_doc_id == doc_id:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO memory_cross_ref "
                "(doc_id, related_doc_id, relation_type, note) "
                "VALUES (?, ?, 'related', '双向链接（自动同步）')",
                (doc_id, target_doc_id)
            )

    def _flush(self):
        with self._lock:
            pending = dict(self._pending)
            self._pending.clear()
            self._timer = None

            # 刷新文件名映射（可能已有新增/重命名文件）
            self._build_name_map()

        if not pending:
            return

        sync = MemorySync(self.db_path, str(self.export_dir))
        for path, _ in pending.items():
            try:
                p = Path(path)
                if not p.exists():
                    # 文件被删除 → SQLite 软删除
                    doc_id = self._find_doc_id_by_path(p)
                    if doc_id:
                        conn = sync._get_db_connection()
                        conn.execute(
                            "UPDATE document_files SET is_deleted = 1 WHERE id = ?",
                            (doc_id,)
                        )
                        conn.commit()
                    continue

                # 文件新增或修改 → 同步到 SQLite
                md_content = p.read_text(encoding='utf-8')
                frontmatter, body = sync._parse_frontmatter(md_content)
                if not frontmatter or 'doc_id' not in frontmatter:
                    continue

                doc_id = int(frontmatter['doc_id'])
                conn = sync._get_db_connection()
                existing = conn.execute(
                    'SELECT doc_id, tier_updated_at FROM memory_classify WHERE doc_id = ?',
                    (doc_id,)
                ).fetchone()

                if existing:
                    existing_time = existing['tier_updated_at'] or ''
                    new_time = frontmatter.get('updated', '')
                    if new_time > existing_time:
                        conn.execute('''
                            UPDATE memory_classify
                            SET compact_content = ?, tier_updated_at = ?
                            WHERE doc_id = ?
                        ''', (body, new_time, doc_id))
                        conn.commit()
                else:
                    conn.execute('''
                        INSERT INTO memory_classify
                        (doc_id, summary, compact_content, content_category,
                         sub_category, label, importance, weight, scope,
                         memory_tier, create_time, tier_updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        doc_id,
                        frontmatter.get('label', ''),
                        body,
                        frontmatter.get('category', ''),
                        frontmatter.get('sub_category', ''),
                        frontmatter.get('label', ''),
                        frontmatter.get('importance', 'P2'),
                        int(frontmatter.get('weight', 50)),
                        frontmatter.get('scope', 'session'),
                        frontmatter.get('tier', 'warm'),
                        frontmatter.get('created', ''),
                        frontmatter.get('updated', '')
                    ))
                    conn.commit()

                # 扫描 [[wikilinks]] → 写入 memory_cross_ref
                self._sync_wikilinks_to_cross_ref(doc_id, body, conn)
                conn.commit()

            except Exception:
                continue

    def _find_doc_id_by_path(self, md_path: Path) -> int | None:
        """从 MD 文件路径反查 doc_id"""
        base_name = md_path.stem
        doc_id = self._name_map.get(base_name)
        if doc_id is not None:
            return doc_id
        match = re.search(r'memory_(\d+)$', base_name)
        if match:
            return int(match.group(1))
        return None


class MdWatcher:
    """MD 文件变更监听器（Watchdog 封装）

    在后台线程监听 export 目录，MD 文件变更时自动同步到 SQLite。
    """

    def __init__(self, db_path: str, export_dir: str, debounce_sec: float = 2.0):
        if not _HAS_WATCHDOG:
            raise ImportError("需要安装 watchdog: pip install watchdog")

        self.db_path = db_path
        self.export_dir = str(Path(export_dir).resolve())
        self.debounce_sec = debounce_sec
        self._observer: Observer | None = None
        self._handler: MdFileHandler | None = None

    def start(self):
        """启动后台监听"""
        if self._observer is not None:
            return

        self._handler = MdFileHandler(
            self.db_path, self.export_dir, self.debounce_sec
        )
        self._observer = Observer()
        self._observer.schedule(self._handler, self.export_dir, recursive=True)
        self._observer.daemon = True
        self._observer.start()

    def stop(self):
        """停止监听"""
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None
            self._handler = None

    @property
    def is_running(self) -> bool:
        return self._observer is not None and self._observer.is_alive()
