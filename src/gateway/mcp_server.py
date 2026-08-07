from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import deque
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from ..core.enums import ClientType
from ..storage.manager import StorageManager

logger = logging.getLogger(__name__)

MAX_CACHE_SIZE = 100
MAX_RETRY = 5

TOOLS = [
    Tool(
        name="read_short_memory",
        description="读取短期会话记忆",
        inputSchema={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "default": ""},
                "start_time": {"type": "string", "default": ""},
                "end_time": {"type": "string", "default": ""},
                "limit": {"type": "integer", "default": 10},
            },
        },
    ),
    Tool(
        name="read_long_memory",
        description="读取长期全局记忆",
        inputSchema={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "default": ""},
                "start_time": {"type": "string", "default": ""},
                "end_time": {"type": "string", "default": ""},
                "limit": {"type": "integer", "default": 20},
            },
        },
    ),
    Tool(
        name="search_planning",
        description="搜索规划类文档",
        inputSchema={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "default": ""},
                "limit": {"type": "integer", "default": 15},
            },
        },
    ),
    Tool(
        name="search_selfimprove",
        description="搜索SelfImprove文档",
        inputSchema={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "default": ""},
                "limit": {"type": "integer", "default": 15},
            },
        },
    ),
    Tool(
        name="search_archive",
        description="搜索归档文档",
        inputSchema={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "default": ""},
                "limit": {"type": "integer", "default": 15},
            },
        },
    ),
    Tool(
        name="merge_memory",
        description="合并多条重复记忆",
        inputSchema={
            "type": "object",
            "properties": {
                "doc_id_list": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["doc_id_list"],
        },
    ),
    Tool(
        name="archive_memory",
        description="将记忆转为归档高权重记忆",
        inputSchema={
            "type": "object",
            "properties": {
                "doc_id": {"type": "integer"},
            },
            "required": ["doc_id"],
        },
    ),
    Tool(
        name="search_memory",
        description="向量语义搜索记忆（最强大的搜索工具，理解语义意图）",
        inputSchema={
            "type": "object",
            "properties": {
                "query_text": {"type": "string", "description": "搜索关键词（自然语言，自动理解语义）"},
                "top_k": {"type": "integer", "default": 5, "description": "返回条数"},
                "namespace": {"type": "string", "default": "", "description": "命名空间过滤"},
                "sim_threshold": {"type": "number", "default": 0.78, "description": "相似度阈值(0-1)"},
            },
            "required": ["query_text"],
        },
    ),
    Tool(
        name="add",
        description="添加新记忆（快餐模式：LLM分类+实体提取，跳过规则冲突检测）",
        inputSchema={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "记忆内容"},
            },
            "required": ["content"],
        },
    ),
]


