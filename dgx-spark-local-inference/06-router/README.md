# Step 6 — one endpoint, local first

LiteLLM in front of vLLM, speaking the OpenAI API on both sides.

| Model name | Goes to | Fallback |
| --- | --- | --- |
| `qwen3.6-35b` | local GPU | two Together AI models, if the GPU cannot answer |
| `spark-only` | local GPU | **none** — fails rather than leave the machine |
| `cloud-*`, `heavy-*` | Together AI | none |
| `openrouter/*` | OpenRouter | none |
| `*` (anything else) | local GPU | none |

`config.yaml` is the model list and fallback block; `deployment.yaml` runs it
with host networking beside vLLM. Vendor keys come from environment variables
(`TOGETHER_API_KEY`, `OPENROUTER_API_KEY`, `VLLM_API_KEY`); the router is the
only process that holds them.
