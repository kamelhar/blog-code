# Step 6 — one endpoint, local first

LiteLLM in front of vLLM, speaking the OpenAI API on both sides.

| Model name | Goes to | Fallback |
| --- | --- | --- |
| `qwen3.6-35b` | local GPU | three Together AI models, only if the GPU cannot answer |
| `spark-only` | local GPU | **none** — fails rather than leave the machine |
| `cloud-*`, `heavy-*` | Together AI | none |
| `openrouter/<model>` | OpenRouter | a generated chain: cheaper models from the same provider, then other providers, then the local GPU |
| `*` (anything else) | local GPU | none |

There is no per-token price ceiling. An earlier version capped OpenRouter with
`max_price`; it refused frontier models outright, and with fallbacks only on
the default name those requests had nowhere to go. Spend is bounded by the
OpenRouter account's own credit limit instead.

`config.yaml` is the model list and fallback block; `deployment.yaml` runs it
with host networking beside vLLM. Vendor keys come from environment variables
(`TOGETHER_API_KEY`, `OPENROUTER_API_KEY`, `VLLM_API_KEY`); the router is the
only process that holds them.
