# DGX Spark local inference — harness, manifest, results

Everything behind
[Running Local AI on a DGX Spark](https://blog.kamelhar.net/serving-an-llm-on-an-nvidia-dgx-spark/).

```
bench/     llm-concurrency.py, a generic harness for any OpenAI-compatible
           endpoint, plus the two site-specific originals that produced the
           numbers in the article
k8s/       the vLLM Deployment as it runs, home directory generalised
results/   the raw runs, with the protocol each was taken under
```

## What was measured, and on what

| | |
| --- | --- |
| Machine | DGX Spark, GB10 Grace Blackwell, ~128 GB unified memory, ~273 GB/s |
| Model | `Qwen3.6-35B-A3B`, NVFP4 quantisation, read from local disk |
| Server | `vllm/vllm-openai@sha256:c5fa18e5360a929262f55c697ea53d963288535fa0b238c0c47e9267a16e13b9` |
| Orchestration | k3s v1.36.4, containerd 2.3.4, NVIDIA device plugin v0.20.0 |
| Driver | open kernel module 580.173.02, aarch64 |
| Dates | 2026-08-21 through 2026-09-20 |

**On the model revision.** The weights were fetched once to local disk and
served from there by directory name. I did not record the source repository
revision at fetch time, so this cannot pin a commit — a real gap, and the
reason the server image is pinned by digest instead of by tag. Treat the model
as "an NVFP4 quantisation of Qwen3.6-35B-A3B as published in August 2026"
rather than as a hash you can diff against.

## The tool you can actually use

`bench/llm-concurrency.py` is the generic one. It takes any OpenAI-compatible
chat-completions endpoint and sweeps concurrency against it — vLLM, SGLang,
llama.cpp, Ollama, or a hosted vendor. Nothing in it is specific to this
machine.

```sh
export LLM_API_KEY=...
./bench/llm-concurrency.py \
    --url http://127.0.0.1:8000/v1/chat/completions \
    --model my-model --concurrency 1,2,4,8,16 --max-tokens 400
```

```
 conc   agg tok/s   tok/s/stream   ttft p50   ttft p95   total p95   err
    1        99.0           99.0      0.23s      0.23s       4.08s     0
    8       330.1           41.3      0.51s      1.12s      10.4s      0
   16       462.0           28.9      0.94s      2.63s      14.2s      0
```

Standard library only, no dependencies. Four things it controls for, each
because getting it wrong produces a confident wrong number:

- **Fixed output length.** `min_tokens` and `ignore_eos`, so a terse model
  cannot score well for saying less. `--allow-early-stop` for servers that
  reject those.
- **Unique prompt per request, by default.** A server with prefix caching
  answers a repeated prompt from cache, so firing the identical prompt N times
  measures the cache. `--shared-prefix` measures it deliberately.
- **A discarded warm-up, and a median of repeats.** First contact pays for
  connection setup and a cold cache.
- **Per-stream measured, not derived.** The per-stream column is each
  request's own decode rate, not aggregate divided by concurrency. Both are
  reported because they answer different questions.

`--json` writes every round for your own analysis. The API key is never
written to it.

## The two that produced the article's numbers

`bench/inference-bench.py` and `bench/decode-profile.py` are the originals, and
they are **site-specific on purpose**: endpoints and key paths are hardcoded
for this machine, and they will not run anywhere else without editing. They are
here so the numbers in `results/` can be checked against the thing that
produced them, not because they are reusable. If you want to run something, run
`llm-concurrency.py`.

## What these runs do not show

- **No per-request latency percentiles in the published tables.** The harness
  computes p50 and p95; the runs quoted in the article kept aggregate
  throughput only. The per-stream column in the article is aggregate divided
  by concurrency, which is a mean and not a measured per-request rate.
- **An idle GPU.** Speech-to-text and text-to-speech were scaled down. Real
  contention costs more than these tables show.
- **No model-quality evaluation.** Every number here is about how fast tokens
  come out, not about whether the answers are good, whether tool calls
  succeed, or what NVFP4 quantisation costs in quality. That is a real gap
  between the article's opening question and its evidence.
- **No expert-overlap measurement.** The explanation for why batching scales
  assumes one weight read serves the batch. In a Mixture-of-Experts model
  different sequences can route to different experts, so actual memory traffic
  under a batch was not measured.
