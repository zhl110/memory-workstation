#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <string>
#include "mw_core.h"
#include "search_engine.h"
#include "graph_engine.h"
#include "rules.h"
#include "hnsw_index.h"

namespace py = pybind11;

// ═══════════════════════════════════════════════════════════════════
// 类型绑定 — 所有数据结构定义
// ═══════════════════════════════════════════════════════════════════

void bind_types(py::module_& m) {
    // SearchResult
    py::class_<mw::SearchResult>(m, "SearchResult")
        .def_readonly("doc_id", &mw::SearchResult::doc_id)
        .def_readonly("score", &mw::SearchResult::score)
        .def_readonly("summary", &mw::SearchResult::summary)
        .def_readonly("category", &mw::SearchResult::category)
        .def_readonly("importance", &mw::SearchResult::importance)
        .def_readonly("weight", &mw::SearchResult::weight)
        .def_readonly("scope", &mw::SearchResult::scope)
        .def_readonly("project", &mw::SearchResult::project)
        .def_readonly("signals", &mw::SearchResult::signals)
        .def("__repr__", [](const mw::SearchResult& r) {
            return "<SearchResult doc_id=" + std::to_string(r.doc_id) +
                   " score=" + std::to_string(r.score) + ">";
        });

    // MemoryRecord
    py::class_<mw::MemoryRecord>(m, "MemoryRecord")
        .def_readonly("doc_id", &mw::MemoryRecord::doc_id)
        .def_readonly("label", &mw::MemoryRecord::label)
        .def_readonly("importance", &mw::MemoryRecord::importance)
        .def_readonly("weight", &mw::MemoryRecord::weight)
        .def_readonly("summary", &mw::MemoryRecord::summary)
        .def_readonly("category", &mw::MemoryRecord::category)
        .def_readonly("sub_category", &mw::MemoryRecord::sub_category)
        .def_readonly("content", &mw::MemoryRecord::content)
        .def_readonly("scope", &mw::MemoryRecord::scope)
        .def_readonly("project", &mw::MemoryRecord::project)
        .def("__repr__", [](const mw::MemoryRecord& r) {
            return "<MemoryRecord doc_id=" + std::to_string(r.doc_id) +
                   " label=" + r.label + ">";
        });

    // BatchIngestResult
    py::class_<mw::Storage::BatchIngestResult>(m, "BatchIngestResult")
        .def_readonly("doc_id", &mw::Storage::BatchIngestResult::doc_id)
        .def_readonly("entities_inserted", &mw::Storage::BatchIngestResult::entities_inserted)
        .def_readonly("cross_refs_inserted", &mw::Storage::BatchIngestResult::cross_refs_inserted)
        .def("__repr__", [](const mw::Storage::BatchIngestResult& r) {
            return "<BatchIngestResult doc_id=" + std::to_string(r.doc_id) +
                   " entities=" + std::to_string(r.entities_inserted) +
                   " cross_refs=" + std::to_string(r.cross_refs_inserted) + ">";
        });

    // Rule
    py::class_<mw::Rule>(m, "Rule")
        .def_readonly("id", &mw::Rule::id)
        .def_readonly("rule_text", &mw::Rule::rule_text)
        .def_readonly("category", &mw::Rule::category)
        .def_readonly("sub_category", &mw::Rule::sub_category)
        .def_readonly("priority", &mw::Rule::priority)
        .def_readonly("confidence", &mw::Rule::confidence)
        .def_readonly("conflict_with", &mw::Rule::conflict_with)
        .def_readonly("complements", &mw::Rule::complements);

    // Entity
    py::class_<mw::Entity>(m, "Entity")
        .def_readonly("doc_id", &mw::Entity::doc_id)
        .def_readonly("entity_name", &mw::Entity::entity_name)
        .def_readonly("entity_type", &mw::Entity::entity_type)
        .def_readonly("weight", &mw::Entity::weight);

    // CrossRefCandidate
    py::class_<mw::CrossRefCandidate>(m, "CrossRefCandidate")
        .def_readonly("doc_id", &mw::CrossRefCandidate::doc_id)
        .def_readonly("summary", &mw::CrossRefCandidate::summary)
        .def_readonly("score", &mw::CrossRefCandidate::score);

    // MentionHit
    py::class_<mw::MentionHit>(m, "MentionHit")
        .def_readonly("related_doc_id", &mw::MentionHit::related_doc_id)
        .def_readonly("entity_name", &mw::MentionHit::entity_name)
        .def_readonly("mention_count", &mw::MentionHit::mention_count)
        .def("__repr__", [](const mw::MentionHit& h) {
            return "<MentionHit doc_id=" + std::to_string(h.related_doc_id) +
                   " entity=" + h.entity_name +
                   " count=" + std::to_string(h.mention_count) + ">";
        });

    // LinkedResult
    py::class_<mw::LinkedResult>(m, "LinkedResult")
        .def_readonly("doc_id", &mw::LinkedResult::doc_id)
        .def_readonly("relation_type", &mw::LinkedResult::relation_type)
        .def_readonly("note", &mw::LinkedResult::note)
        .def_readonly("weight", &mw::LinkedResult::weight)
        .def("__repr__", [](const mw::LinkedResult& r) {
            return "<LinkedResult doc_id=" + std::to_string(r.doc_id) +
                   " relation_type=" + r.relation_type +
                   " weight=" + std::to_string(r.weight) + ">";
        });

    // CorrectionRecord
    py::class_<mw::CorrectionRecord>(m, "CorrectionRecord")
        .def_readonly("id", &mw::CorrectionRecord::id)
        .def_readonly("pattern", &mw::CorrectionRecord::pattern)
        .def_readonly("summary", &mw::CorrectionRecord::summary)
        .def_readonly("context", &mw::CorrectionRecord::context)
        .def_readonly("count", &mw::CorrectionRecord::count)
        .def_readonly("promoted", &mw::CorrectionRecord::promoted)
        .def_readonly("suppressed_at", &mw::CorrectionRecord::suppressed_at)
        .def_readonly("occurred_at", &mw::CorrectionRecord::occurred_at)
        .def_readonly("last_occurred_at", &mw::CorrectionRecord::last_occurred_at);

    // EvolutionLogEntry
    py::class_<mw::EvolutionLogEntry>(m, "EvolutionLogEntry")
        .def_readonly("id", &mw::EvolutionLogEntry::id)
        .def_readonly("event_type", &mw::EvolutionLogEntry::event_type)
        .def_readonly("trigger_name", &mw::EvolutionLogEntry::trigger_name)
        .def_readonly("target_doc_id", &mw::EvolutionLogEntry::target_doc_id)
        .def_readonly("detail", &mw::EvolutionLogEntry::detail)
        .def_readonly("certainty", &mw::EvolutionLogEntry::certainty)
        .def_readonly("created_at", &mw::EvolutionLogEntry::created_at);

    // TierHistoryEntry
    py::class_<mw::TierHistoryEntry>(m, "TierHistoryEntry")
        .def_readonly("id", &mw::TierHistoryEntry::id)
        .def_readonly("doc_id", &mw::TierHistoryEntry::doc_id)
        .def_readonly("from_tier", &mw::TierHistoryEntry::from_tier)
        .def_readonly("to_tier", &mw::TierHistoryEntry::to_tier)
        .def_readonly("reason", &mw::TierHistoryEntry::reason)
        .def_readonly("applied_at", &mw::TierHistoryEntry::applied_at)
        .def_readonly("summary", &mw::TierHistoryEntry::summary);

    // CandidateRecord
    py::class_<mw::CandidateRecord>(m, "CandidateRecord")
        .def_readonly("doc_id", &mw::CandidateRecord::doc_id)
        .def_readonly("summary", &mw::CandidateRecord::summary)
        .def_readonly("label", &mw::CandidateRecord::label)
        .def_readonly("importance", &mw::CandidateRecord::importance)
        .def_readonly("weight", &mw::CandidateRecord::weight)
        .def_readonly("evolution_tier", &mw::CandidateRecord::evolution_tier);

    // HealthCheckResult
    py::class_<mw::HealthCheckResult>(m, "HealthCheckResult")
        .def_readonly("db_ok", &mw::HealthCheckResult::db_ok)
        .def_readonly("fts5_entries", &mw::HealthCheckResult::fts5_entries)
        .def_readonly("fts5_behind", &mw::HealthCheckResult::fts5_behind)
        .def_readonly("hnsw_ready", &mw::HealthCheckResult::hnsw_ready)
        .def_readonly("graph_nodes", &mw::HealthCheckResult::graph_nodes)
        .def_readonly("graph_edges", &mw::HealthCheckResult::graph_edges)
        .def_readonly("graph_orphan_rate", &mw::HealthCheckResult::graph_orphan_rate)
        .def_readonly("error", &mw::HealthCheckResult::error);

    // GraphEdge
    py::class_<mw::GraphEdge>(m, "GraphEdge")
        .def_readonly("target", &mw::GraphEdge::target)
        .def_readonly("relation_type", &mw::GraphEdge::relation_type)
        .def_readonly("weight", &mw::GraphEdge::weight);

    // TraverseResult
    py::class_<mw::TraverseResult>(m, "TraverseResult")
        .def_readonly("doc_id", &mw::TraverseResult::doc_id)
        .def_readonly("hop", &mw::TraverseResult::hop)
        .def_readonly("relation_type", &mw::TraverseResult::relation_type)
        .def_readonly("path", &mw::TraverseResult::path);

    // GraphStats
    py::class_<mw::GraphStats>(m, "GraphStats")
        .def_readonly("total_nodes", &mw::GraphStats::total_nodes)
        .def_readonly("total_edges", &mw::GraphStats::total_edges)
        .def_readonly("avg_degree", &mw::GraphStats::avg_degree)
        .def_readonly("orphan_count", &mw::GraphStats::orphan_count)
        .def_readonly("orphan_rate", &mw::GraphStats::orphan_rate)
        .def_readonly("edge_type_distribution", &mw::GraphStats::edge_type_distribution);

    // SceneRecord
    py::class_<mw::SceneRecord>(m, "SceneRecord")
        .def(py::init<>())
        .def_readwrite("scene_id", &mw::SceneRecord::scene_id)
        .def_readwrite("name", &mw::SceneRecord::name)
        .def_readwrite("parent_scene", &mw::SceneRecord::parent_scene)
        .def_readwrite("description", &mw::SceneRecord::description)
        .def_readonly("create_time", &mw::SceneRecord::create_time);

    // SceneRuleRecord
    py::class_<mw::SceneRuleRecord>(m, "SceneRuleRecord")
        .def(py::init<>())
        .def_readwrite("rule_id", &mw::SceneRuleRecord::rule_id)
        .def_readwrite("scene_id", &mw::SceneRuleRecord::scene_id)
        .def_readwrite("rule_type", &mw::SceneRuleRecord::rule_type)
        .def_readwrite("rule_text", &mw::SceneRuleRecord::rule_text)
        .def_readwrite("priority", &mw::SceneRuleRecord::priority)
        .def_readonly("create_time", &mw::SceneRuleRecord::create_time);

    // EmotionRecord
    py::class_<mw::EmotionRecord>(m, "EmotionRecord")
        .def(py::init<>())
        .def_readonly("emotion_id", &mw::EmotionRecord::emotion_id)
        .def_readonly("doc_id", &mw::EmotionRecord::doc_id)
        .def_readonly("emotion_type", &mw::EmotionRecord::emotion_type)
        .def_readonly("emotion_detail", &mw::EmotionRecord::emotion_detail)
        .def_readonly("intensity", &mw::EmotionRecord::intensity)
        .def_readonly("create_time", &mw::EmotionRecord::create_time);

    // SessionStateRecord
    py::class_<mw::SessionStateRecord>(m, "SessionStateRecord")
        .def(py::init<>())
        .def_readwrite("state_id", &mw::SessionStateRecord::state_id)
        .def_readwrite("agent_name", &mw::SessionStateRecord::agent_name)
        .def_readwrite("session_id", &mw::SessionStateRecord::session_id)
        .def_readwrite("last_topic", &mw::SessionStateRecord::last_topic)
        .def_readwrite("unfinished_tasks", &mw::SessionStateRecord::unfinished_tasks)
        .def_readwrite("emotion_state", &mw::SessionStateRecord::emotion_state)
        .def_readonly("update_time", &mw::SessionStateRecord::update_time);
}

