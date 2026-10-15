# Gemma 4 in ExitCapsule

## Model

| | |
|---|---|
| Default model ID | `gemma-4-31b-it` |
| Also available | `gemma-4-26b-a4b-it` (MoE, faster) |
| Access | Google AI Studio / Gemini API, `google-genai` SDK (primary); Ollama (optional local) |
| Discovery | `client.models.list()` at startup; only IDs matching `gemma-4` are accepted. No non-Gemma fallback exists in code. |

Verified 2026-10-15 by listing models with the project key: the Gemini API returned exactly two Gemma 4 IDs, `gemma-4-26b-a4b-it` and `gemma-4-31b-it`. A real "hello" call succeeded on both.

**Why 31B:** the dense 31B instruction-tuned model is the largest Gemma 4 variant served by the API and the strongest at code generation/patching. **Observed caveat:** under load it returned HTTP 500/503 and took ~30 s; the 26B MoE answered in ~4 s. The provider retries with exponential backoff and can be configured (`GEMMA_MODEL`) to the 26B — still Gemma 4 only.

## Terms

- Gemma 4 model weights are released under the **Apache License 2.0** (Google Open Source Blog, "Gemma 4: Expanding the Gemmaverse with Apache 2.0": https://opensource.googleblog.com/2026/03/gemma-4-expanding-the-gemmaverse-with-apache-20.html).
- Hosted access through Google AI Studio / the Gemini API is additionally subject to the Gemini API Additional Terms of Service. TODO(verify): link and summarise the exact current terms page at M4.

## Prompting and evaluation

Filled in at M4 (prompt templates, `scripts/eval_prompts.py` results).
