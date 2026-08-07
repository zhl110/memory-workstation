#pragma once

#include "mw_core.h"
#include <string>
#include <vector>
#include <map>

namespace mw {

struct Rule {
    int id;
    std::string rule_text;
    std::string category;
    std::string sub_category;
    std::string priority;
    double confidence;
    std::string conflict_with;
    std::string complements;
};

struct Entity {
    int doc_id;
    std::string entity_name;
    std::string entity_type;
    double weight;
};

struct CrossRefCandidate {
    int doc_id;
    std::string summary;
    double score;
};

class Rules {
public:
    explicit Rules(Storage& storage);

    // Rules
    std::vector<Rule> get_rules(const std::string& category = "", int limit = 20);

    // Entities
    std::vector<Entity> get_entities(const std::string& name = "", int limit = 50);

    // Cross reference candidates
    std::vector<CrossRefCandidate> find_cross_ref_candidates(int doc_id, int top_k = 3);

    // Cross reference operations
    int insert_cross_refs(int doc_id, const std::vector<std::map<std::string, std::string>>& refs);
    int auto_cross_ref(int doc_id, int top_k = 3, bool scan_mentions = true);

private:
    Storage& storage_;
};

} // namespace mw