// ═══════════════════════════════════════════════════════════════════
// Storage 绑定 — 核心 I/O 和 CRUD
// ═══════════════════════════════════════════════════════════════════

void bind_storage(py::module_& m) {
    py::class_<mw::Storage>(m, "Storage")
        .def(py::init<const std::string&>(), py::arg("db_path"))
        .def("close", [](mw::Storage& s) {
            py::gil_scoped_release release;
            s.close();
        })
        .def("is_open", [](const mw::Storage& s) {
            return s.is_open();
        })

        // Transaction control
        .def("begin_transaction", [](mw::Storage& s) {
            s.begin_transaction();
        }, "开始事务")
        .def("commit_transaction", [](mw::Storage& s) {
            s.commit_transaction();
        }, "提交事务")
        .def("rollback_transaction", [](mw::Storage& s) {
            s.rollback_transaction();
        }, "回滚事务")
        .def("checkpoint", [](mw::Storage& s, int mode) {
            py::gil_scoped_release release;
            s.checkpoint(mode);
        }, py::arg("mode") = 0,
           "WAL checkpoint（0=PASSIVE, 1=FULL, 2=RESTART, 3=TRUNCATE）")

        .def("init_schema", [](mw::Storage& s) {
            py::gil_scoped_release release;
            s.init_schema();
        }, "初始化数据库 schema（幂等）")

        // Memory CRUD
        .def("get_memory", [](mw::Storage& s, int doc_id) {
            py::gil_scoped_release release;
            return s.get_memory(doc_id);
        }, py::arg("doc_id"),
           "读取单条记忆")
        .def("get_memories_batch", [](mw::Storage& s, const std::vector<int>& doc_ids) {
            py::gil_scoped_release release;
            return s.get_memories_batch(doc_ids);
        }, py::arg("doc_ids"),
           "批量读取记忆（单次 SQL，避免 N+1）")
        .def("get_memories_by_category", [](mw::Storage& s, const std::string& category, int limit) {
            py::gil_scoped_release release;
            return s.get_memories_by_category(category, limit);
        }, py::arg("category") = "", py::arg("limit") = 50,
           "按分类获取记忆列表")
        .def("insert_memory", [](mw::Storage& s, const std::string& content,
                                   const std::map<std::string, std::string>& classification,
                                   const std::string& source) {
            py::gil_scoped_release release;
            return s.insert_memory(content, classification, source);
        }, py::arg("content"), py::arg("classification"), py::arg("source") = "cpp",
           "[deprecated] 请使用 batch_ingest。insert_memory 独立 COMMIT 会破坏 batch_ingest 的事务一致性。")
        .def("update_memory", [](mw::Storage& s, int doc_id, const std::string& summary,
                                  const std::string& importance, int weight) {
            py::gil_scoped_release release;
            return s.update_memory(doc_id, summary, importance, weight);
        }, py::arg("doc_id"), py::arg("summary"),
           py::arg("importance") = "", py::arg("weight") = 0,
           "更新记忆内容")

        // Entity operations
        .def("insert_entities", [](mw::Storage& s, int doc_id,
                                    const std::vector<std::pair<std::string, std::string>>& entities) {
            py::gil_scoped_release release;
            return s.insert_entities(doc_id, entities);
        }, py::arg("doc_id"), py::arg("entities"),
           "批量写入实体（ON CONFLICT 自动 weight+1）")

        // Cross reference operations
        .def("insert_cross_refs", [](mw::Storage& s, int doc_id,
                                      const std::vector<std::map<std::string, std::string>>& refs) {
            py::gil_scoped_release release;
            return s.insert_cross_refs(doc_id, refs);
        }, py::arg("doc_id"), py::arg("refs"),
           "批量写入交叉引用")
        .def("find_cross_ref_candidates", [](mw::Storage& s, int doc_id, int top_k) {
            py::gil_scoped_release release;
            return s.find_cross_ref_candidates(doc_id, top_k);
        }, py::arg("doc_id"), py::arg("top_k") = 3,
           "查找交叉引用候选（entity共享 + 同category）")

        // Mention scanning
        .def("scan_mentions", [](mw::Storage& s, int doc_id, int min_name_len, int top_entities) {
            py::gil_scoped_release release;
            return s.scan_mentions(doc_id, min_name_len, top_entities);
        }, py::arg("doc_id"), py::arg("min_name_len") = 2, py::arg("top_entities") = 100,
           "扫描正文中的 entity 提及（隐式关联检测）")

        // Batch ingest
        .def("batch_ingest", [](mw::Storage& s, const std::string& content,
                                 const std::map<std::string, std::string>& classification,
                                 const std::vector<std::pair<std::string, std::string>>& entities,
                                 const std::string& source,
                                 bool auto_refs, int ref_top_k) {
            py::gil_scoped_release release;
            return s.batch_ingest(content, classification, entities, source, auto_refs, ref_top_k);
        }, py::arg("content"), py::arg("classification"),
           py::arg("entities"), py::arg("source") = "sdk",
            py::arg("auto_refs") = true, py::arg("ref_top_k") = 3,
            "批量写入：memory + entities + cross_refs（单事务）")

        // Cross references
        .def("get_linked", [](mw::Storage& s, int doc_id) {
            py::gil_scoped_release release;
            return s.get_linked(doc_id);
        }, py::arg("doc_id"),
           "获取关联文档 ID 列表")

        // Access records
        .def("record_access", [](mw::Storage& s, int doc_id) {
            py::gil_scoped_release release;
            s.record_access(doc_id);
        }, py::arg("doc_id"),
           "记录访问 + 自动增权")
        .def("record_access_batch", [](mw::Storage& s, const std::vector<int>& doc_ids) {
            py::gil_scoped_release release;
            s.record_access_batch(doc_ids);
        }, py::arg("doc_ids"),
           "批量记录访问")
        .def("has_recent_access", [](mw::Storage& s, int doc_id, int days) {
            py::gil_scoped_release release;
            return s.has_recent_access(doc_id, days);
        }, py::arg("doc_id"), py::arg("days") = 7,
           "检查近期是否有访问记录")

        // Weight operations
        .def("decay_weights", [](mw::Storage& s, double factor, int min_weight, int decay_days) {
            py::gil_scoped_release release;
            return s.decay_weights(factor, min_weight, decay_days);
        }, py::arg("factor") = 0.8, py::arg("min_weight") = 10,
           py::arg("decay_days") = 30,
           "衰减长期未访问的记忆权重")

        // Stats
        .def("count_memories", [](mw::Storage& s) {
            py::gil_scoped_release release;
            return s.count_memories();
        }, "记忆总数")
        .def("count_entities", [](mw::Storage& s) {
            py::gil_scoped_release release;
            return s.count_entities();
        }, "实体总数")
        .def("count_all_cross_refs", [](mw::Storage& s) {
            py::gil_scoped_release release;
            return s.count_cross_refs(0);
        }, "交叉引用总数")
        .def("has_recent_access_batch", [](mw::Storage& s, const std::vector<int>& doc_ids, int days) {
            py::gil_scoped_release release;
            auto result = s.has_recent_access_batch(doc_ids, days);
            return std::vector<int>(result.begin(), result.end());
        }, py::arg("doc_ids"), py::arg("days") = 7,
           "批量查询最近访问的 doc_id 列表")

        // Evolution System
        .def("increment_correction", [](mw::Storage& s, const std::string& pattern, const std::string& summary, const std::string& context) {
            py::gil_scoped_release release;
            return s.increment_correction(pattern, summary, context);
        }, py::arg("pattern"), py::arg("summary"), py::arg("context") = "",
           "记录纠正（累加 count）")
        .def("get_correction_pending", [](mw::Storage& s, int min_count) {
            py::gil_scoped_release release;
            return s.get_correction_pending(min_count);
        }, py::arg("min_count") = 3,
           "获取待固化纠正")
        .def("suppress_correction", [](mw::Storage& s, const std::string& pattern) {
            py::gil_scoped_release release;
            return s.suppress_correction(pattern);
        }, py::arg("pattern"),
           "抑制纠正（24h）")
        .def("promote_correction", [](mw::Storage& s, const std::string& pattern) {
            py::gil_scoped_release release;
            return s.promote_correction(pattern);
        }, py::arg("pattern"),
           "标记纠正已晋升为规则")
        .def("list_corrections", [](mw::Storage& s, int limit) {
            py::gil_scoped_release release;
            return s.list_corrections(limit);
        }, py::arg("limit") = 20,
           "查看纠正历史")
        .def("log_event", [](mw::Storage& s, const std::string& event_type, const std::string& trigger, int target_doc_id, const std::string& detail, double certainty) {
            py::gil_scoped_release release;
            return s.log_event(event_type, trigger, target_doc_id, detail, certainty);
        }, py::arg("event_type"), py::arg("trigger"), py::arg("target_doc_id") = 0, py::arg("detail") = "", py::arg("certainty") = 0.0,
           "记录进化事件")
        .def("get_evolution_log", [](mw::Storage& s, const std::string& event_type, int limit) {
            py::gil_scoped_release release;
            return s.get_evolution_log(event_type, limit);
        }, py::arg("event_type") = "", py::arg("limit") = 20,
           "查看进化日志")
        .def("apply_tier_change", [](mw::Storage& s, int doc_id, const std::string& from_tier, const std::string& to_tier, const std::string& reason) {
            py::gil_scoped_release release;
            return s.apply_tier_change(doc_id, from_tier, to_tier, reason);
        }, py::arg("doc_id"), py::arg("from_tier"), py::arg("to_tier"), py::arg("reason") = "",
           "变更记忆层级")
        .def("get_tier_history", [](mw::Storage& s, int doc_id, int limit) {
            py::gil_scoped_release release;
            return s.get_tier_history(doc_id, limit);
        }, py::arg("doc_id") = 0, py::arg("limit") = 20,
           "查看层级变更历史")
        .def("get_evolution_stats", [](mw::Storage& s) {
            py::gil_scoped_release release;
            return s.get_evolution_stats();
        }, "进化统计")

        // Always Load
        .def("set_always_load", [](mw::Storage& s, int doc_id, bool enabled) {
            py::gil_scoped_release release;
            return s.set_always_load(doc_id, enabled);
        }, py::arg("doc_id"), py::arg("enabled") = true,
           "设置 always_load 标记")
        .def("get_always_load", [](mw::Storage& s, int limit) {
            py::gil_scoped_release release;
            return s.get_always_load(limit);
        }, py::arg("limit") = 5,
           "获取 always_load 记忆")
        .def("clear_always_load", [](mw::Storage& s, int doc_id) {
            py::gil_scoped_release release;
            return s.clear_always_load(doc_id);
        }, py::arg("doc_id") = 0,
           "清除 always_load 标记")

        // Health Check
        .def("health_check", [](mw::Storage& s) {
            py::gil_scoped_release release;
            return s.health_check();
        }, "健康度检查")

        // Cleanup
        .def("cleanup_memories", [](mw::Storage& s, const std::string& mode, bool hard, bool dry_run) {
            py::gil_scoped_release release;
            return s.cleanup_memories(mode, hard, dry_run);
        }, py::arg("mode") = "test", py::arg("hard") = false, py::arg("dry_run") = false,
           "清理测试/过期数据")

        // FTS5 Maintenance
        .def("rebuild_fts5", [](mw::Storage& s) {
            py::gil_scoped_release release;
            return s.rebuild_fts5();
        }, "重建 FTS5 索引")

        // Candidates
        .def("get_own_candidates", [](mw::Storage& s, int cold_days, int cold_max_weight) {
            py::gil_scoped_release release;
            return s.get_own_candidates(cold_days, cold_max_weight);
        }, py::arg("cold_days") = 90, py::arg("cold_max_weight") = 20,
           "获取冷记忆候选")

        // v0.19.0: Scene / Emotion / Session State
        .def("set_scene", [](mw::Storage& s, const mw::SceneRecord& scene) {
            py::gil_scoped_release release;
            return s.set_scene(scene);
        }, py::arg("scene"),
           "设置场景定义")
        .def("get_scene", [](mw::Storage& s, const std::string& scene_id) {
            py::gil_scoped_release release;
            return s.get_scene(scene_id);
        }, py::arg("scene_id"),
           "获取场景定义")
        .def("list_scenes", [](mw::Storage& s) {
            py::gil_scoped_release release;
            return s.list_scenes();
        }, "列出所有场景")
        .def("set_scene_rule", [](mw::Storage& s, const mw::SceneRuleRecord& rule) {
            py::gil_scoped_release release;
            return s.set_scene_rule(rule);
        }, py::arg("rule"),
           "设置场景规则")
        .def("get_scene_rules", [](mw::Storage& s, const std::string& scene_id) {
            py::gil_scoped_release release;
            return s.get_scene_rules(scene_id);
        }, py::arg("scene_id"),
           "获取场景规则列表")
        .def("set_emotion", [](mw::Storage& s, int doc_id, const std::string& emotion_type,
                                const std::string& emotion_detail, double intensity) {
            py::gil_scoped_release release;
            return s.set_emotion(doc_id, emotion_type, emotion_detail, intensity);
        }, py::arg("doc_id"), py::arg("emotion_type"),
           py::arg("emotion_detail") = "", py::arg("intensity") = 0.5,
           "记录情绪")
        .def("get_emotion", [](mw::Storage& s, int doc_id) {
            py::gil_scoped_release release;
            return s.get_emotion(doc_id);
        }, py::arg("doc_id"),
           "获取最新情绪记录")
        .def("save_session_state", [](mw::Storage& s, const mw::SessionStateRecord& state) {
            py::gil_scoped_release release;
            return s.save_session_state(state);
        }, py::arg("state"),
           "保存对话状态")
        .def("get_session_state", [](mw::Storage& s, const std::string& agent_name,
                                      const std::string& session_id) {
            py::gil_scoped_release release;
            return s.get_session_state(agent_name, session_id);
        }, py::arg("agent_name"), py::arg("session_id") = "",
           "获取对话状态")

        // v0.20.0: Tier / Temporal / Entity Resolution
        .def("set_tier", [](mw::Storage& s, int doc_id, const std::string& tier,
                            const std::string& reason) {
            py::gil_scoped_release release;
            return s.set_tier(doc_id, tier, reason);
        }, py::arg("doc_id"), py::arg("tier"), py::arg("reason") = "",
           "设置记忆层级 (hot/warm/cold)")
        .def("get_tier", [](mw::Storage& s, int doc_id) {
            py::gil_scoped_release release;
            return s.get_tier(doc_id);
        }, py::arg("doc_id"),
           "获取记忆层级")
        .def("get_hot_memories", [](mw::Storage& s, int limit) {
            py::gil_scoped_release release;
            return s.get_hot_memories(limit);
        }, py::arg("limit") = 100,
           "获取热记忆列表")
        .def("archive_memory", [](mw::Storage& s, int doc_id, const std::string& reason) {
            py::gil_scoped_release release;
            return s.archive_memory(doc_id, reason);
        }, py::arg("doc_id"), py::arg("reason") = "",
           "归档记忆（移到cold）")
        .def("forget_memory", [](mw::Storage& s, int doc_id, const std::string& reason) {
            py::gil_scoped_release release;
            return s.forget_memory(doc_id, reason);
        }, py::arg("doc_id"), py::arg("reason") = "",
           "删除记忆（软删除）")
        .def("set_valid_time", [](mw::Storage& s, int doc_id, const std::string& valid_from,
                                   const std::string& valid_until) {
            py::gil_scoped_release release;
            return s.set_valid_time(doc_id, valid_from, valid_until);
        }, py::arg("doc_id"), py::arg("valid_from"), py::arg("valid_until") = "",
           "设置记忆生效/失效时间")
        .def("get_current_valid", [](mw::Storage& s, const std::string& entity_name) {
            py::gil_scoped_release release;
            return s.get_current_valid(entity_name);
        }, py::arg("entity_name"),
           "获取实体当前有效记忆")
        .def("resolve_entity", [](mw::Storage& s, const std::string& name,
                                   const std::string& alias) {
            py::gil_scoped_release release;
            return s.resolve_entity(name, alias);
        }, py::arg("name"), py::arg("alias"),
           "实体解析（设置别名）")
        .def("update_entity_mention", [](mw::Storage& s, int entity_id, int memory_id,
                                          const std::string& context) {
            py::gil_scoped_release release;
            return s.update_entity_mention(entity_id, memory_id, context);
        }, py::arg("entity_id"), py::arg("memory_id"), py::arg("context") = "",
           "更新实体提及记录");
}

