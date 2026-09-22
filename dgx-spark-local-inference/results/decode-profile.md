# Single-stream decode — 2026-08-30

Harness: `bench/decode-profile.py`. Protocol: 400 tokens, `min_tokens` +
`ignore_eos`, temperature 0, median of 3, warm-up discarded.

## Decode rate depends on what is being generated

| Profile | tok/s |
| --- | ---: |
| chat (prose, thinking on) | 105.9 |
| chat, thinking off | 88.8 |
| code | 110.6 |

At temperature 0.7, chat measures 90–99 tok/s. Temperature costs about 5
percent, far less than the spread between profiles. The article's headline
figure of 99 is the temperature-0.7 chat case, which is what real clients
send.

**Thinking on is faster per token than thinking off**, which is the opposite
of the intuition. It is a drafter effect: reasoning text is repetitive and
self-similar, so multi-token prediction lands more of each drafted block than
it does on a polished final answer. That is a per-token credit, not a total
one — extended thinking still costs far more tokens overall.

## Multi-token prediction, from vLLM's own counters

Cumulative since service start, at `num_speculative_tokens: 3`:

| | |
| --- | ---: |
| Accept length | 2.84 tokens per target forward pass |
| Drafted tokens accepted | 62% |
| Per-position acceptance | 78% / 60% / 46% |

The third draft slot lands under half the time while making every verify batch
bigger, which is why this is left at 3 rather than raised.
