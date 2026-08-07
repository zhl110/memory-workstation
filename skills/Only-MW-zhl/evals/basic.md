# Evals: Only-MW-zhl

## Eval 1: Basic ingest
**Input:** `/mw-ingest 打包前必须运行 python -c 验证语法`
**Expected:** Agent calls `mw ingest`, runs correction check, updates `memory_index_agents.md`
**Pass criteria:** Doc appears in `m.search("打包验证")` results after ingest

## Eval 2: Cross-query with pool auto-fallback
**Input:** `/mw-query 部署配置`
**Expected:**
1. Agent initializes `MemoryClient` (auto-connects to library pool)
2. Calls `m.search(query, top_k=10)` — first searches own DB
3. If own results < `top_k`, auto-falls back to library pool（LIKE + Entity 匹配）
4. SDK 做 RRF 融合排序，两路结果按 score 合并
5. Shows results with cross_ref expansion
**Pass criteria:** Results include memories from both own DB and library pool

## Eval 3: Correction detection P8
**Input:** User corrects Agent 3 times about "prefer_tabs_over_spaces"
**Expected:** After 3rd correction, `m.get_correction_pending()` returns record, Agent asks user to confirm

## Eval 4: Evolution tier change
**Input:** `mw evolve` shows hot candidate, user confirms
**Expected:** `m.apply_tier_change()` + `m.log_event("tier_change", ...)` executed

## Eval 5: Evolution log query
**Input:** `mw log --type correction`
**Expected:** Agent calls `m.list_corrections()`, displays formatted table

## Eval 6: Check deps script
**Input:** `python scripts/check-deps.py`
**Expected:** Reports SDK install status + DB file existence. Exit code 0 when all OK.

## Eval 7: Verify install script
**Input:** `python scripts/verify-install.py`
**Expected:** Runs all 5 test steps (import, search, rules, entities, stats). Exit code 0 when all pass.

## Eval 8: Lint — orphan page detection
**Input:** `mw health`
**Expected:** Agent detects doc_ids with no cross_ref entries, reports them
**Pass criteria:** List of orphan doc_ids is non-empty if any exist, otherwise reports "0 isolated pages"

## Eval 9: Export + re-import
**Input:** `mw export` then `mw list`
**Expected:** Exported MD files count matches `m.get_stats()["total_memories"]`
**Pass criteria:** Export count is non-zero and matches the memory_classify count

## Eval 10: Init idempotency
**Input:** Call `m.init_schema()` twice on existing DB
**Expected:** Second call does NOT crash — ALTER TABLE is gated by PRAGMA column check
**Pass criteria:** Call in a loop 3 times, all pass without exception
