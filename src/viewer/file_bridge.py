"""Memory Workstation v2 — Data Bridge for Viewer"""
from __future__ import annotations

import os
import json
import logging
import sqlite3
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Config ──────────────────────────────────────────────────
_DEFAULT_CFG = "D:\\MemoryWorkstation\\.memory-workstation"
_CFG_FILE = "MemoryWorkstation.cfg"


def _load_config() -> dict:
    """Load config from exe directory or default."""
    cfg_path = Path(__file__).resolve().parent.parent.parent / _CFG_FILE
    if cfg_path.is_file():
        try:
            lines = cfg_path.read_text(encoding="utf-8").strip().splitlines()
            if lines:
                return {"md_dir": lines[0].strip()}
        except Exception:
            pass
    return {"md_dir": _DEFAULT_CFG}


def _save_config(config: dict):
    """Save config to exe directory."""
    cfg_path = Path(__file__).resolve().parent.parent.parent / _CFG_FILE
    try:
        cfg_path.write_text(config.get("md_dir", _DEFAULT_CFG), encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to save config: %s", e)


# ─── Data Classes ────────────────────────────────────────────
@dataclass
class MemoryItem:
    doc_id: int
    label: str
    content: str
    summary: str
    category: str
    importance: str
    weight: int
    file_path: str
    wikilinks: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


@dataclass
class CategoryNode:
    name: str
    children: list["CategoryNode"] = field(default_factory=list)
    count: int = 0


@dataclass
class GraphData:
    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)