// ═══════════════════════════════════════════════════════════════════
// SearchEngine 绑定 — 搜索核心
// ═══════════════════════════════════════════════════════════════════

void bind_search_engine(py::module_& m) {
    // SearchMode enum
    py::enum_<mw::SearchMode>(m, "SearchMode")
        .value("RRF", mw::SearchMode::RRF)
        .value("Hybrid", mw::SearchMode::Hybrid)
        .export_values();

    // SearchConfig
    py::class_<mw::SearchConfig>(m, "SearchConfig")
        .def(py::init<>())
        .def_readwrite("mode", &mw::SearchConfig::mode)
        .def_readwrite("k", &mw::SearchConfig::k)
        .def_readwrite("dedup_window_minutes", &mw::SearchConfig::dedup_window_minutes)
        .def_readwrite("hnsw_M", &mw::SearchConfig::hnsw_M)
        .def_readwrite("hnsw_ef_construction", &mw::SearchConfig::hnsw_ef_construction)
        .def_readwrite("hnsw_ef_search", &mw::SearchConfig::hnsw_ef_search)
        .def("get_weights", [](const mw::SearchConfig& c) {
            return std::vector<double>(c.weights, c.weights + 3);
        })
        .def("set_weights", [](mw::SearchConfig& c, const std::vector<double>& w) {
            if (w.size() >= 3) {
                c.weights[0] = w[0];
                c.weights[1] = w[1];
                c.weights[2] = w[2];
            }
        });

    // SearchEngine
    py::class_<mw::SearchEngine>(m, "SearchEngine")
        .def(py::init<mw::Storage&, const mw::SearchConfig&>(),
             py::arg("storage"), py::arg("config") = mw::SearchConfig{},
             "融合搜索引擎")
        .def("search", [](mw::SearchEngine& e, const std::string& query, int top_k,
                          bool enable_vector, bool enable_graph,
                          int graph_expand_top, int graph_max_hops,
                          const std::string& extra_keywords) {
            py::gil_scoped_release release;
            return e.search(query, top_k, enable_vector, enable_graph,
                            graph_expand_top, graph_max_hops, extra_keywords);
        }, py::arg("query"), py::arg("top_k") = 10,
           py::arg("enable_vector") = false,
           py::arg("enable_graph") = false,
           py::arg("graph_expand_top") = 3,
           py::arg("graph_max_hops") = 2,
           py::arg("extra_keywords") = "",
           "多路融合搜索")
        .def("search_with_embedding", [](mw::SearchEngine& e, const std::string& query,
                                           const std::vector<float>& query_embedding,
                                           int top_k, bool enable_graph,
                                           int graph_expand_top, int graph_max_hops,
                                           const std::string& extra_keywords) {
            py::gil_scoped_release release;
            return e.search(query, query_embedding, top_k, enable_graph,
                            graph_expand_top, graph_max_hops, extra_keywords);
        }, py::arg("query"), py::arg("query_embedding"),
           py::arg("top_k") = 10,
           py::arg("enable_graph") = false,
           py::arg("graph_expand_top") = 3,
           py::arg("graph_max_hops") = 2,
           py::arg("extra_keywords") = "",
           "带 query embedding 的融合搜索")
         .def("has_vector_index", [](const mw::SearchEngine& e) {
            return e.has_vector_index();
        }, "是否有向量索引");
}

