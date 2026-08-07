from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path  # V9: 快照/日志/配置路径需要
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Query, Body
from fastapi.responses import JSONResponse

from ..core.config import Config
from ..core.enums import DocumentLabel, MemoryTier, LABEL_TO_TIER
from ..storage.manager import StorageManager
from ..search.engine import SearchEngine

logger = logging.getLogger(__name__)


def create_app(config: Config, storage: StorageManager, llm=None, tray=None) -> FastAPI:
    app = FastAPI(title="Memory Workstation API", docs_url=None, redoc_url=None)
    _rate_limiter = _RateLimiter(config.api.req_limit_per_sec)
    storage._llm = llm

    def _verify_token(token: Optional[str] = Header(None)):
        if token != config.api.token:
            raise HTTPException(status_code=401, detail="Invalid token")

    def _merge_rules_into_results(keyword: str, results: list) -> list:
        """搜索关键词匹配全局规则，插到结果前列"""
        if not keyword:
            return results
        rules = storage.sqlite.search_global_rules_with_tracking(keyword, limit=3)
        for r in rules:
            results.insert(0, {
                "content": f"[全局规则:{r['priority']}] {r['rule_text']}",
                "weight": 95,
                "importance": "P0" if r["priority"] in ("high", "critical") else "P1",
                "_rule": True,
            })
        return results

    @app.middleware("http")
    async def rate_limit_middleware(request, call_next):
        if not _rate_limiter.allow():
            return JSONResponse(status_code=429, content={"code": 429, "msg": "Rate limit exceeded"})
        return await call_next(request)

    @app.post("/api/search")
    def search_v2(body: dict = Body(...), token: Optional[str] = Header(None)):
        _verify_token(token)
        query = body.get("q", "")
        top_k = min(body.get("top_k", 10), 50)
        if not query:
            return {"code": 1, "msg": "query is required", "data": {"list": []}}
        engine = SearchEngine(storage, llm)
        results = engine.search(query, top_k)
        return {"code": 0, "data": {"list": results}}

    @app.post("/api/memory/search")
    def search_memory(
        body: dict = Body(...),
        token: Optional[str] = Header(None),
    ):
        _verify_token(token)
        query_text = body.get("query_text", "")
        category_filter = body.get("category_filter", [])
        top_k = min(body.get("top_k", 5), 20)
        namespace = body.get("namespace", None)
        valid_days = body.get("valid_days", 30)
        sim_threshold = body.get("sim_threshold", 0.78)
        use_vector = body.get("use_vector", True)

        results = []

        # 向量语义搜索：嵌入模型就绪时用向量召回
        if use_vector and query_text:
            llm = getattr(storage, '_llm', None)
            if llm and llm.has_embed_model:
                query_vector = llm.embed(query_text)
                if query_vector:
                    vector_results = storage.vector.search(
                        query_vector=query_vector,
                        top_k=top_k * 2,
                        threshold=sim_threshold,
                        label=category_filter[0] if category_filter else None,
                        tier=None,
                    )

                    doc_ids = [r["doc_id"] for r in vector_results]
                    if doc_ids:
                        sql_results = storage.sqlite.get_memories_by_doc_ids(doc_ids)
                        sql_map = {r["doc_id"]: r for r in sql_results}

                        for vr in vector_results:
                            doc_id = vr["doc_id"]
                            if doc_id in sql_map:
                                merged = dict(sql_map[doc_id])
                                merged["similarity"] = vr.get("similarity", 0)
                                results.append(merged)

        if not results:
            results = storage.sqlite.search_memory(
                keyword=query_text,
                label=category_filter[0] if category_filter else None,
                namespace=namespace,
                limit=top_k,
            )

        if results:
            max_w = max((r.get("weight", 50) for r in results), default=1) or 1
            for r in results:
                sim = r.get("similarity", 0)
                w = r.get("weight", 50) / max_w
                r["_score"] = sim * 0.7 + w * 0.3
            results.sort(key=lambda r: r.get("_score", 0), reverse=True)

        results = results[:top_k]

        for r in results:
            storage.sqlite.record_access(r.get("doc_id", 0), "api")

        cleaned = [{"content": r.get("compact_content", ""), "weight": r.get("weight", 50), "importance": r.get("importance", "P2")} for r in results]

        # 关键词匹配的全局规则 top-3，插到结果前列
        if query_text:
            rules = storage.sqlite.search_global_rules_with_tracking(query_text, limit=3)
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

    @app.post("/api/memory/add")
    def add_memory(
        body: dict = Body(...),
        token: Optional[str] = Header(None),
    ):
        _verify_token(token)
        content = body.get("content", "")
        layer2_category = body.get("layer2_category", "unknown")
        namespace = body.get("namespace", "default")
        source = body.get("source", "manual")

        if not content:
            raise HTTPException(status_code=422, detail="content is required")

        doc_id = storage.sqlite.upsert_document(
            file_path=f"_api_add_{int(time.time()*1000)}.md",
            file_hash=str(hash(content)),
            file_size=len(content),
            create_time=body.get("create_ts", ""),
            modify_time=body.get("create_ts", ""),
            origin_source=source,
            raw_text_snippet=content[:500],
        )

        from ..pipeline.pipeline import ClassifyPipeline
        pipeline = ClassifyPipeline(llm, storage)
        pipeline.process_one(doc_id, content, fast_lane=True)

        return {"code": 0, "msg": "success", "data": {"doc_id": doc_id}}

    @app.post("/api/memory/add_batch")
    def add_batch(
        body: dict = Body(...),
        token: Optional[str] = Header(None),
    ):
        _verify_token(token)
        memories = body.get("memories", [])
        if not memories:
            raise HTTPException(status_code=422, detail="memories list is required")

        added = []
        for m in memories:
            content = m.get("content", "")
            if not content:
                continue
            layer2 = m.get("layer2_category", "unknown")
            ns = m.get("namespace", "default")
            src = m.get("source", "manual")

            doc_id = storage.sqlite.upsert_document(
                file_path=f"_api_batch_{int(time.time()*1000)}_{len(added)}.md",
                file_hash=str(hash(content)),
                file_size=len(content),
                create_time=m.get("create_ts", ""),
                modify_time=m.get("create_ts", ""),
                origin_source=src,
                raw_text_snippet=content[:500],
            )
            try:
                label = DocumentLabel(layer2)
            except ValueError:
                label = DocumentLabel.UNKNOWN
            tier = LABEL_TO_TIER.get(label, MemoryTier.SHORT)
            storage.sqlite.set_classification(
                doc_id, label, tier, namespace=ns, compact_content=content,
            )
            added.append(doc_id)

        return {"code": 0, "msg": "success", "data": {"added": len(added), "doc_ids": added}}

    @app.post("/api/memory/archive")
    def archive_memory(
        body: dict = Body(...),
        token: Optional[str] = Header(None),
    ):
        _verify_token(token)
        doc_id = body.get("doc_id")
        if not doc_id:
            raise HTTPException(status_code=422, detail="doc_id is required")
        storage.sqlite.update_classification(doc_id, "compact_archive", "archive", 90)
        return {"code": 0, "msg": "success", "data": {"tier": "archive", "new_weight": 90}}

    @app.post("/api/memory/archive_batch")
    def archive_batch(
        body: dict = Body(...),
        token: Optional[str] = Header(None),
    ):
        _verify_token(token)
        doc_ids = body.get("doc_ids", [])
        if not doc_ids:
            raise HTTPException(status_code=422, detail="doc_ids list is required")
        for did in doc_ids:
            storage.sqlite.update_classification(did, "compact_archive", "archive", 90)
        return {"code": 0, "msg": "success", "data": {"archived": len(doc_ids)}}

    @app.post("/api/memory/recover")
    def recover_memory(
        body: dict = Body(...),
        token: Optional[str] = Header(None),
    ):
        _verify_token(token)
        doc_id = body.get("doc_id")
        if not doc_id:
            raise HTTPException(status_code=422, detail="doc_id is required")
        storage.sqlite.recover(doc_id)
        return {"code": 0, "msg": "success"}

    @app.get("/api/memory/short")
    def get_short_memory(
        keyword: str = Query(""),
        limit: int = Query(10),
        token: Optional[str] = Header(None),
    ):
        _verify_token(token)
        results = storage.sqlite.search_memory(keyword=keyword, tier="short", limit=limit)
        results = _merge_rules_into_results(keyword, results)
        return {"code": 0, "msg": "success", "data": {"list": results}}

    @app.get("/api/memory/long")
    def get_long_memory(
        keyword: str = Query(""),
        limit: int = Query(20),
        token: Optional[str] = Header(None),
    ):
        _verify_token(token)
        results = storage.sqlite.search_memory(keyword=keyword, tier="long", limit=limit)
        results = _merge_rules_into_results(keyword, results)
        return {"code": 0, "msg": "success", "data": {"list": results}}

    @app.get("/api/memory/planning")
    def get_planning(
        keyword: str = Query(""),
        limit: int = Query(15),
        token: Optional[str] = Header(None),
    ):
        _verify_token(token)
        results = storage.sqlite.search_memory(keyword=keyword, label="planning_doc", limit=limit)
        results = _merge_rules_into_results(keyword, results)
        return {"code": 0, "msg": "success", "data": {"list": results}}

    @app.get("/api/memory/selfimprove")
    def get_selfimprove(
        keyword: str = Query(""),
        limit: int = Query(15),
        token: Optional[str] = Header(None),
    ):
        _verify_token(token)
        results = storage.sqlite.search_memory(keyword=keyword, label="self_improve_learn", limit=limit)
        results = _merge_rules_into_results(keyword, results)
        return {"code": 0, "msg": "success", "data": {"list": results}}

    @app.get("/api/memory/archive")
    def get_archive(
        keyword: str = Query(""),
        limit: int = Query(15),
        token: Optional[str] = Header(None),
    ):
        _verify_token(token)
        results = storage.sqlite.search_memory(keyword=keyword, label="compact_archive", limit=limit)
        results = _merge_rules_into_results(keyword, results)
        return {"code": 0, "msg": "success", "data": {"list": results}}

    @app.get("/api/files/unknown")
    def get_unknown_files(token: Optional[str] = Header(None)):
        _verify_token(token)
        results = storage.sqlite.get_unknown_docs()
        return {"code": 0, "msg": "success", "data": {"list": results}}

    @app.get("/api/files/all")
    def get_all_files(
        limit: int = Query(50),
        offset: int = Query(0),
        token: Optional[str] = Header(None),
    ):
        _verify_token(token)
        rows = storage.sqlite.search_memory(limit=limit)
        total = storage.sqlite.total_documents()
        return {"code": 0, "msg": "success", "data": {"list": rows, "total": total}}

    @app.put("/api/files/classify")
    def update_classify(
        doc_id: int = Query(...),
        label: str = Query(...),
        token: Optional[str] = Header(None),
    ):
        _verify_token(token)
        try:
            lbl = DocumentLabel(label)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid label: {label}")
        tier = LABEL_TO_TIER.get(lbl, MemoryTier.SHORT)
        storage.sqlite.update_classification(doc_id, label, tier.value)
        return {"code": 0, "msg": "success"}

    @app.get("/api/memory/health")
    def health_check(token: Optional[str] = Header(None)):
        _verify_token(token)
        return {
            "code": 0,
            "msg": "success",
            "data": {
                "total_documents": storage.sqlite.total_documents(),
                "label_counts": storage.sqlite.count_by_label(),
                "namespace_counts": storage.sqlite.count_by_namespace(),
                "importance_counts": storage.sqlite.count_by_importance(),
                "vector_count": storage.vector.count(),
                "global_rules": storage.sqlite.count_rules_by_priority(),
            },
        }

    @app.get("/api/memory/debug")
    def debug_info(token: Optional[str] = Header(None)):
        _verify_token(token)
        return {
            "code": 0,
            "msg": "success",
            "data": {
                "total_documents": storage.sqlite.total_documents(),
                "label_counts": storage.sqlite.count_by_label(),
                "namespace_counts": storage.sqlite.count_by_namespace(),
                "importance_counts": storage.sqlite.count_by_importance(),
                "vector_count": storage.vector.count(),
                "global_rules": storage.sqlite.count_rules_by_priority(),
                "disk_space": storage.check_disk_space(),
            },
        }

    @app.post("/api/model/reload")
    def reload_model(
        body: dict = Body(...),
        token: Optional[str] = Header(None),
    ):
        _verify_token(token)
        model = body.get("model", "all")
        return {
            "code": 0,
            "msg": "success",
            "data": {
                "message": f"Model reload requested: {model}",
                "note": "Reload requires app restart or LLM manager reload",
            },
        }

    @app.get("/api/classification/categories")
    def get_categories(token: Optional[str] = Header(None)):
        _verify_token(token)
        from ..classifier import DynamicClassifier
        classifier = DynamicClassifier()
        return {
            "code": 0,
            "msg": "success",
            "data": {"categories": classifier.get_all_categories()},
        }

    @app.post("/api/classification/add")
    def add_category(
        body: dict = Body(...),
        token: Optional[str] = Header(None),
    ):
        _verify_token(token)
        name = body.get("name", "")
        parent = body.get("parent", "")
        keywords = body.get("keywords", [])
        
        if not name:
            raise HTTPException(status_code=422, detail="name is required")
        
        from ..classifier import DynamicClassifier
        classifier = DynamicClassifier()
        full_name = classifier.add_category(name, parent, keywords)
        
        return {
            "code": 0,
            "msg": "success",
            "data": {"category": full_name},
        }

    @app.post("/api/classification/learn")
    def learn_from_feedback(
        body: dict = Body(...),
        token: Optional[str] = Header(None),
    ):
        _verify_token(token)
        doc_id = body.get("doc_id")
        category = body.get("category", "")
        
        if not doc_id or not category:
            raise HTTPException(status_code=422, detail="doc_id and category required")
        
        row = storage.sqlite.get_document_by_path(
            storage.sqlite._conn.execute(
                "SELECT file_path FROM document_files WHERE id=?", (doc_id,)
            ).fetchone()
        ) if doc_id else None
        
        content = ""
        filepath = ""
        if doc_id:
            r = storage.sqlite._conn.execute(
                "SELECT file_path, raw_text_snippet FROM document_files WHERE id=?", (doc_id,)
            ).fetchone()
            if r:
                filepath = r["file_path"]
                content = r["raw_text_snippet"] or ""
        
        from ..classifier import DynamicClassifier
        classifier = DynamicClassifier()
        classifier.learn_from_feedback(category, content, filepath)
        
        storage.sqlite._conn.execute(
            "UPDATE memory_classify SET content_category=? WHERE doc_id=?",
            (category, doc_id)
        )
        storage.sqlite._conn.commit()
        
        return {
            "code": 0,
            "msg": "success",
            "data": {"learned": True},
        }

    @app.post("/api/classification/suggest")
    def suggest_category(
        body: dict = Body(...),
        token: Optional[str] = Header(None),
    ):
        _verify_token(token)
        content = body.get("content", "")
        
        from ..classifier import DynamicClassifier
        classifier = DynamicClassifier()
        suggestions = classifier.suggest_category(content)
        
        return {
            "code": 0,
            "msg": "success",
            "data": {"suggestions": suggestions},
        }

    @app.post("/api/classification/extract-keywords")
    def extract_keywords(token: Optional[str] = Header(None)):
        _verify_token(token)
        
        from ..classifier import DynamicClassifier
        classifier = DynamicClassifier()
        count = classifier.auto_extract_keywords(storage.sqlite._conn)
        
        return {
            "code": 0,
            "msg": "success",
            "data": {"categories_updated": count},
        }

    # ========== 知识领域管理 ==========

    @app.get("/api/domains")
    def list_domains(
        namespace: str = Query("default"),
        token: Optional[str] = Header(None),
    ):
        _verify_token(token)
        domains = storage.sqlite.list_domains(namespace)
        return {"code": 0, "msg": "success", "data": {"domains": domains}}

    @app.post("/api/domains/rename")
    def rename_domain(
        body: dict = Body(...),
        token: Optional[str] = Header(None),
    ):
        _verify_token(token)
        old = body.get("old_name", "")
        new = body.get("new_name", "")
        namespace = body.get("namespace", "default")
        if not old or not new:
            raise HTTPException(status_code=422, detail="old_name and new_name required")
        count = storage.sqlite.rename_domain(old, new, namespace)
        return {"code": 0, "msg": "success", "data": {"updated_docs": count}}

    @app.delete("/api/domains")
    def delete_domain(
        name: str = Query(...),
        namespace: str = Query("default"),
        token: Optional[str] = Header(None),
    ):
        _verify_token(token)
        storage.sqlite.delete_domain(name, namespace)
        return {"code": 0, "msg": "success", "data": {"deleted": True}}

    @app.get("/api/domains/with-docs")
    def domains_with_docs(
        namespace: str = Query("default"),
        token: Optional[str] = Header(None),
    ):
        _verify_token(token)
        domains = storage.sqlite.get_domains_with_docs(namespace)
        return {"code": 0, "msg": "success", "data": {"domains": domains}}

    # ========== 全局规则管理 ==========

    @app.get("/api/rules")
    def list_rules(
        scope: str = Query(""),
        category: str = Query(""),
        priority: str = Query(""),
        limit: int = Query(100),
        token: Optional[str] = Header(None),
    ):
        _verify_token(token)
        rules = storage.sqlite.get_global_rules(
            scope=scope or None,
            category=category or None,
            priority=priority or None,
            limit=limit,
        )
        return {"code": 0, "msg": "success", "data": {"list": rules}}

    @app.post("/api/rules/search")
    def search_rules(
        body: dict = Body(...),
        token: Optional[str] = Header(None),
    ):
        _verify_token(token)
        keyword = body.get("keyword", "")
        limit = body.get("limit", 20)
        if not keyword:
            raise HTTPException(status_code=422, detail="keyword is required")
        rules = storage.sqlite.search_global_rules(keyword, limit)
        return {"code": 0, "msg": "success", "data": {"list": rules}}

    @app.delete("/api/rules")
    def delete_rule(
        rule_id: int = Query(...),
        token: Optional[str] = Header(None),
    ):
        _verify_token(token)
        ok = storage.sqlite.delete_global_rule(rule_id)
        return {"code": 0 if ok else 404, "msg": "success" if ok else "not found"}

    @app.post("/api/rules/deactivate")
    def deactivate_rule(
        body: dict = Body(...),
        token: Optional[str] = Header(None),
    ):
        _verify_token(token)
        rule_id = body.get("rule_id")
        if not rule_id:
            raise HTTPException(status_code=422, detail="rule_id is required")
        ok = storage.sqlite.deactivate_global_rule(rule_id)
        return {"code": 0 if ok else 404, "msg": "success" if ok else "not found"}

    @app.post("/api/rules/scenario")
    def rules_by_scenario(
        body: dict = Body(...),
        token: Optional[str] = Header(None),
    ):
        """按场景关键词查询规则，支持多关键词组合过滤"""
        _verify_token(token)
        keywords = body.get("keywords", [])
        limit = body.get("limit", 20)
        if not keywords:
            raise HTTPException(status_code=422, detail="keywords is required")
        rules = storage.sqlite.get_global_rules_by_scenario(keywords, limit)
        return {"code": 0, "msg": "success", "data": {"list": rules}}

    @app.get("/api/rules/type")
    def rules_by_type(
        rule_type: str = Query(...),
        min_weight: int = Query(0),
        limit: int = Query(100),
        token: Optional[str] = Header(None),
    ):
        """按规则类型查询（meta/domain/standard/knowledge/index）"""
        _verify_token(token)
        rules = storage.sqlite.get_global_rules_by_type(rule_type, limit, min_weight)
        return {"code": 0, "msg": "success", "data": {"list": rules}}

    @app.get("/api/rules/{rule_id}/children")
    def rule_children(
        rule_id: int,
        token: Optional[str] = Header(None),
    ):
        """获取父规则下的子规则"""
        _verify_token(token)
        rules = storage.sqlite.get_child_rules(rule_id)
        return {"code": 0, "msg": "success", "data": {"list": rules}}

    @app.post("/api/rules/add")
    def add_rule_manual(
        body: dict = Body(...),
        token: Optional[str] = Header(None),
    ):
        """手动添加规则，自动过门禁安检"""
        _verify_token(token)
        rule_text = body.get("rule_text", "")
        if not rule_text:
            raise HTTPException(status_code=422, detail="rule_text is required")

        rule_id = storage.sqlite.add_global_rule(
            rule_text=rule_text,
            category=body.get("category", "rule"),
            sub_category=body.get("sub_category", "behavior"),
            scope=body.get("scope", "global"),
            priority=body.get("priority", "normal"),
            ttl=body.get("ttl", "M"),
            tags=json.dumps(body.get("tags", [])),
            rule_type=body.get("rule_type", "knowledge"),
            score_universality=body.get("score_universality", 3),
            score_cost=body.get("score_cost", 2),
            score_actionable=body.get("score_actionable", 3),
            score_timeliness=body.get("score_timeliness", 3),
            parent_rule_id=body.get("parent_rule_id"),
        )

        if rule_id == -2:
            raise HTTPException(status_code=422, detail="规则未通过门禁，已被拦截")
        return {"code": 0, "msg": "success", "data": {"rule_id": rule_id}}

    # ========== 控制面板 API ==========

    @app.get("/api/control/status")
    def control_status(token: Optional[str] = Header(None)):
        _verify_token(token)
        doc_count = storage.sqlite.total_documents() if storage.sqlite else 0
        classify_q = getattr(getattr(storage, '_ctx', None), 'scheduler', None)
        qsize = classify_q.classify_queue_size if classify_q else 0
        from ..core.config import load_config as lc
        cfg = lc()
        return {
            "code": 0, "msg": "success",
            "data": {
                "llm_status": "keyword_only",  # V10: 关键词分类模式
                "embed_model": "ready" if (llm and llm.has_embed_model) else "unavailable",
                "provider": "none",
                "model": "",
                "api_base_url": "",
                "scan_paths": list(cfg.scan.custom_white_path),
                "agent_paths": list(getattr(cfg.scan, 'agent_paths', [])),
                "doc_count": doc_count,
                "classify_queue": qsize,
                "classify_progress": classify_q.get_progress() if classify_q else {},
                "scan_progress": getattr(getattr(storage, '_ctx', None), 'scan_progress', {}),
            },
        }

    @app.get("/api/control/snapshots")
    def control_snapshots(token: Optional[str] = Header(None)):
        _verify_token(token)
        snap_dir = config.storage.snapshot_dir
        files = sorted(Path(snap_dir).glob("*.zip"), reverse=True)
        return {"code": 0, "data": {"list": [s.name for s in files]}}

    @app.post("/api/control/scan/full")
    def control_full_scan(token: Optional[str] = Header(None)):
        _verify_token(token)
        ctx = getattr(storage, '_ctx', None)
        if ctx and hasattr(ctx, 'safe_full_scan'):
            threading.Thread(target=ctx.safe_full_scan, daemon=True).start()
            return {"code": 0, "msg": "全盘扫描已启动"}
        return {"code": 500, "msg": "context not available"}

    @app.post("/api/control/scan/incremental")
    def control_incremental_scan(token: Optional[str] = Header(None)):
        _verify_token(token)
        ctx = getattr(storage, '_ctx', None)
        if ctx and hasattr(ctx, 'scanner') and ctx.scanner:
            def _run():
                count, pending = ctx.scanner.full_scan()
                if pending:
                    ctx._process_pending_llm(pending)
                ctx._export_memories()
            threading.Thread(target=_run, daemon=True).start()
            return {"code": 0, "msg": "增量扫描已启动"}
        return {"code": 500, "msg": "scanner not available"}

    @app.post("/api/control/optimize")
    def control_optimize(token: Optional[str] = Header(None)):
        _verify_token(token)
        ctx = getattr(storage, '_ctx', None)
        if ctx and ctx.optimizer:
            def _run():
                result = ctx.optimizer.run_once()
                try:
                    if tray:
                        if "error" in result:
                            tray.show_toast("整理失败", str(result["error"]))
                        else:
                            tray.show_toast("整理完成", f"衰减{result.get('decayed',0)}条")
                except Exception:
                    pass
            threading.Thread(target=_run, daemon=True).start()
            return {"code": 0, "data": {"message": "整理已开始"}}
        return {"code": 500, "msg": "optimizer not available"}

    @app.post("/api/control/scan-path/add")
    def control_add_path(body: dict = Body(...), token: Optional[str] = Header(None)):
        _verify_token(token)
        path = body.get("path", "")
        if path and path not in config.scan.custom_white_path:
            config.scan.custom_white_path.append(path)
        return {"code": 0, "msg": "ok"}

    @app.post("/api/control/scan-path/remove")
    def control_remove_path(body: dict = Body(...), token: Optional[str] = Header(None)):
        _verify_token(token)
        path = body.get("path", "")
        if path in config.scan.custom_white_path:
            config.scan.custom_white_path.remove(path)
        return {"code": 0, "msg": "ok"}

    @app.post("/api/control/snapshot/restore")
    def control_restore_snapshot(body: dict = Body(...), token: Optional[str] = Header(None)):
        _verify_token(token)
        snap_name = body.get("snapshot", "")
        if not snap_name:
            raise HTTPException(status_code=422, detail="snapshot name required")
        ctx = getattr(storage, '_ctx', None)
        if ctx and hasattr(ctx, 'restore_from_snapshot'):
            ok = ctx.restore_from_snapshot(snap_name)
            return {"code": 0 if ok else 500, "msg": "ok" if ok else "恢复失败"}
        return {"code": 500, "msg": "restore not available"}

    @app.post("/api/control/open-logs")
    def control_open_logs(token: Optional[str] = Header(None)):
        _verify_token(token)
        import subprocess, os
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        subprocess.Popen(["explorer", str(log_dir.resolve())])
        return {"code": 0, "msg": "ok"}

    @app.post("/api/control/restart")
    def control_restart(token: Optional[str] = Header(None)):
        _verify_token(token)
        ctx = getattr(storage, '_ctx', None)
        if ctx and hasattr(ctx, 'restart'):
            threading.Thread(target=ctx.restart, daemon=True).start()
        return {"code": 0, "msg": "restarting"}

    @app.post("/api/control/exit")
    def control_exit(token: Optional[str] = Header(None)):
        _verify_token(token)
        ctx = getattr(storage, '_ctx', None)
        if ctx and hasattr(ctx, 'shutdown'):
            threading.Thread(target=ctx.shutdown, daemon=True).start()
        return {"code": 0, "msg": "exiting"}

    return app


class _RateLimiter:
    def __init__(self, max_per_sec: int):
        self.max_per_sec = max_per_sec
        self._timestamps: list[float] = []
        self._lock = threading.Lock()

    def allow(self) -> bool:
        now = time.time()
        with self._lock:
            self._timestamps = [t for t in self._timestamps if now - t < 1.0]
            if len(self._timestamps) >= self.max_per_sec:
                return False
            self._timestamps.append(now)
            return True
