#include "llama.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <vector>

static std::mutex g_mutex;
static llama_model * g_model = nullptr;
static llama_context * g_ctx = nullptr;
static std::string g_model_path;
static bool g_backend_ready = false;

static int ensure_model(const char * model_path) {
    if (!model_path || !*model_path) {
        return -10;
    }

    if (g_model && g_ctx && g_model_path == model_path) {
        return 0;
    }

    if (g_ctx) {
        llama_free(g_ctx);
        g_ctx = nullptr;
    }

    if (g_model) {
        llama_model_free(g_model);
        g_model = nullptr;
    }

    if (!g_backend_ready) {
        llama_backend_init();
        g_backend_ready = true;
    }

    llama_model_params mp = llama_model_default_params();
    g_model = llama_model_load_from_file(model_path, mp);

    if (!g_model) {
        g_model_path.clear();
        return -1;
    }

    llama_context_params cp = llama_context_default_params();
    cp.n_ctx = 512;
    cp.n_batch = 512;
    cp.n_threads = 4;
    cp.n_threads_batch = 4;

    g_ctx = llama_init_from_model(g_model, cp);

    if (!g_ctx) {
        llama_model_free(g_model);
        g_model = nullptr;
        g_model_path.clear();
        return -2;
    }

    g_model_path = model_path;
    return 0;
}

extern "C" {

int julia_qwen_generate(
    const char * model_path,
    const char * user_text,
    char * out,
    int out_size
) {
    if (!model_path || !user_text || !out || out_size <= 1) {
        return -10;
    }

    out[0] = '\0';

    std::lock_guard<std::mutex> lock(g_mutex);

    int rc = ensure_model(model_path);
    if (rc != 0) {
        return rc;
    }

    const llama_vocab * vocab = llama_model_get_vocab(g_model);
    const char * tmpl = llama_model_chat_template(g_model, nullptr);

    if (!vocab || !tmpl) {
        return -3;
    }

    llama_memory_clear(llama_get_memory(g_ctx), true);

    llama_chat_message msg;
    msg.role = "user";
    msg.content = user_text;

    int32_t prompt_size = llama_chat_apply_template(
        tmpl,
        &msg,
        1,
        true,
        nullptr,
        0
    );

    if (prompt_size <= 0) {
        return -4;
    }

    std::vector<char> prompt(prompt_size + 1);

    int32_t formatted = llama_chat_apply_template(
        tmpl,
        &msg,
        1,
        true,
        prompt.data(),
        (int32_t) prompt.size()
    );

    if (formatted < 0 || formatted >= (int32_t) prompt.size()) {
        return -5;
    }

    int32_t n_tokens = llama_tokenize(
        vocab,
        prompt.data(),
        formatted,
        nullptr,
        0,
        true,
        true
    );

    if (n_tokens >= 0) {
        return -6;
    }

    n_tokens = -n_tokens;

    std::vector<llama_token> tokens(n_tokens);

    int32_t actual_tokens = llama_tokenize(
        vocab,
        prompt.data(),
        formatted,
        tokens.data(),
        n_tokens,
        true,
        true
    );

    if (actual_tokens < 0) {
        return -7;
    }

    n_tokens = actual_tokens;

    if ((uint32_t) n_tokens >= llama_n_ctx(g_ctx)) {
        return -8;
    }

    llama_batch batch = llama_batch_get_one(tokens.data(), n_tokens);

    if (llama_decode(g_ctx, batch) != 0) {
        return -9;
    }

    llama_sampler * sampler = llama_sampler_init_greedy();

    if (!sampler) {
        return -11;
    }

    std::string response;
    const int max_new_tokens = 96;

    for (int i = 0; i < max_new_tokens; ++i) {
        llama_token token = llama_sampler_sample(
            sampler,
            g_ctx,
            -1
        );

        if (llama_vocab_is_eog(vocab, token)) {
            break;
        }

        char piece[256];

        int32_t n = llama_token_to_piece(
            vocab,
            token,
            piece,
            sizeof(piece) - 1,
            0,
            false
        );

        if (n > 0) {
            piece[n] = '\0';
            response.append(piece, n);
        }

        llama_sampler_accept(sampler, token);

        batch = llama_batch_get_one(&token, 1);

        if (llama_decode(g_ctx, batch) != 0) {
            break;
        }
    }

    if ((int) response.size() >= out_size) {
        response.resize(out_size - 1);
    }

    memcpy(out, response.c_str(), response.size());
    out[response.size()] = '\0';

    llama_sampler_free(sampler);

    return 0;
}

}