// ═══════════════════════════════════════════════════════════════════
// GraphEngine 绑定 — 图遍历和路径查找
// ═══════════════════════════════════════════════════════════════════

void bind_graph_engine(py::module_& m) {
    py::class_<mw::GraphEngine>(m, "GraphEngine")
        .def(py::init<mw::Storage&>(), py::arg("storage"),
             "知识图谱引擎")
        .def("build", [](mw::GraphEngine& g) {
            py::gil_scoped_release release;
            g.build();
        }, "从数据库构建图")
        .def("invalidate", [](mw::GraphEngine& g) {
            g.invalidate();
        }, "使缓存失效")
        .def("bfs_traverse", [](mw::GraphEngine& g, int source, int max_hops,
                                 const std::string& relation_type) {
            py::gil_scoped_release release;
            return g.bfs_traverse(source, max_hops, relation_type);
        }, py::arg("source"), py::arg("max_hops") = 3,
           py::arg("relation_type") = "",
           "BFS 遍历")
        .def("bfs_by_hop", [](mw::GraphEngine& g, int source, int max_hops,
                               const std::string& relation_type) {
            py::gil_scoped_release release;
            return g.bfs_by_hop(source, max_hops, relation_type);
        }, py::arg("source"), py::arg("max_hops") = 3,
           py::arg("relation_type") = "",
           "BFS 按跳数分组")
        .def("get_neighbors", [](mw::GraphEngine& g, int doc_id,
                                  const std::string& relation_type) {
            py::gil_scoped_release release;
            return g.get_neighbors(doc_id, relation_type);
        }, py::arg("doc_id"), py::arg("relation_type") = "",
           "获取邻居节点")
        .def("shortest_path", [](mw::GraphEngine& g, int source, int target, int max_hops) {
            py::gil_scoped_release release;
            return g.shortest_path(source, target, max_hops);
        }, py::arg("source"), py::arg("target"), py::arg("max_hops") = 5,
           "Dijkstra 最短路径")
        .def("find_path", [](mw::GraphEngine& g, int source, int target, int max_hops) {
            py::gil_scoped_release release;
            return g.find_path(source, target, max_hops);
        }, py::arg("source"), py::arg("target"), py::arg("max_hops") = 5,
           "智能路径查找（Dijkstra + BFS）")
        .def("get_stats", [](mw::GraphEngine& g) {
            py::gil_scoped_release release;
            return g.get_stats();
        }, "图谱统计")
        .def("add_edge", [](mw::GraphEngine& g, int doc_id, int related_doc_id,
                             const std::string& relation_type, const std::string& note) {
            py::gil_scoped_release release;
            return g.add_edge(doc_id, related_doc_id, relation_type, note);
        }, py::arg("doc_id"), py::arg("related_doc_id"),
           py::arg("relation_type") = "related", py::arg("note") = "",
           "添加边");
}

