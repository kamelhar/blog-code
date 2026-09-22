# Local inference on a DGX Spark — the code

The configuration and tools behind
[Building local inference on a DGX Spark](https://blog.kamelhar.net/serving-an-llm-on-an-nvidia-dgx-spark/),
one folder per step, in the order you build the stack. Each file is what runs,
with private addresses replaced by placeholders.

| Step | Folder | What is in it |
| --- | --- | --- |
| 1. Pick the model the memory rewards | [`01-model/`](01-model/) | the pinned model (repo, revision, every file's SHA-256) and a verifier |
| 2. Kubernetes on the host | [`02-host/`](02-host/) | NVIDIA container runtime and k3s install scripts |
| 3. Make one GPU look like four | [`03-gpu-sharing/`](03-gpu-sharing/) | NVIDIA device plugin with time-slicing |
| 4. Serve it with vLLM | [`04-vllm/`](04-vllm/) | the vLLM Deployment, every flag commented |
| 5. Deliver it without kubectl | [`05-delivery/`](05-delivery/) | Argo CD ApplicationSets and the five-line marker file |
| 6. One endpoint in front, local first | [`06-router/`](06-router/) | LiteLLM model list, fallbacks, and its Deployment |
| 7. Web search without a model socket | [`07-web-search/`](07-web-search/) | agent config wiring a SearXNG MCP server |
| 8. Measure it | [`08-measure/`](08-measure/) | concurrency and decode benchmarks, and the raw results |

## What you need

- A DGX Spark, or any single-GPU Linux box with the NVIDIA driver installed.
- About 22 GB of disk for the model weights.
- For step 5, a git repository Argo CD can read; the manifests work with plain
  `kubectl apply -k` too if you skip GitOps.

## The model

`nvidia/Qwen3.6-35B-A3B-NVFP4` at revision `491c2f1ea524c639598bf8fa787a93fed5a6fbce`,
served by `vllm/vllm-openai@sha256:c5fa18e5360a929262f55c697ea53d963288535fa0b238c0c47e9267a16e13b9`.
Both are pinned, so a rebuild serves exactly what was measured.

## Placeholders to replace

| Placeholder | Where | Meaning |
| --- | --- | --- |
| `/home/YOUR-USER/models` | `04-vllm/deployment.yaml` | where the weights live on the host |
| `https://github.com/YOUR-ORG/YOUR-GITOPS-REPO.git` | `05-delivery/applicationsets.yaml` | your GitOps repository |
| `ROUTER-HOST`, `SEARXNG-HOST` | `07-web-search/opencode.json` | where the router and search service answer |
| `LAN_ROUTER_IP`, `CONTROL_PLANE_IP` | environment, for `02-host/40-k3s.sh` | your gateway, and the host allowed to reach the k3s API |

Secrets are referenced by name only (`vllm-env`, the router's vendor keys). Create
them yourself; nothing here contains a credential.
