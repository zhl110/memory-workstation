#pragma once

#include <string>
#include <vector>
#include <map>
#include <set>
#include <optional>
#include <memory>
#include <sqlite3.h>

namespace mw {

// ── Named Constants ──────────────────────────────────────────
// Search engine
constexpr double RECENCY_BOOST = 1.3;           // search_engine: recent access bonus
constexpr double GRAPH_DECAY = 0.7;             // search/graph: per-hop score decay

// Weight management
constexpr int WEIGHT_ACCESS_INCREMENT = 5;      // record_access: weight bump per hit
constexpr int WEIGHT_CAP = 100;                 // weight upper bound

// Entity defaults
constexpr double ENTITY_INITIAL_WEIGHT = 1.0;   // new entity weight
constexpr int ENTITY_WEIGHT_INCREMENT = 1;      // duplicate entity bump

// Content limits
constexpr size_t CONTENT_PREVIEW_SIZE = 500;    // snippet truncation for storage

// SQLite constraints
constexpr int SQLITE_VARIABLE_LIMIT = 999;      // max bound parameters per query
constexpr int NO_ACCESS_SENTINEL = 999;         // days_since_last_access: no record

// Forward declare

struct SearchResult {
    int doc_id;
    double score;
    std::string summary;
    std::string category;
    std::string importance;
    int weight;
    std::string scope;
    std::string project;
    std::map<std::string, double> signals;
};

struct MemoryRecord {
    int doc_id;
    std::string label;
    std::string importance;
    int weight;
    std::string summary;
    std::string category;
    std::string sub_category;
    std::string content;
    std::string scope;
    std::string project;
};

struct MentionHit {
    int related_doc_id;
    std::string entity_name;
    int mention_count;
};

struct LinkedResult {
    int doc_id;
    std::string relation_type;
    std::string note;
    double weight = 1.0;
};

struct CorrectionRecord {
    int id;
    std::string pattern;
    std::string summary;
    std::string context;
    int count;
    bool promoted;
    std::string suppressed_at;
    std::string occurred_at;
    std::string last_occurred_at;
};

struct EvolutionLogEntry {
    int id;
    std::string event_type;
    std::string trigger_name;
    int target_doc_id;
    std::string detail;
    double certainty;
    std::string created_at;
};

struct TierHistoryEntry {
    int id;
    int doc_id;
    std::string from_tier;
    std::string to_tier;
    std::string reason;
    std::string applied_at;
    std::string summary;
};

struct CandidateRecord {
    int doc_id;
    std::string summary;
    std::string label;
    std::string importance;
    int weight;
    std::string evolution_tier;
};

struct HealthCheckResult {
    bool db_ok;
    int fts5_entries;
    int fts5_behind;
    bool hnsw_ready;
    int graph_nodes;
    int graph_edges;
    double graph_orphan_rate;
    std::string error;
};

// ── v0.19.0: 场景/情绪/对话状态 ──────────────────────────────

struct SceneRecord {
    std::string scene_id;
    std::string name;
    std::string parent_scene;
    std::string description;
    std::string create_time;
};

struct SceneRuleRecord {
    std::string rule_id;
    std::string scene_id;
    std::string rule_type;   // must / should / prefer
    std::string rule_text;
    int priority;
    std::string create_time;
};

struct EmotionRecord {
    std::string emotion_id;
    int doc_id;
    std::string emotion_type;  // positive / neutral / negative
    std::string emotion_detail;
    double intensity;
    std::string create_time;
};

struct SessionStateRecord {
    std::string state_id;
    std::string agent_name;
    std::string session_id;
    std::string last_topic;
    std::string unfinished_tasks;  // JSON array
    std::string emotion_state;
    std::string update_time;
};

class Storage {
public:
    explicit Storage(const std::string& db_path);
    ~Storage();

    // Non-copyable
    Storage(const Storage&) = delete;
    Storage& operator=(const Storage&) = delete;

    // Database
    void close();
    bool is_open() const;

    // Transaction control
    void begin_transaction();
    void commit_transaction();
    void rollback_transaction();

    // WAL checkpoint control
    void checkpoint(int mode = 0);

    // Schema
    void init_schema();

    // FTS5 search
    std::vector<SearchResult> fts_search(const std::string& query, int limit,
                                          const std::string& extra_keywords = "");
    std::vector<SearchResult> like_search(const std::string& query, int limit);

    // Entity search
    std::map<int, double> entity_search(const std::string& query);

    // Vector embedding
    std::vector<float> get_query_embedding(const std::string& query);
    std::vector<float> get_memory_embedding(int doc_id);
    std::vector<std::pair<int, std::vector<float>>> get_all_embeddings();

    // Memory CRUD
    std::optional<MemoryRecord> get_memory(int doc_id);
    std::map<int, MemoryRecord> get_memories_batch(const std::vector<int>& doc_ids);
    std::vector<MemoryRecord> get_memories_by_category(const std::string& category, int limit);
    int insert_memory(const std::string& content, const std::map<std::string, std::string>& classification,
                      const std::string& source);
    bool update_memory(int doc_id, const std::string& summary, const std::string& importance, int weight);

    // Entity operations
    int insert_entities(int doc_id, const std::vector<std::pair<std::string, std::string>>& entities);

    // Cross reference operations
    int insert_cross_refs(int doc_id, const std::vector<std::map<std::string, std::string>>& refs);