// ═══════════════════════════════════════════════════════════════════
// Rules 绑定 — 规则引擎
// ═══════════════════════════════════════════════════════════════════

void bind_rules(py::module_& m) {
    py::class_<mw::Rules>(m, "Rules")
        .def(py::init<mw::Storage&>(), py::arg("storage"),
             "规则引擎")
        .def("get_rules", [](mw::Rules& r, const std::string& category, int limit) {
            py::gil_scoped_release release;
            return r.get_rules(category, limit);
        }, py::arg("category") = "", py::arg("limit") = 20,
           "获取全局规则")
        .def("get_entities", [](mw::Rules& r, const std::string& name, int limit) {
            py::gil_scoped_release release;
            return r.get_entities(name, limit);
        }, py::arg("name") = "", py::arg("limit") = 50,
           "获取实体列表")
        .def("find_cross_ref_candidates", [](mw::Rules& r, int doc_id, int top_k) {
            py::gil_scoped_release release;
            return r.find_cross_ref_candidates(doc_id, top_k);
        }, py::arg("doc_id"), py::arg("top_k") = 3,
           "查找交叉引用候选")
        .def("insert_cross_refs", [](mw::Rules& r, int doc_id,
                                      const std::vector<std::map<std::string, std::string>>& refs) {
            py::gil_scoped_release release;
            return r.insert_cross_refs(doc_id, refs);
        }, py::arg("doc_id"), py::arg("refs"),
           "批量写入交叉引用")
        .def("auto_cross_ref", [](mw::Rules& r, int doc_id, int top_k, bool scan_mentions) {
            py::gil_scoped_release release;
            return r.auto_cross_ref(doc_id, top_k, scan_mentions);
        }, py::arg("doc_id"), py::arg("top_k") = 3, py::arg("scan_mentions") = true,
           "自动建双向交叉引用（含 mention 扫描）");
}

