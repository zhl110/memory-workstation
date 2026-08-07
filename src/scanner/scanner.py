from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from ..core.config import Config
from ..core.enums import DocumentLabel, LABEL_TO_TIER
from ..storage.manager import StorageManager
from ..utils.frontmatter import parse_frontmatter, extract_metadata_from_file
from ..import_manager.parser import DocumentParser

logger = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml", ".jsonl", ".py", ".js", ".ts", ".sh", ".toml", ".cfg", ".ini", ".epub", ".html", ".htm", ".docx"}
SINGLE_LINE_MAX_CHARS = 10000
FILE_LOCK_RETRY = 3
FILE_LOCK_DELAY = 0.2

LOW_VALUE_FILES = {
    "license", "license.txt", "license.md", "license-mit",
    ".gitignore", ".gitattributes", ".editorconfig",
    "readme.txt", "changelog.txt", "authors.txt",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "composer.lock", "poetry.lock", "Cargo.lock",
    "go.sum", "go.mod", "requirements.txt",
    "setup.py", "setup.cfg", "pyproject.toml",
}



def file_hash(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


_parser = DocumentParser()


def read_file_safe(filepath: str, max_size_mb: int = 5) -> Optional[str]:
    """读取文件内容，支持 chardet 编码检测 + PDF/ZIP 解析

    委托给 DocumentParser._read_file()，统一编码处理链：
    chardet（可选）→ utf-8 → gbk → latin-1
    PDF 走 PyMuPDF，ZIP 走解包拼接
    """
    try:
        size = os.path.getsize(filepath)
        if size > max_size_mb * 1024 * 1024:
            logger.warning("File too large (%d MB), skipping: %s", size // (1024*1024), filepath)
            return None
        if size == 0:
            return ""
        return _parser._read_file(filepath)
    except PermissionError:
        logger.warning("File locked: %s", filepath)
        return None
    except OSError as e:
        logger.warning("Cannot read file %s: %s", filepath, e)
        return None


def truncate_lines(text: str, max_chars: int = SINGLE_LINE_MAX_CHARS) -> str:
    lines = text.split("\n")
    truncated = []
    for line in lines:
        if len(line) > max_chars:
            truncated.append(line[:max_chars] + f"\n[TRUNCATED at {max_chars} chars]")
        else:
            truncated.append(line)
    return "\n".join(truncated)


class _FileEventHandler(FileSystemEventHandler):
    def __init__(self, scanner: "FileScanner"):
        self.scanner = scanner

    def on_created(self, event: FileSystemEvent):
        if not event.is_directory:
            self.scanner._enqueue(str(event.src_path))

    def on_modified(self, event: FileSystemEvent):
        if not event.is_directory:
            self.scanner._enqueue(str(event.src_path))


class FileScanner:
    def __init__(self, config: Config, storage: StorageManager):
        self.config = config
        self.storage = storage
        self._queue: list[str] = []
        self._queue_lock = threading.Lock()
        self._observer: Optional[Observer] = None
        self._scan_thread: Optional[threading.Thread] = None
        self._running = False
        self._on_classify: Optional[Callable[[int, str], None]] = None
        self._pending_llm: list[tuple[int, str, str]] = []

    def set_classify_callback(self, cb: Callable[[int, str], None]):
        self._on_classify = cb

    def start(self):
        self._running = True
        self._observer = Observer()
        handler = _FileEventHandler(self)

        watch_dirs = self._get_watch_dirs()
        for d in watch_dirs:
            try:
                self._observer.schedule(handler, d, recursive=True)
                logger.info("Watching: %s", d)
            except Exception as e:
                logger.error("Cannot watch %s: %s", d, e)

        self._observer.start()
        self._scan_thread = threading.Thread(target=self._scan_loop, daemon=True)
        self._scan_thread.start()
        logger.info("FileScanner started (watching %d dirs)", len(watch_dirs))

    def stop(self):
        self._running = False
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)

    def full_scan(self, force_reclassify: bool = True):
        logger.info("Starting full scan (Phase 1: Gate filter)%s...",
                    ", force reclassify all" if force_reclassify else "")
        self._pending_llm.clear()
        count = 0
        for filepath in self._iter_all_files():
            self._process_file(filepath, accumulate_llm=True, force_reclassify=force_reclassify)
            count += 1
        logger.info("Phase 1 complete: %d files scanned, %d need LLM classification",
                    count, len(self._pending_llm))
        return count, self._pending_llm

    def _get_watch_dirs(self) -> list[str]:
        dirs = []
        agent_paths = self.config.scan.agent_paths
        for p in agent_paths:
            expanded = os.path.expanduser(p)
            if os.path.exists(expanded):
                dirs.append(expanded)
                for sub in ["memory", "projects", "learnings", "chat-history"]:
                    sub_path = os.path.join(expanded, sub)
                    if os.path.exists(sub_path):
                        dirs.append(sub_path)
        for custom in self.config.scan.custom_white_path:
            if os.path.exists(custom):
                dirs.append(custom)
        return dirs

    def _iter_all_files(self):
        for d in self._get_watch_dirs():
            if os.path.isfile(d):
                if self._should_process(d):
                    yield d
            elif os.path.isdir(d):
                for root, subdirs, files in os.walk(d):
                    subdirs[:] = [s for s in subdirs if not self._is_low_value_dir(os.path.join(root, s))]
                    for f in files:
                        fp = os.path.join(root, f)
                        if self._should_process(fp):
                            yield fp

    def _is_blacklisted(self, path: str) -> bool:
        norm = path.replace("/", "\\").lower()
        for bl in self.config.scan.disk_black_list:
            if norm.startswith(bl.lower().replace("/", "\\")):
                return True
        return False

    def _is_low_value_dir(self, dirpath):
        dirname = os.path.basename(dirpath)
        dir_lower = dirpath.lower().replace("\\", "/")
        skip_dirs = {
            ".sandbox", ".sandbox-bin", ".sandbox-secrets",
            ".tmp", "node_modules", ".git", ".svn",
            "backup", "backups", "cache", ".cache",
            "logs", "log", "__pycache__", "bin", "obj",
            "dist", "build",
        }
        if dirname in skip_dirs:
            return True
        if "/.codex/" in dir_lower and "/.sandbox" in dir_lower:
            return True
        return False

    def _should_process(self, filepath: str) -> bool:
        ext = Path(filepath).suffix.lower()
        if ext not in SUPPORTED_SUFFIXES:
            return False
        if ext in self.config.scan.ignore_suffix:
            return False
        if self._is_blacklisted(filepath):
            return False
        
        filename = Path(filepath).name.lower()
        if filename in LOW_VALUE_FILES:
            return False
        
        if filename.startswith("package-lock") or filename.endswith(".lock"):
            return False
        
        try:
            size = os.path.getsize(filepath)
            if size > self.config.scan.single_file_max_size_mb * 1024 * 1024:
                return False
            if size == 0:
                return False
            if ext == ".json" and size > 500 * 1024:
                return False
        except OSError:
            return False
        return True

    def _enqueue(self, filepath: str):
        if self._should_process(filepath):
            with self._queue_lock:
                if filepath not in self._queue:
                    self._queue.append(filepath)

    def _scan_loop(self):
        while self._running:
            batch = []
            with self._queue_lock:
                batch = list(self._queue)
                self._queue.clear()
            for fp in batch:
                self._process_file(fp)
            time.sleep(1)

    def _process_file(self, filepath: str, accumulate_llm: bool = False, force_reclassify: bool = False):
        try:
            content = read_file_safe(filepath, self.config.scan.single_file_max_size_mb)
            if content is None:
                return

            # ─── custom_white_path 内的文件跳过 gate，直通 LLM ───
            custom_paths = getattr(self.config.scan, 'custom_white_path', [])
            is_custom = any(
                os.path.normpath(filepath).startswith(os.path.normpath(p))
                for p in custom_paths if p
            )
            if not is_custom:
                if not self._gate1_is_memory_file(content, filepath):
                    return
                if not self._gate2_has_value(content, filepath):
                    return

            if content.strip() == "":
                content = ""

            meta, body = parse_frontmatter(content)

            # 手动从文件读取 frontmatter（补充 parse_frontmatter 可能漏的字段）
            if not meta.get("source") or not meta.get("create_utc"):
                file_meta = extract_metadata_from_file(filepath)
                if file_meta:
                    meta.update(file_meta)

            h = file_hash(filepath)
            stat = os.stat(filepath)
            create_time = datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat()
            modify_time = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()

            existing = self.storage.sqlite.get_document_by_hash(h)
            if existing:
                if accumulate_llm:
                    doc_id = existing["id"]
                    row = self.storage.sqlite._conn.execute(
                        "SELECT doc_id FROM memory_classify WHERE doc_id=?", (doc_id,)
                    ).fetchone()
                    if not row or force_reclassify:
                        self._pending_llm.append((doc_id, content, filepath))
                return

            snippet = truncate_lines(content)[:500]
            doc_id = self.storage.sqlite.upsert_document(
                file_path=filepath,
                file_hash=h,
                file_size=stat.st_size,
                create_time=meta.get("create_utc", create_time),
                modify_time=modify_time,
                origin_source=meta.get("source", "manual"),
                raw_text_snippet=snippet,
            )

            self.storage.sqlite.upsert_processing_log(filepath, h, doc_id)

            if meta.get("auto_label"):
                try:
                    label = DocumentLabel(meta["auto_label"])
                    tier_str = meta.get("memory_tier", "short")
                    from ..core.enums import MemoryTier
                    tier = MemoryTier(tier_str) if tier_str in [t.value for t in MemoryTier] else LABEL_TO_TIER.get(label, LABEL_TO_TIER[DocumentLabel.UNKNOWN])
                    weight = int(meta.get("weight", 50))
                    self.storage.sqlite.set_classification(doc_id, label, tier, weight)
                    # auto_label已入库，但LLM仍需做摘要/适用面精选
                    # 不return，继续积累
                except (ValueError, KeyError):
                    pass

            rule_label = self._rule_based_classify(content, filepath)
            if rule_label != DocumentLabel.UNKNOWN:
                tier = LABEL_TO_TIER.get(rule_label, LABEL_TO_TIER[DocumentLabel.UNKNOWN])
                self.storage.sqlite.set_classification(
                    doc_id, rule_label, tier,
                    compact_content=content,
                )

                # 规则分类已命中 → 跳过 LLM（纯配置等不需要 LLM 摘要）
                # 例外：chat-history 路径的文件要过 LLM 精筛，不跳过
                # force_reclassify 时仍要过 LLM，不走跳过
                fp_lower = filepath.lower().replace("\\", "/")
                is_chat_history = "/chat-history/" in fp_lower
                if (not force_reclassify
                        and rule_label in {DocumentLabel.CHAT_LOG, DocumentLabel.CONFIG_INVENTORY}
                        and not (rule_label == DocumentLabel.CHAT_LOG and is_chat_history)):
                    if accumulate_llm:
                        return
                    elif self._on_classify:
                        return

            if accumulate_llm:
                self._pending_llm.append((doc_id, content, filepath))
            elif self._on_classify:
                self._on_classify(doc_id, content, filepath)

        except Exception as e:
            logger.error("Error processing %s: %s", filepath, e)
    
    def _gate1_is_memory_file(self, content: str, filepath: str) -> bool:
        fp = filepath.lower().replace("\\", "/")
        filename = os.path.basename(filepath).lower() if filepath else ""

        # 判断是否在记忆相关路径下
        MEMORY_PATH_PATTERNS = [
            "/memory/", "/learnings/", "/projects/", "/chat-history/",
            "/.codex/", "/.claude/projects/", "/.claude/plans/",
            "/agent-hub/",
        ]
        is_memory_path = any(p in fp for p in MEMORY_PATH_PATTERNS)

        # 记忆路径：放宽检查
        if is_memory_path:
            MEMORY_MARKERS = ['doc_id', 'create_utc', 'source', 'node_type', 'memory_tier', 'auto_label']
            if any(marker in content for marker in MEMORY_MARKERS):
                return True

            MEMORY_DIRS = ["/memory/", "/projects/", "/learnings/", "/chat-history/", "/.claude/projects/", "/.codex/"]
            MEMORY_EXTS = (".md", ".txt", ".yaml", ".yml")
            if any(d in fp for d in MEMORY_DIRS):
                if filepath.endswith(MEMORY_EXTS):
                    return True

            MEMORY_FILES = ["claude.md", "rules.md", "skill.md", "constitution.md", "readme.md", "agents.md"]
            if filename in MEMORY_FILES:
                return True

            if filepath.endswith('.jsonl'):
                return True

            return False

        # 非记忆路径：严格 — 必须带 YAML frontmatter
        if content.strip().startswith("---"):
            return True

        return False
    
    def _gate2_has_value(self, content: str, filepath: str = "") -> bool:
        if len(content.strip()) < 50:
            return False
        
        stripped = content.strip()
        lines = stripped.split("\n")

        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                import json
                obj = json.loads(stripped[:500])
                if isinstance(obj, dict):
                    if any(k in obj for k in ["type", "timestamp", "sessionId", "event"]):
                        return False
            except (json.JSONDecodeError, ValueError):
                pass

        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                import json
                json.loads(stripped[:500])
                return False
            except (json.JSONDecodeError, ValueError):
                pass

        if filepath and filepath.endswith(".jsonl"):
            try:
                import json
                first_line = lines[0].strip()
                if first_line.startswith("{"):
                    obj = json.loads(first_line)
                    if isinstance(obj, dict) and any(k in obj for k in ["type", "timestamp", "sessionId", "event", "operation"]):
                        return False
            except (json.JSONDecodeError, ValueError):
                pass

        ext = os.path.splitext(filepath)[1].lower() if filepath else ""
        if ext in {".py", ".js", ".ts", ".sh", ".bat", ".mjs"}:
            first_lines = content[:500]
            has_natural_language = False
            for line in first_lines.split("\n"):
                line = line.strip()
                if re.search(r'[\u4e00-\u9fff]{3,}', line):
                    has_natural_language = True
                    break
                if line.startswith("#") or line.startswith('"""') or line.startswith("'''"):
                    stop_words = set(["todo", "fixme", "hack", "xxx", "temp", "note"])
                    meaningful = [w for w in re.findall(r"[a-zA-Z]{2,}", line.lower()) if w not in stop_words]
                    if len(meaningful) >= 2:
                        has_natural_language = True
                        break
            if not has_natural_language:
                return False

        total_chars = len(stripped[:1000])
        if total_chars > 0:
            readable = len(re.findall(r'[\u4e00-\u9fff]|[a-zA-Z]{2,}', stripped[:1000]))
            ratio = readable / total_chars
            if ratio < 0.3:
                if not re.search(r'[\u4e00-\u9fff]', stripped[:200]):
                    return False

        return True

    def _rule_based_classify(self, content: str, filepath: str = "") -> DocumentLabel:
        first_lines = content[:500].lower()
        fp = filepath.lower()
        
        if "# memory-layer" in first_lines or "memory_tier:" in first_lines:
            return DocumentLabel.MEMORY_LAYER
        if "## selfimprove" in first_lines or "self_improve" in first_lines:
            return DocumentLabel.SELF_IMPROVE_LEARN
        if "## planning" in first_lines or "# plan" in first_lines:
            return DocumentLabel.PLANNING_DOC
        if "## archive" in first_lines or "compact_archive" in first_lines:
            return DocumentLabel.COMPACT_ARCHIVE
        if "## meta" in first_lines or "# rule" in first_lines or "## skill" in first_lines:
            return DocumentLabel.META_RULE
        if "## chat" in first_lines or "user:" in first_lines or "assistant:" in first_lines:
            return DocumentLabel.CHAT_LOG
        
        filename = Path(filepath).name.lower()
        
        if any(x in filename for x in ["skill", "prompt", "mcp", "claude_config"]):
            return DocumentLabel.META_RULE
        if any(x in filename for x in ["config", "plugin", "tool", "install", "package"]):
            return DocumentLabel.CONFIG_INVENTORY
        if any(x in filename for x in ["plan", "todo", "task", "roadmap"]):
            return DocumentLabel.PLANNING_DOC
        if any(x in filename for x in ["learn", "tutorial", "guide"]):
            return DocumentLabel.SELF_IMPROVE_LEARN
        if any(x in filename for x in ["chat", "conversation", "session"]):
            return DocumentLabel.CHAT_LOG
        
        if "def " in content or "class " in content or "import " in content:
            return DocumentLabel.PLANNING_DOC
        if "requirement" in first_lines or "specification" in first_lines:
            return DocumentLabel.PLANNING_DOC
        
        return DocumentLabel.UNKNOWN