class MCPServer:
    def __init__(self, storage: StorageManager, heartbeat_interval: int = 3,
                 multi_client: bool = True):
        self.storage = storage
        self._server = Server("memory-workstation")
        self.heartbeat_interval = heartbeat_interval
        self.multi_client = multi_client
        self._connected_clients: dict[str, float] = {}
        self._disconnect_cache: deque = deque(maxlen=MAX_CACHE_SIZE)
        self._lock = threading.Lock()
        self._heartbeat_task: asyncio.Task = None

    def setup_handlers(self):
        self._server.list_tools = self._list_tools
        self._server.call_tool = self._call_tool

    async def _list_tools(self) -> list[Tool]:
        return TOOLS

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> list[TextContent]:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self._handle_tool(name, arguments),
        )
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    def _handle_tool(self, name: str, args: dict) -> dict:
        try:
            if name == "read_short_memory":
                return self._search("short", args)
            elif name == "read_long_memory":
                return self._search("long", args)
            elif name == "search_planning":
                return self._search_by_label("planning_doc", args)
            elif name == "search_selfimprove":
                return self._search_by_label("self_improve_learn", args)
            elif name == "search_archive":
                return self._search_by_label("compact_archive", args)
            elif name == "merge_memory":
                return self._merge(args)
            elif name == "archive_memory":
                return self._archive(args)
            elif name == "search_memory":
                return self._search_memory(args)
            elif name == "add":
                return self._add_memory(args)
            else:
                return {"code": 404, "msg": f"Unknown tool: {name}", "data": {}}
        except Exception as e:
            logger.error("Tool %s error: %s", name, e)
            return {"code": 500, "msg": str(e), "data": {}}

    def _search(self, tier: str, args: dict) -> dict:
        keyword = args.get("keyword", "")
        limit = args.get("limit", 20)

        results = self.storage.sqlite.search_memory(
            keyword=keyword,
            tier=tier,
            limit=limit,
        )
        for r in results:
            self.storage.sqlite.record_access(
                self._get_doc_id(r["file_path"]),
                ClientType.MCP_CLAUDE.value,
            )
        cleaned = [{"content": r.get("compact_content", ""), "weight": r.get("weight", 50), "importance": r.get("importance", "P2")} for r in results]

        # 关键词匹配的全局规则 top-3，插到结果前列
        if keyword:
            rules = self.storage.sqlite.search_global_rules_with_tracking(keyword, limit=3)
            for r in rules:
                cleaned.insert(0, {
                    "content": f"[全局规则:{r['priority']}] {r['rule_text']}",
                    "weight": 95,
                    "importance": "P0" if r["priority"] in ("high", "critical") else "P1",
                    "_rule": True,
                })

        return {"code": 0, "msg": "success", "data": {"list": cleaned}}

    def _search_by_label(self, label: str, args: dict) -> dict:
        keyword = args.get("keyword", "")
        limit = args.get("limit", 15)

        results = self.storage.sqlite.search_memory(
            keyword=keyword,
            label=label,
            limit=limit,
        )
        cleaned = [{"content": r.get("compact_content", ""), "weight": r.get("weight", 50), "importance": r.get("importance", "P2")} for r in results]

        if keyword:
            rules = self.storage.sqlite.search_global_rules_with_tracking(keyword, limit=2)
            for r in rules:
                cleaned.insert(0, {
                    "content": f"[全局规则:{r['priority']}] {r['rule_text']}",
                    "weight": 95,
                    "importance": "P0" if r["priority"] in ("high", "critical") else "P1",
                    "_rule": True,
                })

        return {"code": 0, "msg": "success", "data": {"list": cleaned}}

    def _get_doc_id(self, file_path: str) -> int:
        row = self.storage.sqlite.get_document_by_path(file_path)
        return row["id"] if row else 0

    def _merge(self, args: dict) -> dict:
        doc_ids = args.get("doc_id_list", [])
        if len(doc_ids) < 2:
            return {"code": 422, "msg": "Need at least 2 doc_ids", "data": {}}
        snippets = self.storage.sqlite.get_snippets_by_ids(doc_ids)
        merged = "\n---\n".join(snippets)
        import hashlib
        new_path = f"_merged_{doc_ids[0]}_{doc_ids[1]}.md"
        new_hash = hashlib.sha256(merged.encode()).hexdigest()
        new_id = self.storage.sqlite.upsert_document(
            file_path=new_path,
            file_hash=new_hash,
            file_size=len(merged),
            create_time="",
            modify_time="",
            origin_source="merge",
            raw_text_snippet=merged[:500],
        )
        return {"code": 0, "msg": "success", "data": {"new_doc_id": new_id}}

    def _archive(self, args: dict) -> dict:
        doc_id = args.get("doc_id")
        if not doc_id:
            return {"code": 422, "msg": "doc_id required", "data": {}}
        self.storage.sqlite.update_classification(doc_id, "compact_archive", "archive", 90)
        return {"code": 0, "msg": "success", "data": {"tier": "archive", "new_weight": 90}}

    def _search_memory(self, args: dict) -> dict:
        """向量语义搜索（加权排序）"""
        query_text = args.get("query_text", "")
        top_k = min(args.get("top_k", 5), 20)
        namespace = args.get("namespace", None)
        sim_threshold = args.get("sim_threshold", 0.78)

        if not query_text:
            return {"code": 422, "msg": "query_text required", "data": {"list": []}}

        # 全局规则匹配（优先于向量结果）
        rules = self.storage.sqlite.search_global_rules_with_tracking(query_text, limit=3)

        results = []
        # 向量语义搜索：嵌入模型就绪时用向量召回 + 加权排序
        llm = getattr(self.storage, '_llm', None)
        if llm and llm.has_embed_model:
            try:
                query_vector = llm.embed(query_text)
                if query_vector:
                    vector_results = self.storage.vector.search(
                        query_vector=query_vector,
                        top_k=top_k * 2,
                        threshold=sim_threshold,
                    )
                    doc_ids = [r["doc_id"] for r in vector_results]
                    if doc_ids:
                        sql_results = self.storage.sqlite.get_memories_by_doc_ids(doc_ids)
                        sql_map = {r["doc_id"]: r for r in sql_results}
                        for vr in vector_results:
                            did = vr["doc_id"]
                            if did in sql_map:
                                merged = dict(sql_map[did])
                                merged["similarity"] = vr.get("similarity", 0)
                                results.append(merged)

                    if results:
                        max_w = max((r.get("weight", 50) for r in results), default=1) or 1
                        for r in results:
                            sim = r.get("similarity", 0)
                            w = r.get("weight", 50) / max_w
                            r["_score"] = sim * 0.7 + w * 0.3
                        results.sort(key=lambda x: x.get("_score", 0), reverse=True)

                    results = results[:top_k]

                    for r in results:
                        self.storage.sqlite.record_access(
                            r.get("doc_id", 0), ClientType.MCP_CLAUDE.value,
                        )

            except Exception as e:
                logger.error("MCP search_memory error: %s", e)
                return {"code": 500, "msg": str(e), "data": {"list": []}}

        cleaned = [{"content": r.get("compact_content", ""), "weight": r.get("weight", 50), "importance": r.get("importance", "P2")} for r in results]
        # 全局规则插到结果前列
        for r in rules:
            cleaned.insert(0, {
                "content": f"[全局规则:{r['priority']}] {r['rule_text']}",
                "weight": 95,
                "importance": "P0" if r["priority"] in ("high", "critical") else "P1",
                "_rule": True,
            })
        return {
            "code": 0,
            "msg": "success",
            "data": {
                "list": cleaned,
                "total": len(cleaned),
            },
        }

    def _add_memory(self, args: dict) -> dict:
        import time
        content = args.get("content", "")
        if not content:
            return {"code": 422, "msg": "content required", "data": {}}
        doc_id = self.storage.sqlite.upsert_document(
            file_path=f"_mcp_add_{int(time.time()*1000)}.md",
            file_hash=str(hash(content)),
            file_size=len(content),
            create_time="",
            modify_time="",
            origin_source="mcp",
            raw_text_snippet=content[:500],
        )
        from ..pipeline.pipeline import ClassifyPipeline
        llm = getattr(self.storage, '_llm', None)
        pipeline = ClassifyPipeline(llm, self.storage)
        pipeline.process_one(doc_id, content, fast_lane=True)
        return {"code": 0, "msg": "success", "data": {"doc_id": doc_id}}

    def _cache_request(self, name: str, args: dict):
        with self._lock:
            self._disconnect_cache.append((name, args, time.time()))
            if len(self._disconnect_cache) > MAX_CACHE_SIZE:
                self._disconnect_cache.popleft()

    def _flush_cache(self):
        with self._lock:
            cached = list(self._disconnect_cache)
            self._disconnect_cache.clear()
        for name, args, ts in cached:
            try:
                self._handle_tool(name, args)
                logger.info("Replayed cached request: %s", name)
            except Exception as e:
                logger.error("Replay failed for %s: %s", name, e)

    def _register_client(self, client_id: str):
        with self._lock:
            self._connected_clients[client_id] = time.time()

    def _unregister_client(self, client_id: str):
        with self._lock:
            self._connected_clients.pop(client_id, None)

    @property
    def connected_count(self) -> int:
        with self._lock:
            return len(self._connected_clients)

    async def _heartbeat_loop(self):
        while True:
            await asyncio.sleep(self.heartbeat_interval)
            logger.debug("Heartbeat: %d clients connected", self.connected_count)

    async def run_stdio(self):
        self.setup_handlers()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        try:
            async with stdio_server() as (read_stream, write_stream):
                self._register_client("stdio-main")
                try:
                    await self._server.run(read_stream, write_stream)
                finally:
                    self._unregister_client("stdio-main")
                    self._flush_cache()
        except Exception as e:
            logger.warning("MCP stdio unavailable (background thread): %s", e)
        finally:
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
