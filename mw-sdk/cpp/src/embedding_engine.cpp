#include "embedding_engine.h"
// C++ wrapper headers — they include onnxruntime_c_api.h internally
#include <onnxruntime_cxx_api.h>
#include <algorithm>
#include <cmath>
#include <fstream>
#include <cstring>
#include <sstream>
#ifdef _WIN32
#include <windows.h>
#endif

namespace mw {

// ── Windows UTF-8 路径辅助 ───────────────────────────────────
// std::ifstream 用窄字符串（A 码表）打开路径，中文系统按 GBK 解释 UTF-8 字节，
// 导致含中文的用户路径打不开模型/vocab 文件。
// 这里统一走宽字符路径打开（与下方 ONNX wide-char 加载一致）。

#ifdef _WIN32
static std::wstring utf8_to_wide(const std::string& utf8) {
    if (utf8.empty()) return L"";
    int len = MultiByteToWideChar(CP_UTF8, 0, utf8.c_str(), -1, nullptr, 0);
    if (len <= 0) return L"";
    std::wstring w(len - 1, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, utf8.c_str(), -1, &w[0], len);
    return w;
}
#endif

// ── Singleton ──────────────────────────────────────────────────

EmbeddingEngine& EmbeddingEngine::instance() {
    static EmbeddingEngine engine;
    return engine;
}

// ── Lifecycle ──────────────────────────────────────────────────

EmbeddingEngine::~EmbeddingEngine() {
    delete[] input_name_;
    delete[] mask_name_;
    delete[] seg_name_;

    // Cast and release
    if (session_) delete static_cast<Ort::Session*>(session_);
    if (env_) delete static_cast<Ort::Env*>(env_);
    if (mem_info_) delete static_cast<Ort::MemoryInfo*>(mem_info_);
}

bool EmbeddingEngine::load(const std::string& model_dir) {
    if (loaded_) return true;
    std::lock_guard<std::mutex> lock(mtx_);
    if (loaded_) return true;  // double-checked locking

    std::string model_path = model_dir + "/all-MiniLM-L6-v2.onnx";
    std::string vocab_path = model_dir + "/vocab.json";

    // Check model file exists（Windows 用宽字符路径，避免中文系统 GBK 解释 UTF-8 失败）
#ifdef _WIN32
    std::ifstream f(utf8_to_wide(model_path));
#else
    std::ifstream f(model_path);
#endif
    if (!f.good()) {
        fprintf(stderr, "[embedding] model file not found: %s\n", model_path.c_str());
        return false;
    }
    f.close();

    // Load vocab
    if (!load_vocab(vocab_path)) {
        fprintf(stderr, "[embedding] vocab file not found/invalid: %s\n", vocab_path.c_str());
        return false;
    }

    try {
        // Create ONNX Runtime environment
        auto* env = new Ort::Env(ORT_LOGGING_LEVEL_WARNING, "mw_embed");
        env_ = env;

        // Session options
        Ort::SessionOptions opts;
        opts.SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_ENABLE_BASIC);

        // Create session (wide char path on Windows, proper UTF-8 conversion)
#ifdef _WIN32
        int wlen = MultiByteToWideChar(CP_UTF8, 0, model_path.c_str(), -1, nullptr, 0);
        std::wstring wpath(wlen - 1, L'\0');
        MultiByteToWideChar(CP_UTF8, 0, model_path.c_str(), -1, &wpath[0], wlen);
#else
        std::wstring wpath(model_path.begin(), model_path.end());
#endif
        auto* session = new Ort::Session(*env, wpath.c_str(), opts);
        session_ = session;

        // Get input names
        Ort::AllocatorWithDefaultOptions alloc;
        size_t count = session->GetInputCount();
        for (size_t i = 0; i < count; i++) {
            auto name = session->GetInputNameAllocated(i, alloc);
            char* dup = new char[strlen(name.get()) + 1];
            strcpy(dup, name.get());
            if (i == 0) input_name_ = dup;
            else if (i == 1) mask_name_ = dup;
            else if (i == 2) seg_name_ = dup;
            else delete[] dup;
        }

        // Create memory info
        auto* mi = new Ort::MemoryInfo(Ort::MemoryInfo::CreateCpu(
            OrtArenaAllocator, OrtMemTypeDefault));
        mem_info_ = mi;

    } catch (const Ort::Exception& e) {
        fprintf(stderr, "[embedding] ONNX Runtime error: %s\n", e.what());
        if (env_) { delete static_cast<Ort::Env*>(env_); env_ = nullptr; }
        if (session_) { delete static_cast<Ort::Session*>(session_); session_ = nullptr; }
        if (mem_info_) { delete static_cast<Ort::MemoryInfo*>(mem_info_); mem_info_ = nullptr; }
        return false;
    }

    model_dir_ = model_dir;
    loaded_ = true;
    return true;
}

// ── Minimal JSON parser for vocab ─────────────────────────────

