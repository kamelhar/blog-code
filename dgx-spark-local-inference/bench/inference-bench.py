#!/usr/bin/env python3
"""Measure what each inference tier actually delivers under load.

WHY THIS EXISTS
    The tiering was designed from two numbers taken by hand — vLLM prefills
    around 3,000 tok/s, Ollama around 450 — and one of them overturned an
    assumption everyone starts with: that the small model is the fast one. It
    is not, for the prompts this box actually sends. Decisions that big should
    not rest on numbers nobody can reproduce.

    So this sweeps concurrency against each tier and reports the shape of the
    curve, not a single figure. What matters for routing is not peak
    throughput; it is where latency turns the corner, and what a request costs
    when it arrives while the box is already busy.

WHAT IT MEASURES, PER TIER AND CONCURRENCY
    ttft      time to first token — what the person feels
    total     wall time of the whole answer
    decode    output tokens per second, per stream
    aggregate output tokens per second across all streams
    errors    anything that did not answer

PROMPT PROFILES
    short   a greeting. Flatters everyone; included as a floor.
    agent   ~8k tokens of context, which is what a real turn looks like once
            the system prompt, skills and history are in front of it. This is
            the profile that decides the routing.

    Both matter: the gap between them IS the prefill cost, and prefill is
    where the tiers separate.

USAGE
    inference-bench.py --tier vllm --tier ollama --concurrency 1,2,4,8,16
    inference-bench.py --tier together --profile agent      # a free cloud tier

Runs on the box: two of the three tiers listen on loopback only.
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

# Each tier: where it listens, what to call it, and how to authenticate.
# Ollama and vLLM both speak the OpenAI chat API, which is what makes a single
# harness honest — the same request shape reaches all three.
TIERS: dict[str, dict] = {
    "vllm": {
        "url": "http://127.0.0.1:8000/v1/chat/completions",
        "model": "qwen3.6-35b",
        "key_file": "/etc/spark/vllm.env",
        "key_name": "VLLM_API_KEY",
        "label": "35B  vLLM (NVFP4, GPU)",
    },
    "ollama": {
        "url": "http://127.0.0.1:11434/v1/chat/completions",
        "model": "qwen3:4b",
        "key": "ollama",
        "label": "4B   Ollama (GPU)",
    },
    "together": {
        "url": "https://api.together.xyz/v1/chat/completions",
        "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free",
        "env": "TOGETHER_API_KEY",
        "label": "70B  Together (free tier, cloud)",
    },
}

FILLER = (
    "The Spark serves a 35B mixture-of-experts model in NVFP4 on a GB10 with "
    "121 GB of unified memory. Requests arrive from WhatsApp, are debounced, "
    "and land in a session lane that serialises turns per conversation. "
)


def read_key(tier: dict) -> str:
    if "key" in tier:
        return tier["key"]
    if tier.get("env"):
        value = os.environ.get(tier["env"], "")
        if not value:
            raise SystemExit(f"{tier['env']} is not set; export it or skip this tier")
        return value
    path = tier["key_file"]
    try:
        with open(path) as handle:
            for line in handle:
                name, _, value = line.partition("=")
                if name.strip() == tier["key_name"]:
                    return value.strip().strip("\"'")
    except PermissionError:
        raise SystemExit(f"cannot read {path} — run this with sudo") from None
    raise SystemExit(f"{tier['key_name']} not found in {path}")


# The same job in both profiles, so the ONLY difference is prompt length and
# the gap between the two profiles is the prefill cost. It also asks for a
# real answer: with a two-token reply the decode rate measures nothing.
TASK = "Explain in about 120 words why unified memory helps local LLM inference."


def build_prompt(profile: str) -> str:
    if profile == "short":
        return TASK
    # ~16k tokens of context in front of the identical task — which is what a
    # real agent turn looks like once system prompt, skills and history are
    # loaded. This profile is the one that decides routing.
    return "Context to read but not summarise:\n" + FILLER * 300 + "\n\n" + TASK


def one_request(
    tier: dict, key: str, prompt: str, max_tokens: int, thinking: bool, salt: str = ""
) -> dict:
    """Stream one completion, timing first token and first answer separately.

    Qwen3.6 is a reasoning model: it streams `reasoning` deltas before any
    `content`. Both are generated tokens and both cost GPU, so both count
    toward throughput — but only content is an answer. Measuring solely on
    content made every request look like a failure at a 64-token cap, because
    the budget went entirely on thinking.
    """
    body: dict = {
        "model": tier["model"],
        "messages": [{"role": "user", "content": salt + prompt}],
        "max_tokens": max_tokens,
        "temperature": 0,
        "stream": True,
    }
    if not thinking:
        # How vLLM exposes Qwen's thinking switch. Harmless elsewhere.
        body["chat_template_kwargs"] = {"enable_thinking": False}
    payload = json.dumps(body).encode()
    request = urllib.request.Request(tier["url"], data=payload, method="POST")
    request.add_header("Authorization", f"Bearer {key}")
    request.add_header("Content-Type", "application/json")

    started = time.perf_counter()
    first_at: float | None = None
    first_answer_at: float | None = None
    tokens = 0
    answer_tokens = 0
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                body = line[5:].strip()
                if body == "[DONE]":
                    break
                try:
                    chunk = json.loads(body)
                except json.JSONDecodeError:
                    continue
                delta = (chunk.get("choices") or [{}])[0].get("delta", {})
                # "reasoning" is vLLM's field; "reasoning_content" is the
                # name some builds use. Either way it is a generated token.
                thought = delta.get("reasoning") or delta.get("reasoning_content")
                answer = delta.get("content")
                if thought or answer:
                    if first_at is None:
                        first_at = time.perf_counter()
                    tokens += 1
                if answer:
                    if first_answer_at is None:
                        first_answer_at = time.perf_counter()
                    answer_tokens += 1
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"error": str(exc)[:120]}

    done = time.perf_counter()
    if first_at is None:
        return {"error": "nothing streamed"}
    return {
        "ttft": first_at - started,
        # None when the whole budget went on thinking — which is a result, not
        # an error, and one worth seeing rather than averaging away.
        "ttfa": (first_answer_at - started) if first_answer_at else None,
        "total": done - started,
        "tokens": tokens,
        "answer_tokens": answer_tokens,
        "decode": tokens / max(done - first_at, 1e-6),
    }


def run_level(
    tier: dict,
    key: str,
    prompt: str,
    concurrency: int,
    max_tokens: int,
    thinking: bool,
    unique: bool,
) -> dict:
    """Fire `concurrency` requests at once and collect what came back."""
    results: list[dict] = []
    lock = threading.Lock()

    def worker(index: int) -> None:
        # A unique leading string per request. vLLM caches shared prefixes, so
        # firing the identical 16k prompt N times measures the cache, not the
        # box: it made concurrency 2 look faster than concurrency 1. Real
        # conversations differ from each other, so differ here too.
        salt = f"[req {index}-{time.time_ns()}] " if unique else ""
        outcome = one_request(tier, key, prompt, max_tokens, thinking, salt)
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(concurrency)]
    wall_started = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    wall = time.perf_counter() - wall_started

    good = [r for r in results if "error" not in r]
    errors = [r for r in results if "error" in r]
    if not good:
        return {"concurrency": concurrency, "errors": len(errors), "why": errors[0]["error"]}

    ttfts = sorted(r["ttft"] for r in good)
    answered = [r["ttfa"] for r in good if r["ttfa"] is not None]
    return {
        "concurrency": concurrency,
        "ok": len(good),
        "errors": len(errors),
        "ttft_p50": statistics.median(ttfts),
        "ttft_p95": ttfts[min(len(ttfts) - 1, int(len(ttfts) * 0.95))],
        "ttfa_p50": statistics.median(answered) if answered else None,
        "answered": len(answered),
        "total_p50": statistics.median(r["total"] for r in good),
        "decode_mean": statistics.mean(r["decode"] for r in good),
        "aggregate": sum(r["tokens"] for r in good) / wall,
        "wall": wall,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", action="append", choices=list(TIERS), required=True)
    parser.add_argument("--concurrency", default="1,2,4,8")
    parser.add_argument("--profile", action="append", choices=["short", "agent"])
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument(
        "--thinking",
        action="store_true",
        help="leave the reasoning phase on (default: off, matching how the agent runs)",
    )
    parser.add_argument(
        "--shared-prefix",
        action="store_true",
        help="send the identical prompt to every request, measuring vLLM's prefix cache",
    )
    parser.add_argument("--json", metavar="PATH", help="also write raw results here")
    args = parser.parse_args(argv)

    profiles = args.profile or ["short", "agent"]
    levels = [int(x) for x in args.concurrency.split(",")]
    collected: dict = {}

    for tier_name in args.tier:
        tier = TIERS[tier_name]
        key = read_key(tier)
        print(f"\n=== {tier['label']} ===")
        for profile in profiles:
            prompt = build_prompt(profile)
            print(f"\n  profile: {profile}  (~{len(prompt) // 4} prompt tokens)")
            print(
                f"    {'conc':>4}  {'ok':>3} {'err':>3}  {'ttft p50':>9} {'ttft p95':>9}"
                f"  {'answer':>8}  {'total p50':>9}  {'decode/s':>9}  {'agg tok/s':>9}"
            )
            for level in levels:
                row = run_level(
                    tier, key, prompt, level, args.max_tokens, args.thinking, not args.shared_prefix
                )
                collected[f"{tier_name}/{profile}/{level}"] = row
                if "ttft_p50" not in row:
                    print(f"    {level:>4}  ALL FAILED: {row.get('why')}")
                    continue
                answer = f"{row['ttfa_p50']:>7.2f}s" if row["ttfa_p50"] else "   none"
                print(
                    f"    {level:>4}  {row['ok']:>3} {row['errors']:>3}"
                    f"  {row['ttft_p50']:>8.2f}s {row['ttft_p95']:>8.2f}s"
                    f"  {answer:>8}  {row['total_p50']:>8.2f}s  {row['decode_mean']:>8.1f}"
                    f"  {row['aggregate']:>9.1f}"
                )

    if args.json:
        with open(args.json, "w") as handle:
            json.dump(collected, handle, indent=2)
        print(f"\nraw results: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
