#include "llama.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

extern "C" {

// Retourne une réponse générée.
// Le buffer "out" reçoit une chaîne UTF-8 terminée par \0.
// Retourne 0 si succès.
int julia_qwen_generate(
    const char *model_path,
    const char *user_text,
    char *out,
    int out_size
) {
    if (!model_path || !user_text || !out || out_size <= 1) {
        return -10;
    }

    out[0] = '\0';

    llama_backend_init();

    llama_model_params mp = llama_model_default_params();
    llama_model *model = llama_model_load_from_file(model_path, mp);

    if (!model) {
        llama_backend_free();
        return -1;
    }

    llama_context_params cp = llama_context_default_params();
    cp.n_ctx = 512;
    cp.n_batch = 512;
    cp.n_threads = 4;
    cp.n_threads_batch = 4;

    llama_context *ctx = llama_init_from_model(model, cp);

    if (!ctx) {
        llama_model_free(model);
        llama_backend_free();
        return -2;
    }

    const llama_vocab *vocab = llama_model_get_vocab(model);
    const char *tmpl = llama_model_chat_template(model, nullptr);

    if (!vocab || !tmpl) {
        llama_free(ctx);
        llama_model_free(model);
        llama_backend_free();
        return -3;
    }

    llama_chat_message msg;
    msg.role = "user";
    msg.content = user_text;

    // Première passe : obtenir la taille du prompt formaté.
    int32_t prompt_size = llama_chat_apply_template(
        tmpl,
        &msg,
        1,
        true,
        nullptr,
        0
    );

    if (prompt_size <= 0) {
        llama_free(ctx);
        llama_model_free(model);
        llama_backend_free();
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
        llama_free(ctx);
        llama_model_free(model);
        llama_backend_free();
        return -5;
    }

    // Première passe de tokenisation.
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
        llama_free(ctx);
        llama_model_free(model);
        llama_backend_free();
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
        llama_free(ctx);
        llama_model_free(model);
        llama_backend_free();
        return -7;
    }

    n_tokens = actual_tokens;

    if ((uint32_t)n_tokens >= llama_n_ctx(ctx)) {
        llama_free(ctx);
        llama_model_free(model);
        llama_backend_free();
        return -8;
    }

    llama_batch batch = llama_batch_get_one(tokens.data(), n_tokens);

    if (llama_decode(ctx, batch) != 0) {
        llama_free(ctx);
        llama_model_free(model);
        llama_backend_free();
        return -9;
    }

    llama_sampler *sampler = llama_sampler_init_greedy();

    if (!sampler) {
        llama_free(ctx);
        llama_model_free(model);
        llama_backend_free();
        return -11;
    }

    std::string response;

    const int max_new_tokens = 96;

    for (int i = 0; i < max_new_tokens; ++i) {
        llama_token token = llama_sampler_sample(
            sampler,
            ctx,
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

        if (llama_decode(ctx, batch) != 0) {
            break;
        }
    }

    if ((int)response.size() >= out_size) {
        response.resize(out_size - 1);
    }

    memcpy(out, response.c_str(), response.size());
    out[response.size()] = '\0';

    llama_sampler_free(sampler);
    llama_free(ctx);
    llama_model_free(model);
    llama_backend_free();

    return 0;
}

}