// ═══════════════════════════════════════════════════════════════════
// HNSWIndex 绑定 — 向量索引
// ═══════════════════════════════════════════════════════════════════

void bind_hnsw_index(py::module_& m) {
    // HNSWIndex::SearchResult (inner type)
    py::class_<mw::HNSWIndex::SearchResult>(m, "HNSWSearchResult")
        .def_readonly("id", &mw::HNSWIndex::SearchResult::id)
        .def_readonly("distance", &mw::HNSWIndex::SearchResult::distance)
        .def("__repr__", [](const mw::HNSWIndex::SearchResult& r) {
            return "<HNSWSearchResult id=" + std::to_string(r.id) +
                   " distance=" + std::to_string(r.distance) + ">";
        });

    // HNSWIndex
    py::class_<mw::HNSWIndex>(m, "HNSWIndex")
        .def(py::init<int, int, int>(),
             py::arg("dim"), py::arg("M") = 16, py::arg("ef_construction") = 200,
             "HNSW 向量索引")
        .def("add", [](mw::HNSWIndex& idx, int id, const std::vector<float>& vector) {
            py::gil_scoped_release release;
            idx.add(id, vector);
        }, py::arg("id"), py::arg("vector"),
           "添加向量")
        .def("add_batch", [](mw::HNSWIndex& idx,
                              const std::vector<int>& ids,
                              const std::vector<std::vector<float>>& vectors) {
            py::gil_scoped_release release;
            idx.add_batch(ids, vectors);
        }, py::arg("ids"), py::arg("vectors"),
           "批量添加向量")
        .def("search", [](mw::HNSWIndex& idx, const std::vector<float>& query,
                           int top_k, int ef) {
            py::gil_scoped_release release;
            return idx.search(query, top_k, ef);
        }, py::arg("query"), py::arg("top_k") = 10, py::arg("ef") = 100,
           "搜索最近邻")
        .def("remove", [](mw::HNSWIndex& idx, int id) {
            py::gil_scoped_release release;
            idx.remove(id);
        }, py::arg("id"),
           "删除向量")
        .def("clear", [](mw::HNSWIndex& idx) {
            py::gil_scoped_release release;
            idx.clear();
        }, "清空索引")
        .def("reserve", [](mw::HNSWIndex& idx, size_t n) {
            idx.reserve(n);
        }, py::arg("n"), "预分配内存（批量构建前调用）")
        .def("size", [](const mw::HNSWIndex& idx) { return idx.size(); }, "索引大小")
        .def("empty", [](const mw::HNSWIndex& idx) { return idx.empty(); }, "是否为空")
        .def("dimension", [](const mw::HNSWIndex& idx) { return idx.dimension(); }, "向量维度")
        .def("serialize", [](mw::HNSWIndex& idx) {
            py::gil_scoped_release release;
            return idx.serialize();
        }, "序列化")
        .def("deserialize", [](mw::HNSWIndex& idx, const std::vector<char>& data) {
            py::gil_scoped_release release;
            return idx.deserialize(data);
        }, py::arg("data"),
           "反序列化");
}