# ─── DataBridge ──────────────────────────────────────────────
class DataBridge:
    """Bridge between Python backend and QML/JS frontend."""

    def __init__(self, md_dir: str):
        self.md_dir = md_dir
        self._memories: list[MemoryItem] = []
        self._loaded = False
        self._dirty = False

    def _ensure_loaded(self):
        if not self._loaded:
            self._load_memories()

    def _load_memories(self):
        """Load memories from SQLite database."""
        db_path = os.path.join(self.md_dir, "meta_agents.sqlite")
        if not os.path.isfile(db_path):
            logger.warning("Database not found: %s", db_path)
            self._loaded = True
            return

        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Check if tables exist
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row[0] for row in cursor.fetchall()}

            if "memory_classify" not in tables:
                logger.warning("Table memory_classify not found")
                self._loaded = True
                return

            # Load memories with classification
            query = """
                SELECT d.id as doc_id, d.file_path,
                       c.label, c.summary, c.content_category as category,
                       c.importance, c.weight, c.tags
                FROM document_files d
                LEFT JOIN memory_classify c ON d.id = c.doc_id
                WHERE d.is_deleted = 0
                ORDER BY d.id
            """
            cursor.execute(query)
            rows = cursor.fetchall()

            self._memories = []
            for row in rows:
                # Derive label from file_path if not set
                label = row["label"] or ""
                if not label:
                    fp = row["file_path"] or ""
                    label = fp.split("/")[-1].replace(".md", "") if fp else f"item_{row['doc_id']}"

                item = MemoryItem(
                    doc_id=row["doc_id"],
                    label=label,
                    content="",  # Don't load raw_text_snippet to avoid encoding issues
                    summary=row["summary"] or "",
                    category=row["category"] or "未分类",
                    importance=row["importance"] or "P3",
                    weight=row["weight"] or 50,
                    file_path=row["file_path"] or "",
                    tags=(row["tags"] or "").split(",") if row["tags"] else [],
                )
                self._memories.append(item)

            # Load cross references
            if "memory_cross_ref" in tables:
                cursor.execute("SELECT doc_id, related_doc_id FROM memory_cross_ref")
                for row in cursor.fetchall():
                    for mem in self._memories:
                        if mem.doc_id == row["doc_id"]:
                            # Find the related memory's label
                            for m2 in self._memories:
                                if m2.doc_id == row["related_doc_id"]:
                                    mem.wikilinks.append(m2.label)
                                    break

            conn.close()
            logger.info("Loaded %d memories", len(self._memories))

        except Exception as e:
            logger.error("Failed to load memories: %s", e)

        self._loaded = True

    def get_memories(self) -> list[MemoryItem]:
        self._ensure_loaded()
        return self._memories

    def get_memory(self, doc_id: int) -> Optional[MemoryItem]:
        self._ensure_loaded()
        for m in self._memories:
            if m.doc_id == doc_id:
                return m
        return None

    def search_memories(self, query: str) -> list[MemoryItem]:
        self._ensure_loaded()
        query_lower = query.lower()
        results = []
        for m in self._memories:
            if (query_lower in m.label.lower() or
                query_lower in m.content.lower() or
                query_lower in m.summary.lower()):
                results.append(m)
        return results

    def update_memory(self, doc_id: int, **kwargs) -> bool:
        """Update memory fields in database."""
        try:
            conn = sqlite3.connect(os.path.join(self.md_dir, "meta_agents.sqlite"))
            cursor = conn.cursor()

            # Update memory_classify fields
            classify_fields = {"label", "summary", "importance", "weight", "content_category", "tags"}
            classify_updates = {k: v for k, v in kwargs.items() if k in classify_fields}
            if classify_updates:
                set_clause = ", ".join(f"{k} = ?" for k in classify_updates)
                values = list(classify_updates.values()) + [doc_id]
                cursor.execute(f"UPDATE memory_classify SET {set_clause} WHERE doc_id = ?", values)

            conn.commit()
            conn.close()

            # Update in-memory cache
            for m in self._memories:
                if m.doc_id == doc_id:
                    for k, v in kwargs.items():
                        if hasattr(m, k):
                            setattr(m, k, v)
                    break

            self._dirty = True
            return True
        except Exception as e:
            logger.error("Failed to update memory %d: %s", doc_id, e)
            return False

    def delete_memory(self, doc_id: int) -> bool:
        """Soft delete a memory."""
        try:
            conn = sqlite3.connect(os.path.join(self.md_dir, "meta_agents.sqlite"))
            cursor = conn.cursor()
            cursor.execute("UPDATE document_files SET is_deleted = 1 WHERE id = ?", (doc_id,))
            conn.commit()
            conn.close()

            # Remove from in-memory cache
            self._memories = [m for m in self._memories if m.doc_id != doc_id]
            self._dirty = True
            return True
        except Exception as e:
            logger.error("Failed to delete memory %d: %s", doc_id, e)
            return False

    def reclassify_memory(self, doc_id: int, new_category: str) -> bool:
        """Move memory to a different category."""
        return self.update_memory(doc_id, content_category=new_category)

    def adjust_weight(self, doc_id: int, delta: int) -> bool:
        """Adjust memory weight by delta."""
        mem = self.get_memory(doc_id)
        if mem:
            new_weight = max(0, min(100, mem.weight + delta))
            return self.update_memory(doc_id, weight=new_weight)
        return False

    def get_categories(self) -> list[CategoryNode]:
        self._ensure_loaded()
        cat_map: dict[str, CategoryNode] = {}
        for m in self._memories:
            cat = m.category or "未分类"
            if cat not in cat_map:
                cat_map[cat] = CategoryNode(name=cat)
            cat_map[cat].count += 1
        return list(cat_map.values())

    def get_graph_data(
        self, center_id: int | None = None, max_nodes: int = 200,
    ) -> GraphData:
        self._ensure_loaded()
        nodes = []
        for m in self._memories:
            nodes.append({
                "id": m.doc_id,
                "label": m.label,
                "category": m.category,
                "weight": m.weight,
                "importance": m.importance,
                "summary": m.summary[:60],
            })

        edges = []
        for m in self._memories:
            if not m.wikilinks:
                continue
            for wl in m.wikilinks:
                target = self._find_by_label(wl)
                if target and target.doc_id != m.doc_id:
                    edges.append({
                        "source": m.doc_id,
                        "target": target.doc_id,
                        "type": "关联",
                    })

        if center_id is not None:
            linked_ids = {center_id}
            for e in edges:
                if e["source"] == center_id:
                    linked_ids.add(e["target"])
                elif e["target"] == center_id:
                    linked_ids.add(e["source"])

            nodes = [n for n in nodes if n["id"] in linked_ids]
            node_ids = {n["id"] for n in nodes}
            edges = [
                e for e in edges
                if e["source"] in node_ids and e["target"] in node_ids
            ]

        if len(nodes) > max_nodes:
            nodes = nodes[:max_nodes]
            node_ids = {n["id"] for n in nodes}
            edges = [e for e in edges if e["source"] in node_ids and e["target"] in node_ids]

        return GraphData(nodes=nodes, edges=edges)

    def _find_by_label(self, label: str) -> MemoryItem | None:
        for m in self._memories:
            if m.label == label or m.file_path.endswith(label + ".md"):
                return m
        return None

    def get_stats(self) -> dict:
        self._ensure_loaded()
        imp_counts: dict[str, int] = {}
        cat_counts: dict[str, int] = {}
        for m in self._memories:
            imp_counts[m.importance] = imp_counts.get(m.importance, 0) + 1
            cat_counts[m.category] = cat_counts.get(m.category, 0) + 1

        graph = self.get_graph_data()
        return {
            "total": len(self._memories),
            "by_importance": imp_counts,
            "by_category": cat_counts,
            "graph_nodes": len(graph.nodes),
            "graph_edges": len(graph.edges),
        }

    def fetch_models(self, base_url: str, api_key: str = "") -> dict:
        """Fetch available models from API endpoint."""
        import urllib.request
        import urllib.error

        url = base_url.rstrip("/")
        base = url[:-3] if url.endswith("/v1") else url

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        errors = []

        # 1) Try Ollama /api/tags (priority, local common)
        try:
            req = urllib.request.Request(f"{base}/api/tags", headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
                if models:
                    return {"success": True, "models": sorted(models)}
        except urllib.error.URLError as e:
            errors.append(f"Ollama: {e.reason}")
        except Exception as e:
            errors.append(f"Ollama: {e}")

        # 2) Try OpenAI compatible /v1/models
        try:
            req = urllib.request.Request(f"{base}/v1/models", headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("id", "") for m in data.get("data", []) if m.get("id")]
                if models:
                    return {"success": True, "models": sorted(models)}
        except urllib.error.URLError as e:
            errors.append(f"OpenAI API: {e.reason}")
        except Exception as e:
            errors.append(f"OpenAI API: {e}")

        detail = "; ".join(errors) if errors else "未知错误"
        return {"success": False, "error": f"无法连接到 {base}\n{detail}"}

    def get_settings(self) -> dict:
        cfg = _load_config()
        return {
            "md_dir": self.md_dir,
            "staging_dir": cfg.get("staging_dir", "D:\\MemoryWorkstation\\.memory-workstation\\staging"),
            "log_dir": cfg.get("log_dir", "D:\\MemoryWorkstation\\.memory-workstation\\logs"),
            "log_level": cfg.get("log_level", "INFO"),
            "api_provider": cfg.get("api_provider", "Ollama（本地）"),
            "api_key": cfg.get("api_key", ""),
            "api_base_url": cfg.get("api_base_url", "http://localhost:11434/v1"),
            "api_model": cfg.get("api_model", "llama3.2"),
        }

    def save_settings(self, settings: dict) -> bool:
        try:
            config = _load_config()
            cfg_changed = False

            md_dir = settings.get("md_dir", "")
            if md_dir and os.path.isdir(md_dir):
                if md_dir != self.md_dir:
                    self.md_dir = md_dir
                    self._dirty = True
                config["md_dir"] = md_dir
                cfg_changed = True

            for key in ("staging_dir", "log_dir", "log_level", "api_provider", "api_key", "api_base_url", "api_model"):
                if key in settings:
                    config[key] = settings[key]
                    cfg_changed = True

            if cfg_changed:
                _save_config(config)
                logger.info("Settings saved: %s", settings)
            return True
        except Exception as e:
            logger.error("Failed to save settings: %s", e)
            return False
