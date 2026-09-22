#!/usr/bin/env python3
"""Measure what an OpenAI-compatible endpoint delivers under concurrency.

Points at any server speaking /v1/chat/completions -- vLLM, SGLang, llama.cpp,
Ollama, or a hosted vendor -- and sweeps concurrency against it, reporting time
to first token, per-stream decode rate and aggregate throughput.

    export LLM_API_KEY=...
    ./llm-concurrency.py --url http://127.0.0.1:8000/v1/chat/completions \\
        --model my-model --concurrency 1,2,4,8,16

WHAT IT CONTROLS FOR, AND WHY EACH ONE MATTERS

  Fixed output length.  Every request is asked for exactly --max-tokens, with
  min_tokens and ignore_eos where the server honours them. Without this a model
  that stops early scores well for being terse, and a run is not comparable with
  the next one.

  Unique prefixes, by default.  Servers with prefix caching answer a repeated
  prompt from cache, so firing the identical prompt N times measures the cache
  rather than the machine -- in one run it made concurrency 2 look faster than
  concurrency 1. Each request therefore carries a unique salt. --shared-prefix
  turns that off, which is how you measure the cache deliberately.

  Repeats and a discarded warm-up.  First contact pays for connection setup and
  a cold cache. The default is one warm-up plus three measured rounds, reported
  as the median.

  Percentiles, not just means.  Aggregate throughput hides what an individual
  request experiences once the server is saturated. p95 time to first token is
  usually the number that decides whether people can sit behind it.

WHAT IT DOES NOT DO

  It measures speed, never quality. Nothing here checks that answers are
  correct, that tool calls parse, or what quantisation cost you.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request

# Roughly four characters per token for English prose. Good enough to size a
# synthetic prompt; the exact count is reported by the server in `usage`.
_FILLER = (
    "The quarterly review covered infrastructure spend, headcount and the "
    "migration timeline agreed in the previous cycle. "
)


def build_prompt(prompt_tokens: int) -> str:
    """A synthetic prompt of approximately `prompt_tokens` tokens."""
    if prompt_tokens <= 0:
        return "Hello."
    reps = max(1, (prompt_tokens * 4) // len(_FILLER))
    return (
        _FILLER * reps
        + "\n\nSummarise the material above in careful, complete prose."
    )


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def one_request(cfg, prompt: str, salt: str) -> dict:
    """One streamed completion. -> {ttft, total, out_tokens, decode, error}."""
    body = {
        "model": cfg.model,
        "messages": [{"role": "user", "content": salt + prompt}],
        "max_tokens": cfg.max_tokens,
        "temperature": cfg.temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if not cfg.allow_early_stop:
        # Not every server implements these; the ones that do stop a terse model
        # from scoring well for saying less.
        body["min_tokens"] = cfg.max_tokens
        body["ignore_eos"] = True

    req = urllib.request.Request(
        cfg.url, data=json.dumps(body).encode(), method="POST"
    )
    req.add_header("Content-Type", "application/json")
    if cfg.key:
        req.add_header("Authorization", f"Bearer {cfg.key}")

    started = time.perf_counter()
    ttft = None
    counted = 0
    usage_tokens = None
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if chunk.get("usage"):
                    usage_tokens = chunk["usage"].get("completion_tokens")
                for choice in chunk.get("choices") or []:
                    piece = (choice.get("delta") or {}).get("content") or ""
                    if piece:
                        if ttft is None:
                            ttft = time.perf_counter() - started
                        counted += 1
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}

    total = time.perf_counter() - started
    out_tokens = usage_tokens or counted
    if ttft is None or not out_tokens:
        return {"error": "no content streamed"}
    # Decode rate excludes prefill: it is the tokens after the first, over the
    # time after the first. Including prefill would make a long prompt look like
    # a slow model.
    decode = (out_tokens - 1) / (total - ttft) if total > ttft else float("nan")
    return {"ttft": ttft, "total": total, "out_tokens": out_tokens, "decode": decode}


def sweep_once(cfg, prompt: str, concurrency: int) -> dict:
    """Fire `concurrency` requests at once and collect what came back."""
    results: list[dict] = []
    lock = threading.Lock()

    def worker(index: int) -> None:
        salt = "" if cfg.shared_prefix else f"[req {index}-{time.time_ns()}] "
        outcome = one_request(cfg, prompt, salt)
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(concurrency)]
    wall_start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - wall_start

    ok = [r for r in results if "error" not in r]
    errors = [r["error"] for r in results if "error" in r]
    if not ok:
        return {"concurrency": concurrency, "errors": errors, "ok": 0}
    produced = sum(r["out_tokens"] for r in ok)
    return {
        "concurrency": concurrency,
        "ok": len(ok),
        "errors": errors,
        # Aggregate is total tokens over the wall time of the whole batch, which
        # is what the server delivered. Per-stream is measured per request, not
        # this number divided by concurrency -- they are not the same thing.
        "aggregate_tok_s": produced / wall if wall else float("nan"),
        "decode_p50": statistics.median(r["decode"] for r in ok),
        "ttft_p50": percentile([r["ttft"] for r in ok], 0.50),
        "ttft_p95": percentile([r["ttft"] for r in ok], 0.95),
        "total_p50": percentile([r["total"] for r in ok], 0.50),
        "total_p95": percentile([r["total"] for r in ok], 0.95),
    }


def median_of(rows: list[dict], key: str) -> float:
    vals = [r[key] for r in rows if key in r]
    return statistics.median(vals) if vals else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Concurrency sweep against an OpenAI-compatible endpoint.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--url", required=True, help="full chat-completions URL")
    ap.add_argument("--model", required=True, help="model name the server expects")
    ap.add_argument("--key-env", default="LLM_API_KEY",
                    help="environment variable holding the API key (default: LLM_API_KEY)")
    ap.add_argument("--key-file", help="read the key from this file instead")
    ap.add_argument("--concurrency", default="1,2,4,8",
                    help="comma-separated levels to sweep (default: 1,2,4,8)")
    ap.add_argument("--max-tokens", type=int, default=400)
    ap.add_argument("--prompt-tokens", type=int, default=500,
                    help="approximate synthetic prompt size (default: 500)")
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--repeat", type=int, default=3, help="measured rounds, reported as the median")
    ap.add_argument("--warmup", type=int, default=1, help="rounds to discard first")
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--shared-prefix", action="store_true",
                    help="send the identical prompt to every request, measuring the server's prefix cache")
    ap.add_argument("--allow-early-stop", action="store_true",
                    help="do not force min_tokens/ignore_eos (use against servers that reject them)")
    ap.add_argument("--json", metavar="PATH", help="also write raw results here")
    cfg = ap.parse_args()

    cfg.key = ""
    if cfg.key_file:
        try:
            cfg.key = open(cfg.key_file).read().strip()
        except OSError as exc:
            print(f"cannot read --key-file: {exc}", file=sys.stderr)
            return 2
    else:
        cfg.key = os.getenv(cfg.key_env, "")

    levels = [int(x) for x in cfg.concurrency.split(",") if x.strip()]
    prompt = build_prompt(cfg.prompt_tokens)

    print(f"endpoint   {cfg.url}")
    print(f"model      {cfg.model}")
    print(f"protocol   {cfg.max_tokens} output tokens"
          f"{'' if cfg.allow_early_stop else ', min_tokens+ignore_eos'}"
          f", temperature {cfg.temperature}")
    print(f"prompt     ~{cfg.prompt_tokens} tokens, "
          f"{'shared across requests (measuring the cache)' if cfg.shared_prefix else 'unique per request'}")
    print(f"rounds     {cfg.warmup} warm-up discarded, median of {cfg.repeat}\n")

    header = f"{'conc':>5}  {'agg tok/s':>10}  {'tok/s/stream':>13}  {'ttft p50':>9}  {'ttft p95':>9}  {'total p95':>10}  {'err':>4}"
    print(header)
    print("-" * len(header))

    collected = {}
    for level in levels:
        for _ in range(max(0, cfg.warmup)):
            sweep_once(cfg, prompt, level)
        rounds = [sweep_once(cfg, prompt, level) for _ in range(max(1, cfg.repeat))]
        errors = sum(len(r.get("errors") or []) for r in rounds)
        if not any(r.get("ok") for r in rounds):
            print(f"{level:>5}  {'all failed':>10}  {'':>13}  {'':>9}  {'':>9}  {'':>10}  {errors:>4}")
            first = next((r["errors"][0] for r in rounds if r.get("errors")), "")
            print(f"        {first}")
            collected[level] = rounds
            continue
        print(f"{level:>5}  {median_of(rounds,'aggregate_tok_s'):>10.1f}  "
              f"{median_of(rounds,'decode_p50'):>13.1f}  "
              f"{median_of(rounds,'ttft_p50'):>8.2f}s  {median_of(rounds,'ttft_p95'):>8.2f}s  "
              f"{median_of(rounds,'total_p95'):>9.2f}s  {errors:>4}")
        collected[level] = rounds

    if cfg.json:
        with open(cfg.json, "w") as fh:
            json.dump({"config": {k: v for k, v in vars(cfg).items() if k != "key"},
                       "rounds": collected}, fh, indent=2, default=str)
        print(f"\nraw results -> {cfg.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
