# Concurrency sweeps

Harness: `bench/inference-bench.py`. Protocol: 400 output tokens, `min_tokens`
+ `ignore_eos` so every request decodes the same amount, unique leading string
per request so no two share a cached prefix.

## Sweep A — 2026-09-20, `--max-num-seqs 8`

The run that showed the cap was the limit rather than the hardware.

| Concurrency | Aggregate tok/s |
| ---: | ---: |
| 1 | 98.2 |
| 4 | 219.5 |
| 8 | 353.8 |
| 12 | 295.5 |

Twelve streams are **slower in aggregate** than eight. Four of them are
queued behind a cap of eight, so the extra load buys nothing and costs
scheduling. Hardware saturating does not look like this; it flattens rather
than falls.

## Sweep B — 2026-09-20, `--max-num-seqs 16`

After raising the cap, same protocol.

| Concurrency | Aggregate tok/s | Aggregate ÷ streams |
| ---: | ---: | ---: |
| 1 | 99 | 99 |
| 8 | 304–354 | 38–44 |
| 16 | 459–469 | 29 |

Peak aggregate moved up about 31 percent against sweep A. The third column is
arithmetic, not a separately measured per-request rate.

## Time to first token, by prompt size

| Prompt tokens | TTFT |
| ---: | ---: |
| 500 | 0.23 s |
| 4,000 | 0.81 s |
| 16,000 | 0.80–2.80 s |
| 40,000 | 6.23 s |

The spread at 16,000 is cache state: the low end is a warm prefix, the high
end a cold one.
