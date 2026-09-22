#!/usr/bin/env python3
"""Measure single-stream decode rate, and time to first token against context size.

    ./decode-profile.py --url http://127.0.0.1:8000        # a vLLM server
    LLM_API_KEY=... ./decode-profile.py --url http://host:8000 --temperature 0.7

WHY THIS EXISTS
    `llm-concurrency.py` answers how a server behaves under concurrency. It
    says nothing about the number people actually quote at each other — "how many tokens per
    second does the Spark do" — and that number turns out to depend almost
    entirely on what is being generated.

    A decode figure written down by hand once drifted ~1.9x from what its own
    stated protocol produced two weeks later, and nobody could tell, because the
    protocol lived in a comment and the measurement lived nowhere. Hence this.

WHAT IT MEASURES
    decode    output tokens per second, single stream, forced to a fixed
              length with min_tokens + ignore_eos so a chatty or terse model
              cannot flatter itself on length
    ttft      time to first token, streamed, swept over context size —
              this is prefill, and prefill is what an agent loop pays 12x

PROFILES, AND WHY THE SPREAD MATTERS
    chat      prose. The hardest thing for a speculative drafter to guess,
              and therefore the slowest. This is reel's final answer and
              Chispa's replies.
    code      a function with a docstring. Boilerplate-dense, so the drafter
              lands most of its block. The number vendors quote.
    thinking  the same chat prompt with the reasoning channel on, which is
              what reel actually streams. Measured separately because it is
              NOT the average of the two: reasoning text is repetitive, so it
              drafts BETTER than the prose it precedes.

    Quoting one of these as "the" decode rate is how the headers drifted.
    Record all three or none.

ACCEPTANCE
    Reads vLLM's own spec-decode counters when they are exposed, so the
    speedup is attributable rather than assumed. These are cumulative since
    the service started and mix in every other client on the box — treat them
    as a running average, not as this run's acceptance.

USAGE
    Wherever the server is reachable. A vLLM server bound to loopback means
    running this on the same machine, or through an ssh tunnel to it:

        LLM_API_KEY=... ./decode-profile.py --url http://127.0.0.1:8000
        ./decode-profile.py --url http://127.0.0.1:8000 --temperature 0.7

    The acceptance counters come from vLLM's /metrics; on another server that
    line is simply skipped.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path

# Set from --url in main(): the server root, without /v1.
ENDPOINT = "http://127.0.0.1:8000"

#: Fixed generation length. Long enough that per-request overhead is noise,
#: short enough that a sweep stays under a minute per profile.
GEN_TOKENS = 400

#: Median of 3, warm-up discarded — the protocol the unit headers claim.
#: A variable-length probe is far too noisy to tune on; it once produced a
#: spurious 15.2 tok/s reading on this box.
REPEATS = 3

PROFILES = {
    "chat": (
        "Recommend a film for tonight and explain in a few paragraphs why it "
        "suits a rainy evening.",
        True,
    ),
    "chat-nothink": (
        "Recommend a film for tonight and explain in a few paragraphs why it "
        "suits a rainy evening.",
        False,
    ),
    "code": (
        "Write a Python function that parses an ISO8601 duration string into "
        "seconds, with a docstring and tests.",
        True,
    ),
}

#: Context sizes for the prefill sweep. 32k is included because that is where
#: a long reel turn lands and where TTFT stops being linear.
CONTEXT_SWEEP = (500, 8000, 32000)

_FILLER = "The library holds entries with title, year, genre, runtime and a short synopsis. "


def api_key(env_var: str, key_file: str | None) -> str:
    """The server's API key, from a file or an environment variable. An
    unauthenticated server works too: an empty key sends no credential."""
    if key_file:
        return Path(key_file).read_text().strip().strip("\"'")
    return os.environ.get(env_var, "")


def auth(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"} if key else {}


def post(key: str, body: dict, stream: bool = False):
    req = urllib.request.Request(
        f"{ENDPOINT}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={**auth(key), "Content-Type": "application/json"},
    )
    return urllib.request.urlopen(req, timeout=600)


def served_model(key: str) -> str:
    req = urllib.request.Request(
        f"{ENDPOINT}/v1/models", headers=auth(key)
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["data"][0]["id"]


def body_for(model: str, messages: list[dict], thinking: bool, temp: float) -> dict:
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": GEN_TOKENS,
        "min_tokens": GEN_TOKENS,
        "ignore_eos": True,
        "temperature": temp,
    }
    if not thinking:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    return body


def decode_rate(key: str, model: str, prompt: str, thinking: bool, temp: float) -> float:
    """Tokens per second for a fixed-length, non-streamed completion."""
    msgs = [{"role": "user", "content": prompt}]
    t0 = time.time()
    with post(key, body_for(model, msgs, thinking, temp)) as r:
        payload = json.load(r)
    elapsed = time.time() - t0
    return payload["usage"]["completion_tokens"] / elapsed


def ttft(key: str, model: str, ctx_tokens: int, temp: float) -> tuple[float, float]:
    """(ttft, decode) for a streamed turn with `ctx_tokens` of context."""
    filler = _FILLER * max(1, ctx_tokens // 18)
    msgs = [
        {"role": "system", "content": "You are a film agent."},
        {"role": "user", "content": filler + "\n\nRecommend one film and say why."},
    ]
    body = body_for(model, msgs, True, temp)
    body["stream"] = True
    t0 = time.time()
    first = None
    with post(key, body, stream=True) as r:
        for line in r:
            if not line.startswith(b"data: ") or line.strip() == b"data: [DONE]":
                continue
            if first is None:
                first = time.time() - t0
    total = time.time() - t0
    return first or total, GEN_TOKENS / (total - (first or 0))


def acceptance(key: str) -> str | None:
    """vLLM's cumulative spec-decode counters, as accept length + rate."""
    req = urllib.request.Request(f"{ENDPOINT}/metrics", headers=auth(key))
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read().decode()
    except urllib.error.URLError:
        return None
    vals = {}
    for name in ("num_drafts", "num_draft_tokens", "num_accepted_tokens"):
        for line in text.splitlines():
            if line.startswith(f"vllm:spec_decode_{name}_total"):
                vals[name] = float(line.rsplit(" ", 1)[1])
                break
    if len(vals) != 3 or not vals["num_drafts"]:
        return None
    accept_len = 1 + vals["num_accepted_tokens"] / vals["num_drafts"]
    rate = vals["num_accepted_tokens"] / vals["num_draft_tokens"]
    return f"accept length {accept_len:.2f} tok/forward, {rate:.0%} of drafts accepted"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="0 for the tuning protocol; 0.7 for what clients actually send",
    )
    ap.add_argument("--url", default="http://127.0.0.1:8000",
                    help="server root, without /v1 (default: http://127.0.0.1:8000)")
    ap.add_argument("--model", help="model name; default: the first the server lists")
    ap.add_argument("--key-env", default="LLM_API_KEY", help="env var holding the API key")
    ap.add_argument("--key-file", help="read the API key from this file instead")
    ap.add_argument("--skip-prefill", action="store_true")
    args = ap.parse_args()

    global ENDPOINT
    ENDPOINT = args.url.rstrip("/").removesuffix("/v1")
    key = api_key(args.key_env, args.key_file)
    model = args.model or served_model(key)
    print(f"model           {model} at {ENDPOINT}")
    print(
        f"protocol        {GEN_TOKENS} tok, min_tokens+ignore_eos, "
        f"temp {args.temperature}, median of {REPEATS}, warm-up discarded\n"
    )

    # Warm-up. The first request after an idle period pays for a cold cache
    # and would drag the median down for whichever profile ran first.
    decode_rate(key, model, PROFILES["chat"][0], True, args.temperature)

    print("decode, single stream")
    for name, (prompt, thinking) in PROFILES.items():
        runs = [decode_rate(key, model, prompt, thinking, args.temperature) for _ in range(REPEATS)]
        print(
            f"  {name:14s} {statistics.median(runs):6.1f} tok/s"
            f"   (min {min(runs):.1f}, max {max(runs):.1f})"
        )

    if not args.skip_prefill:
        print("\nprefill, streamed")
        for ctx in CONTEXT_SWEEP:
            # Run each size twice: the second pays a warm prefix, which is
            # what a multi-turn conversation actually gets.
            cold_ttft, _ = ttft(key, model, ctx, args.temperature)
            warm_ttft, warm_decode = ttft(key, model, ctx, args.temperature)
            print(
                f"  ~{ctx // 1000 or 0.5}k ctx     TTFT {cold_ttft:5.2f}s cold"
                f" / {warm_ttft:5.2f}s warm prefix   decode {warm_decode:6.1f} tok/s"
            )

    acc = acceptance(key)
    if acc:
        print(f"\nspec decode     {acc}")
        print("                (cumulative since service start, all clients)")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise SystemExit(f"the server refused the credential ({e.code}): "
                             "set LLM_API_KEY, or pass --key-env / --key-file")
        raise SystemExit(f"{e.url}: HTTP {e.code} {e.reason}")
    except urllib.error.URLError as e:
        raise SystemExit(f"cannot reach the server: {e.reason}")
