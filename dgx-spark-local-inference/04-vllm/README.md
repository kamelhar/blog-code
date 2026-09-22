# Step 4 — vLLM

The Deployment that serves the model: host networking bound to `127.0.0.1`,
one time-sliced GPU, weights read from the host, and a startup probe that
allows up to 30 minutes for a load that takes about six. Every serving flag has
a one-line comment saying why it is there.

Needs a Secret `vllm-env` holding `VLLM_API_KEY`, the `nvidia` RuntimeClass
from step 2, and the weights from step 1 at the `hostPath`. Replace
`/home/YOUR-USER` first.
