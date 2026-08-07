#pragma once
// EmbeddingEngine — C++ ONNX inference via onnxruntime C++ API (singleton)

#include <string>
#include <vector>
#include <unordered_map>
#include <memory>
#include <mutex>
#include <atomic>

struct OrtEnv;
struct OrtSession;
struct OrtMemoryInfo;
struct OrtValue;

namespace mw {

class EmbeddingEngine {
public:
    EmbeddingEngine(const EmbeddingEngine&) = delete;
    EmbeddingEngine& operator=(const EmbeddingEngine&) = delete;

    static EmbeddingEngine& instance();

    bool load(const std::string& model_dir);
    std::vector<float> embed(const std::string& text);
    bool is_loaded() const { return loaded_; }
    int embedding_dim() const { return 384; }

private:
    EmbeddingEngine() = default;
    ~EmbeddingEngine();

    bool load_vocab(const std::string& path);
    void tokenize(const std::string& text, std::vector<int64_t>& tokens);

    std::atomic<bool> loaded_{false};
    std::string model_dir_;
    mutable std::mutex mtx_;

    // Use Ort raw pointers (proper types after including C++ header)
    void* env_ = nullptr;
    void* session_ = nullptr;
    void* mem_info_ = nullptr;

    char* input_name_ = nullptr;
    char* mask_name_ = nullptr;
    char* seg_name_ = nullptr;

    std::unordered_map<std::string, int> vocab_;
    int unk_id_ = 100;
    int pad_id_ = 0;
    int sep_id_ = 102;
    int cls_id_ = 101;
    const int max_seq_len_ = 128;
};

} // namespace mw