    // Cross reference candidate finding
    std::vector<std::map<std::string, std::string>> find_cross_ref_candidates(int doc_id, int top_k);

    // Mention scanning — find entity names mentioned in content
    std::vector<MentionHit> scan_mentions(int doc_id, int min_name_len = 2, int top_entities = 100);

    // Batch ingest: insert + entities + cross refs (single transaction)
    struct BatchIngestResult {
        int doc_id;
        int entities_inserted;
        int cross_refs_inserted;
    };
    BatchIngestResult batch_ingest(const std::string& content,
                                   const std::map<std::string, std::string>& classification,
                                   const std::vector<std::pair<std::string, std::string>>& entities,
                                   const std::string& source,
                                   bool auto_refs = true,
                                   int ref_top_k = 3);

    // Cross references
    std::vector<LinkedResult> get_linked(int doc_id);
    int count_cross_refs(int doc_id);

    // Access records
    void record_access(int doc_id);
    void record_access_batch(const std::vector<int>& doc_ids);
    bool has_recent_access(int doc_id, int days = 7);
    std::set<int> has_recent_access_batch(const std::vector<int>& doc_ids, int days = 7);
    std::map<int, int> get_access_days_batch(const std::vector<int>& doc_ids);
    std::map<int, int> get_weights_batch(const std::vector<int>& doc_ids);
    int days_since_last_access(int doc_id);

    // Weight operations
    int decay_weights(double factor = 0.8, int min_weight = 10, int decay_days = 30);

    // Stats
    int count_memories();
    int count_entities();
    int count_cross_refs();

    // ── Evolution System ────────────────────────────────────────
    // Correction log
    std::pair<int, bool> increment_correction(const std::string& pattern, const std::string& summary, const std::string& context = "");
    std::vector<CorrectionRecord> get_correction_pending(int min_count = 3);
    bool suppress_correction(const std::string& pattern);
    bool promote_correction(const std::string& pattern);
    std::vector<CorrectionRecord> list_corrections(int limit = 20);

    // Evolution log
    int log_event(const std::string& event_type, const std::string& trigger, int target_doc_id = 0, const std::string& detail = "", double certainty = 0.0);
    std::vector<EvolutionLogEntry> get_evolution_log(const std::string& event_type = "", int limit = 20);

    // Tier management
    bool apply_tier_change(int doc_id, const std::string& from_tier, const std::string& to_tier, const std::string& reason = "");
    std::vector<TierHistoryEntry> get_tier_history(int doc_id = 0, int limit = 20);

    // Evolution stats
    std::map<std::string, int> get_evolution_stats();

    // ── Always Load ─────────────────────────────────────────────
    bool set_always_load(int doc_id, bool enabled = true);
    std::vector<CandidateRecord> get_always_load(int limit = 5);
    int clear_always_load(int doc_id = 0);

    // ── Health Check ────────────────────────────────────────────
    HealthCheckResult health_check();

    // ── Cleanup ─────────────────────────────────────────────────
    std::map<std::string, int> cleanup_memories(const std::string& mode = "test", bool hard = false, bool dry_run = false);

    // ── FTS5 Maintenance ────────────────────────────────────────
    bool rebuild_fts5();

    // ── Candidates ──────────────────────────────────────────────
    std::vector<CandidateRecord> get_own_candidates(int cold_days = 90, int cold_max_weight = 20);

    // ── v0.19.0: Scene / Emotion / Session State ────────────────
    bool set_scene(const SceneRecord& scene);
    std::optional<SceneRecord> get_scene(const std::string& scene_id);
    std::vector<SceneRecord> list_scenes();
    bool set_scene_rule(const SceneRuleRecord& rule);
    std::vector<SceneRuleRecord> get_scene_rules(const std::string& scene_id);
    bool set_emotion(int doc_id, const std::string& emotion_type,
                     const std::string& emotion_detail, double intensity);
    std::optional<EmotionRecord> get_emotion(int doc_id);
    bool save_session_state(const SessionStateRecord& state);
    std::optional<SessionStateRecord> get_session_state(const std::string& agent_name,
                                                        const std::string& session_id);

    // ── v0.20.0: Tier / Temporal / Entity Resolution ────────────
    bool set_tier(int doc_id, const std::string& tier, const std::string& reason = "");
    std::string get_tier(int doc_id);
    std::vector<CandidateRecord> get_hot_memories(int limit = 100);
    bool archive_memory(int doc_id, const std::string& reason = "");
    bool forget_memory(int doc_id, const std::string& reason = "");
    bool set_valid_time(int doc_id, const std::string& valid_from,
                        const std::string& valid_until = "");
    std::vector<MemoryRecord> get_current_valid(const std::string& entity_name);
    bool resolve_entity(const std::string& name, const std::string& alias);
    bool update_entity_mention(int entity_id, int memory_id, const std::string& context = "");

    // Raw connection (for transition period)
    sqlite3* raw_conn() { return conn_; }

    // Embedding engine (lazy-loaded)
    bool load_embedding(const std::string& model_dir);
    bool has_embedding() const;

private:
    sqlite3* conn_ = nullptr;
    std::string db_path_;

    void ensure_fts5();
    void ensure_weight_column();
    void exec_sql(const std::string& sql);
};

} // namespace mw