bool EmbeddingEngine::load_vocab(const std::string& path) {
#ifdef _WIN32
    std::ifstream f(utf8_to_wide(path));
#else
    std::ifstream f(path);
#endif
    if (!f.is_open()) return false;

    std::string content((std::istreambuf_iterator<char>(f)),
                         std::istreambuf_iterator<char>());

    auto skip = [&](size_t& i) {
        while (i < content.size() && (content[i] == ' ' || content[i] == '\n' ||
               content[i] == '\r' || content[i] == '\t')) i++;
    };

    size_t i = 0;
    skip(i);
    if (i >= content.size() || content[i] != '{') return false;
    i++;

    while (i < content.size()) {
        skip(i);
        if (i >= content.size() || content[i] == '}') break;

        if (content[i] != '"') return false;
        i++;
        std::string key;
        while (i < content.size() && content[i] != '"') {
            if (content[i] == '\\' && i + 1 < content.size()) {
                key += content[i + 1]; i += 2;
            } else {
                key += content[i]; i++;
            }
        }
        if (i >= content.size()) return false;
        i++;

        skip(i);
        if (i >= content.size() || content[i] != ':') return false;
        i++;
        skip(i);

        bool neg = false;
        if (i < content.size() && content[i] == '-') { neg = true; i++; }
        int val = 0;
        while (i < content.size() && content[i] >= '0' && content[i] <= '9') {
            val = val * 10 + (content[i] - '0'); i++;
        }
        if (neg) val = -val;
        vocab_[key] = val;

        skip(i);
        if (i < content.size() && content[i] == ',') i++;
        else if (i < content.size() && content[i] == '}') break;
    }

    // Set special tokens
    auto it = vocab_.find("[UNK]"); if (it != vocab_.end()) unk_id_ = it->second;
    it = vocab_.find("[PAD]"); if (it != vocab_.end()) pad_id_ = it->second;
    it = vocab_.find("[SEP]"); if (it != vocab_.end()) sep_id_ = it->second;
    it = vocab_.find("[CLS]"); if (it != vocab_.end()) cls_id_ = it->second;

    return !vocab_.empty();
}

// ── Tokenization ──────────────────────────────────────────────

void EmbeddingEngine::tokenize(const std::string& text, std::vector<int64_t>& tokens) {
    tokens.clear();
    tokens.push_back(cls_id_);

    std::string word;
    for (char ch : text) {
        char lower = (ch >= 'A' && ch <= 'Z') ? (ch - 'A' + 'a') : ch;
        if (std::isalnum(static_cast<unsigned char>(lower)) || lower == '_') {
            word += lower;
        } else {
            if (!word.empty()) {
                auto it = vocab_.find(word);
                tokens.push_back(it != vocab_.end() ? it->second : unk_id_);
                word.clear();
            }
        }
    }
    if (!word.empty()) {
        auto it = vocab_.find(word);
        tokens.push_back(it != vocab_.end() ? it->second : unk_id_);
    }

    tokens.push_back(sep_id_);

    if ((int)tokens.size() > max_seq_len_) {
        tokens.resize(max_seq_len_);
        tokens.back() = sep_id_;
    }
}

// ── Embed ─────────────────────────────────────────────────────

std::vector<float> EmbeddingEngine::embed(const std::string& text) {
    if (!loaded_ || !session_) return {};

    auto* sess = static_cast<Ort::Session*>(session_);
    auto* mi = static_cast<Ort::MemoryInfo*>(mem_info_);

    std::vector<int64_t> tokens;
    tokenize(text, tokens);

    int seq_len = (int)tokens.size();
    std::vector<int64_t> padded = tokens;
    padded.resize(max_seq_len_, pad_id_);

    std::vector<int64_t> mask(max_seq_len_, 0);
    for (int i = 0; i < seq_len; i++) mask[i] = 1;

    std::vector<int64_t> seg(max_seq_len_, 0);

    std::vector<int64_t> shape = {1, max_seq_len_};

    try {
        // Create input tensors
        Ort::Value input_ids = Ort::Value::CreateTensor<int64_t>(
            *mi, padded.data(), padded.size() * sizeof(int64_t),
            shape.data(), shape.size());
        Ort::Value attn_mask = Ort::Value::CreateTensor<int64_t>(
            *mi, mask.data(), mask.size() * sizeof(int64_t),
            shape.data(), shape.size());
        Ort::Value seg_ids = Ort::Value::CreateTensor<int64_t>(
            *mi, seg.data(), seg.size() * sizeof(int64_t),
            shape.data(), shape.size());

        // Input names
        std::vector<const char*> input_names = {input_name_, mask_name_, seg_name_};
        std::vector<Ort::Value> input_values;
        input_values.push_back(std::move(input_ids));
        input_values.push_back(std::move(attn_mask));
        input_values.push_back(std::move(seg_ids));

        // Output name (index 1 = sentence_embedding)
        Ort::AllocatorWithDefaultOptions alloc;
        auto output_name = sess->GetOutputNameAllocated(1, alloc);

        const char* out_names[] = {output_name.get()};

        // Run
        auto outputs = sess->Run(Ort::RunOptions{},
                                 input_names.data(), input_values.data(), 3,
                                 out_names, 1);

        if (outputs.empty() || !outputs[0].IsTensor()) return {};

        // Extract data
        auto type_info = outputs[0].GetTensorTypeAndShapeInfo();
        size_t total = type_info.GetElementCount();
        float* data = outputs[0].GetTensorMutableData<float>();
        if (!data) return {};

        std::vector<float> result(data, data + total);

        // L2 normalize
        float sum = 0;
        for (auto v : result) sum += v * v;
        float norm = std::sqrt(sum);
        if (norm > 0) {
            for (auto& v : result) v /= norm;
        }

        return result;

    } catch (const Ort::Exception& e) {
        fprintf(stderr, "[embedding] embed() ONNX error: %s\n", e.what());
        return {};
    }
}

} // namespace mw