// ═══════════════════════════════════════════════════════════════════
// 模块级辅助函数
// ═══════════════════════════════════════════════════════════════════

void bind_helpers(py::module_& m) {
    // Storage embedding methods
    m.def("storage_get_memory_embedding", [](mw::Storage& s, int doc_id) {
        py::gil_scoped_release release;
        return s.get_memory_embedding(doc_id);
    }, py::arg("storage"), py::arg("doc_id"), "获取记忆的 embedding");

    m.def("storage_get_all_embeddings", [](mw::Storage& s) {
        py::gil_scoped_release release;
        return s.get_all_embeddings();
    }, py::arg("storage"), "获取所有 embeddings");

    // Embedding engine methods
    m.def("storage_load_embedding", [](mw::Storage& s, const std::string& model_dir) {
        py::gil_scoped_release release;
        return s.load_embedding(model_dir);
    }, py::arg("storage"), py::arg("model_dir"), "加载 ONNX embedding 模型");

    m.def("storage_has_embedding", [](mw::Storage& s) {
        return s.has_embedding();
    }, py::arg("storage"), "检查 embedding 模型是否已加载");

    m.def("storage_embed_text", [](mw::Storage& s, const std::string& text) {
        py::gil_scoped_release release;
        return s.get_query_embedding(text);
    }, py::arg("storage"), py::arg("text"), "用 C++ 引擎生成文本 embedding");

    // SearchEngine vector methods
    m.def("search_engine_build_vector_index", [](mw::SearchEngine& e, int dim) {
        py::gil_scoped_release release;
        e.build_vector_index(dim);
    }, py::arg("engine"), py::arg("dim"), "构建向量索引");

    m.def("search_engine_add_vector", [](mw::SearchEngine& e, int id,
                                           const std::vector<float>& vec) {
        py::gil_scoped_release release;
        e.add_vector(id, vec);
    }, py::arg("engine"), py::arg("id"), py::arg("vector"), "添加向量到索引");

    m.def("search_engine_vector_search", [](mw::SearchEngine& e,
                                              const std::vector<float>& query, int top_k) {
        py::gil_scoped_release release;
        return e.vector_search(query, top_k);
    }, py::arg("engine"), py::arg("query"), py::arg("top_k") = 10, "向量搜索");

    m.def("search_engine_has_vector_index", [](mw::SearchEngine& e) {
        return e.has_vector_index();
    }, py::arg("engine"), "是否有向量索引");

    m.def("search_engine_save_vector_index", [](mw::SearchEngine& e) {
        py::gil_scoped_release release;
        return e.save_vector_index();
    }, py::arg("engine"), "保存向量索引");

    m.def("search_engine_load_vector_index", [](mw::SearchEngine& e,
                                                 const std::vector<char>& data) {
        py::gil_scoped_release release;
        return e.load_vector_index(data);
    }, py::arg("engine"), py::arg("data"), "加载向量索引");
}

// ═══════════════════════════════════════════════════════════════════
// 主模块入口
// ═══════════════════════════════════════════════════════════════════

std::string version() {
    return "1.0.0";
}

PYBIND11_MODULE(mw_core, m) {
    m.doc() = "MW Core Engine — C++ 核心搜索/索引引擎";
    m.def("version", &version, "返回 MW Core 版本号");

    bind_types(m);
    bind_storage(m);
    bind_search_engine(m);
    bind_graph_engine(m);
    bind_rules(m);
    bind_hnsw_index(m);
    bind_helpers(m);
}
