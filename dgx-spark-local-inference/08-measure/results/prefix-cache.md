# Why the harness sends unique prompts

`--shared-prefix` exists so this can be demonstrated rather than asserted.

Identical 16,000-token prompt to every request, concurrency 2:

| Prompts | Wall time p50 |
| --- | ---: |
| identical (shared prefix) | 5.01 s |
| unique per request | 15.41 s |

Roughly threefold, and entirely vLLM's automatic prefix caching. A sweep run
with a shared prompt is measuring the cache, not the machine — it made
concurrency 2 look faster than concurrency 1.

Unique prompts are therefore the default, and every concurrency figure in the
article and in `concurrency.md` is a unique-prompt run.

This is not an argument against prefix caching, which is switched on in the
deployment and is worth a great deal in a real conversation where a system
prompt genuinely repeats. It is an argument against benchmarking with it warm
and calling the result hardware.
