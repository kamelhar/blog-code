# Step 8 — measure it

Two tools, standard library only, for any OpenAI-compatible server:

```sh
export LLM_API_KEY=...

# concurrency: time to first token, per-stream decode, aggregate throughput
./llm-concurrency.py --url http://127.0.0.1:8000/v1/chat/completions \
    --model qwen3.6-35b --concurrency 1,2,4,8,16 --max-tokens 400

# single stream: decode rate by content type, TTFT against context size,
# and vLLM's speculative-decoding acceptance from /metrics
./decode-profile.py --url http://127.0.0.1:8000
```

Both fix the output length (`min_tokens` + `ignore_eos`), discard a warm-up and
report a median. The concurrency tool gives each request a unique prefix by
default so a prefix cache cannot flatter the run; `--shared-prefix` measures
the cache on purpose.

`results/` holds the runs behind the article's tables, with the protocol for each.
