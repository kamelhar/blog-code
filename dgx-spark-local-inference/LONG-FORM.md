# The long version

The [published post](https://blog.kamelhar.net/serving-an-llm-on-an-nvidia-dgx-spark/)
is about 1,200 words and makes one argument: the decision about whether a prompt
may leave belongs on the machine holding the GPU.

This is the draft it was cut from — around 7,000 words covering the whole build.
It is kept because the detail is real and someone may want it, not because it is
a better post. It was four articles stapled together, which is why it needed a
table of contents.

What is here and not in the post:

- k3s on the host, and making one GPU schedulable (time-slicing versus MPS,
  and where that decision actually lives)
- vLLM as a Kubernetes workload, flag by flag
- delivery: how workloads arrive without anyone running `kubectl`
- reaching the open web over MCP without the model opening a connection
- the full benchmark protocol, and three beliefs an experiment contradicted

---

A DGX Spark puts 128 GB of unified memory on a desk — and *unified* is the interesting word. Not system memory with a GPU beside it, holding its own private copy. One pool, addressed by both, so a model is mapped where it already sits instead of being copied across PCIe into a card. A desktop can therefore hold a model that will not fit on a much faster accelerator.

That inverts the usual trade, and the inversions keep coming. This machine has a fraction of an H100's bandwidth and several times its memory, which makes **the architecture of the model the performance lever**, not the size of the GPU. A Mixture-of-Experts model with 3B of its 35B parameters active per token runs here at **99 tokens per second**. A dense model of the same parameter count would run roughly six times slower on the same hardware, untouched. Choosing the model well is worth more than any flag.

Three more that surprised me, each measured rather than assumed:

- A dense **4B** model was *slower to first token* than the sparse **35B**. Small model, big runtime penalty.
- Thinking mode **on** decodes *faster per token* than off — reasoning text is repetitive, so the speculative drafter lands more of it.
- Sixteen concurrent streams cost almost nothing per extra stream, while the peak I had been measuring turned out to be a **setting**, not the hardware.

**And then the part that took the rest of the year.** A model on your own machine is not by itself a privacy boundary. The boundary is whatever decides, per request, whether a prompt may leave — and that decision has exactly one correct home. Put it in each client and you have written the same policy four times and drifted three of them, with a vendor key in every one. Put it at a hosted gateway and the prompt has already arrived at the thing that was meant to decide whether to send it. It belongs on the machine holding the GPU: one endpoint, one table, one decision per request, and one name in that table with no fallback at all, so it fails rather than send.

The measurements matter because a boundary people route around is decoration. Fast enough, and nobody has a reason to leave.

## What the machine holds up

**Qwen3.6-35B-A3B**, a Mixture-of-Experts model with 3B of its 35B parameters active per token, quantised to **NVFP4**, served by **vLLM** under k3s.

| | |
| --- | --- |
| One stream | **99 tokens/sec** |
| 16 streams | **459–469 tokens/sec** aggregate, about **29 per stream** |
| Time to first token | 0.23 s at 500 prompt tokens, 6.23 s at 40,000 |
| Routing | Local first, not local only. The default name serves from this GPU and falls back to a vendor if the machine cannot answer at all; the cloud tiers are reached by asking for them; **one pinned name has no fallback and fails rather than leave**. If a request must not leave, that is the name to ask for. |

**What the machine is shaped like:** 128 GB of memory at about 273 GB/s, against an H100 SXM's 80 GB at 3,350. Roughly half again the capacity, at a twelfth of the speed. Read that as a shape rather than a shortfall — it is a machine built to *hold* a great deal and read it deliberately, which is a genuinely different design point and the reason every choice below goes the way it does. Single-stream speed is that bandwidth divided by the bytes read per token, so the lever is the second number: read less per token and the machine gets faster without changing. That is what sparsity buys, and section 1 is the arithmetic.

Three reasons to want any of this. Agent workloads bill per token and are unusually input-heavy, so the meter runs fastest on the work that benefits most. Hosted models are revised and retired on someone else's schedule. And a fair amount of ordinary professional work is simply not yours to hand to a third party.

**Sections 1 to 7** are how the machine was made to serve a model reliably. **Section 8 is the argument.** **Sections 10 to 12** are what measurement cost me, including three things I believed that an experiment contradicted.

**Everything here is runnable.** [kamelhar/blog-code](https://github.com/kamelhar/blog-code) has the harness, the manifests and the raw sweeps — including a generic concurrency benchmark you can point at your own server, whatever is serving it:

```sh
export LLM_API_KEY=...
./bench/llm-concurrency.py \
    --url http://127.0.0.1:8000/v1/chat/completions \
    --model my-model --concurrency 1,2,4,8,16 --max-tokens 400
```

Standard library only. It controls for the four things that otherwise produce a confident wrong number — fixed output length, a unique prompt per request so a prefix cache cannot flatter the run, a discarded warm-up, and per-stream rates measured rather than divided out. The repository also carries the vLLM Deployment as it runs, the raw results behind every table below, and the code that publishes this blog.

### Contents

1. [The machine, and what it forces you to choose](#1-the-machine-and-what-it-forces-you-to-choose)
2. [Why a Mixture-of-Experts model](#2-why-a-mixture-of-experts-model)
3. [Kubernetes, installed directly on the host](#3-kubernetes-installed-directly-on-the-host)
4. [Making one GPU schedulable](#4-making-one-gpu-schedulable)
5. [What actually runs on the machine](#5-what-actually-runs-on-the-machine)
6. [vLLM as a Kubernetes workload](#6-vllm-as-a-kubernetes-workload)
7. [Delivery: nothing is applied by hand](#7-delivery-nothing-is-applied-by-hand)
8. [The router decides what may leave](#8-the-router-decides-what-may-leave)
9. [Reaching the open web without the model touching it](#9-reaching-the-open-web-without-the-model-touching-it)
10. [What the machine measures](#10-what-the-machine-measures)
11. [How these numbers were taken](#11-how-these-numbers-were-taken)
12. [Three beliefs that measurement contradicted](#12-three-beliefs-that-measurement-contradicted)
13. [What this design is for](#13-what-this-design-is-for)

Every number here was measured in September 2026 on the machine described, with the harness and protocol in section 11. Where something is not measured, the text says so.

<figure class="mark">
  <img src="/static/img/nvidia_n.webp" alt="The GB10 Grace Blackwell Superchip in a DGX Spark">
  <figcaption>The GB10 Grace Blackwell Superchip in a DGX Spark</figcaption>
</figure>

<figure>
  <img src="/static/img/d2_system.webp" alt="The system end to end. Everything above the dashed line stays on the machine; a request crosses it only when the router decides it may.">
  <figcaption><strong>Figure 1.</strong> The system end to end. Everything above the dashed line stays on the machine; a request crosses it only when the router decides it may.</figcaption>
</figure>

## 1. The machine, and what it forces you to choose

The GB10 in a DGX Spark reports 20 CPUs and roughly **128 GB of unified memory** shared between CPU and GPU, at about **273 GB/s**. For comparison, an H100 SXM has 80 GB at about 3,350 GB/s: less memory, roughly twelve times the bandwidth. (The PCIe and NVL configurations differ; the SXM figure is the one quoted here.)

The Spark is not a smaller H100. It is a machine that can hold very large models and read them slowly, and almost every decision below follows from that asymmetry.

Token generation is memory-bound. Each decoding step reads the active weights out of memory, so throughput is approximated by a single ratio:

```
tokens/sec  ~=  memory bandwidth  /  bytes read per token
```

This is an estimate for memory-bound decoding at batch size one, and it assumes the read is the only thing that costs. It ignores attention over a growing context, kernel efficiency below peak bandwidth, and anything that yields more than one token per weight read — multi-token prediction, later in this article, does exactly that and is why the measured figure beats what this arithmetic predicts.

For a **dense** model, "active weights" means all of them. A dense 35B model at 4-bit reads about 18 GB per token, which puts it near 15 tokens per second no matter how much memory sits unused.

> **The consequence**
>
> For a model this size at conversational speed on a memory-rich, bandwidth-poor machine, a Mixture-of-Experts architecture is not an optimisation — it is what makes the target reachable. A much smaller dense model is a different trade, and one I measured: on real agent-sized prompts a dense 4B was *slower* to first token than this 35B, because prefill dominates and the runtime mattered more than the parameter count.

## 2. Why a Mixture-of-Experts model

<figure class="mark">
  <img src="/static/img/qwen_n.webp" alt="Qwen3.6-35B-A3B, quantised to NVFP4">
  <figcaption>Qwen3.6-35B-A3B, quantised to NVFP4</figcaption>
</figure>

A Mixture-of-Experts model activates only a fraction of its parameters for each token. The model used here has **35 billion parameters in total, but only 3 billion active** per token. At 4-bit that is about 1.5 GB read per decoding step rather than 18 GB, and the machine lands at **99 tokens per second on a single stream**.

Same memory, same bandwidth, roughly six times the speed of a dense model of equal size. The architecture of the model, not the size of the machine, is what made it usable.

Two further choices matter as much as the model family:

- NVFP4 weights. Four-bit quantisation puts the weights at about 18 GB instead of roughly 70 GB, and fewer bytes read per token is the speed.
- FP8 KV cache. Roughly doubles the conversation length that fits in the same memory. Of the pool the server is given, about 18 GB is weights and most of the rest is KV cache — but not all of it: the runtime holds activations, CUDA graphs and temporary buffers too, and I did not capture vLLM's own allocation breakdown at startup. Take "most of the remainder is KV" as the shape of it rather than a measured split. The reason to want 128 GB is that this is where the room goes.

## 3. Kubernetes, installed directly on the host

<figure>
  <img src="/static/img/d1_stack.webp" alt="The stack, hardware upward. Each layer exists to make the one above it possible; the device plugin is what turns a GPU into something the scheduler can reason about.">
  <figcaption><strong>Figure 2.</strong> The stack, hardware upward. Each layer exists to make the one above it possible; the device plugin is what turns a GPU into something the scheduler can reason about.</figcaption>
</figure>

The machine runs **k3s v1.36.4** on **containerd 2.3.4**, installed on the host operating system. There is no Docker daemon, no virtual machine and no hypervisor. A second, ordinary server runs the Argo CD control plane and every application; the Spark holds model serving and nothing else.

That separation is a written rule rather than a preference, and the reason is unified memory. An application, an agent loop or a database on this machine takes memory away from the model, and the model is the only reason the machine exists.

> **Why Kubernetes at all on one node**
>
> Not for scale. It is there so that every change is a pull request, the GPU is a schedulable resource rather than something processes fight over, secrets are ciphertext in version control, and a rebuilt machine converges to the same state without anyone remembering what they typed.

## 4. Making one GPU schedulable

Kubernetes will not schedule a GPU it cannot see. Two pieces make that work.

The **NVIDIA device plugin** (`k8s-device-plugin v0.20.0`) runs as a DaemonSet and advertises `nvidia.com/gpu` on the node. Workloads then set `runtimeClassName: nvidia` so containerd hands them the NVIDIA container runtime.

Then the interesting part: one physical GPU is advertised as four.

```
sharing:
  timeSlicing:
    resources:
      - name: nvidia.com/gpu
        replicas: 4
```

### Time-slicing versus MPS

These are not interchangeable, and the difference is worth stating plainly.

|  | Time-slicing | MPS |
| --- | --- | --- |
| How | The GPU context-switches between processes | Kernels from several processes run concurrently |
| Memory | No isolation, and none available to configure. Every replica sees all of it and one can exhaust it for the others | Per-client memory budgets |
| Suits | Trusted workloads that must coexist | Workloads that must be fenced from each other |

NVIDIA documents this directly: time-sliced replicas get neither memory nor fault isolation. A replica is a scheduling share, not a sandbox, and MPS gives budgets rather than the isolation a hostile neighbour would need. This deployment uses time-slicing, which in a multi-tenant cluster would be reckless. Here the three workloads sharing the GPU are all owned by the same person, their memory use is known and bounded, and the actual requirement is that speech-to-text can coexist with the language model rather than be fenced off from it. The node reports four GPUs, three pods claim one each, and the scheduler stops arguing.

### Where that decision actually lives

Time-slicing is often described as a Kubernetes feature. It is not. The device plugin only advertises a number to the scheduler and hands out allocations; the thing that actually interleaves work between processes is the kernel module, several layers below anything Kubernetes can see. That is also why there is no memory isolation to configure — the layer doing the sharing was never asked to provide any.

<figure>
  <img src="/static/img/d6_kernel.webp" alt="The kernel path. A request crosses from user space to the kernel exactly once, through an ioctl on a character device; everything the scheduler knows about the GPU is an advertisement, and everything that is enforced is enforced below the line.">
  <figcaption><strong>Figure 3.</strong> The kernel path. A request crosses from user space to the kernel exactly once, through an ioctl on a character device; everything the scheduler knows about the GPU is an advertisement, and everything that is enforced is enforced below the line.</figcaption>
</figure>

Two details in that picture matter more than the rest. The driver here is the **open kernel module** build (`580.173.02, aarch64`), and the container image contains none of it: `nvidia-container-runtime` injects the device nodes and the driver libraries at create time, which is why the same image runs on a machine with a different driver version.

The second is `nvidia_uvm.ko`. On a conventional server the weights are copied across PCIe into dedicated GPU memory before anything can run. Here the CPU and the GPU address the same physical pages, so a model is mapped where it already sits. That is the entire reason a machine with 128 GB of comparatively slow memory can hold a model that would not fit on a much faster card.

## 5. What actually runs on the machine

| Namespace | Workload | Role |
| --- | --- | --- |
| inference | vllm | The language model. Claims one GPU. |
| inference | vllm-dev | Scaled to zero. Exists to swap a candidate model in, never to run a second copy. |
| inference | llm-router | The single endpoint every client talks to. |
| inference | llm-router-dev | Same model server, separate key, concurrency-capped, no cloud access. |
| inference | edge proxy | TLS termination and internal names. |
| inference | speech-to-text | whisper.cpp. Claims one GPU. |
| inference | text-to-speech | Claims GPU time when in use. |
| inference | watchdog | CronJob. Restarts a wedged model server. |
| platform | nvidia-device-plugin | DaemonSet. Advertises the GPU to the scheduler. |
| kube-system | sealed-secrets | Decrypts SealedSecrets inside the cluster. |

> **One model copy, always**
>
> A second model server exists in version control but is permanently scaled to zero. Testing a new model means swapping the one that runs, never running two. Two copies would each hold their own weights and compete for what is left, cutting the cache available to either — not by exactly half, since the weights are duplicated and the runtime overhead is paid twice, but by enough that the long contexts this is sized for stop fitting.

## 6. vLLM as a Kubernetes workload

<figure class="mark">
  <img src="/static/img/vllm_n.webp" alt="vLLM serves the model; the router is its intended client">
  <figcaption>vLLM serves the model; the router is its intended client</figcaption>
</figure>

The Deployment is ordinary except in five places, and each of the five is doing real work.

```
runtimeClassName: nvidia          # containerd hands it the NVIDIA runtime
hostNetwork: true                 # binds 127.0.0.1 - off-host traffic cannot reach it
resources:
  limits: { nvidia.com/gpu: 1 }   # one time-slice
volumes:                          # ~18 GB of weights, read from the host disk
  - hostPath: /models
  - hostPath: ~/.cache/huggingface
startupProbe / readinessProbe / livenessProbe
```

**Host networking bound to loopback** means the model server is not reachable from another host. Within the machine it is reachable by any local process that knows the port, so the router is its *intended* client rather than its only possible one; what stops a casual bypass is an API key the router holds and other workloads do not. That is a meaningful control and not an isolation boundary. Making it one would take a network policy or a unix socket, neither of which is in place.

**The startup probe earns its place** because loading takes about six minutes. A liveness probe alone would kill the container repeatedly before it ever finished starting — a classic and very confusing failure.

### The serving flags, and what each one buys

| Flag | Why it is there |
| --- | --- |
| --kv-cache-dtype fp8 | Roughly doubles the context held in the same memory. |
| --enable-prefix-caching | A stable system prompt is not reprocessed on every turn of a conversation. |
| --speculative-config mtp | Multi-token prediction: several tokens per weight read. The most effective single-stream lever in my testing — vLLM's counters show 2.84 tokens accepted per target forward pass, 62 percent of drafted tokens. Raising it past 3 buys draft slots that land under half the time. |
| --moe-backend marlin | Quantised Mixture-of-Experts kernels. |
| --attention-backend flashinfer | Faster attention kernels. |
| --max-num-seqs 16 | How many sequences may be in flight. See the measurements — this was the limiting setting. |
| --tool-call-parser | Tool calls arrive as structured blocks rather than prose an agent has to guess at. |
| --max-model-len 131072 | Context window. Paged attention allocates KV by tokens actually used, not by this maximum. |

## 7. Delivery: nothing is applied by hand

<figure>
  <img src="/static/img/d3_deployment.webp" alt="The deployment model. A workload arrives as an overlay and a five-line file; the control plane reads the destination out of that file rather than from a central list.">
  <figcaption><strong>Figure 4.</strong> The deployment model. A workload arrives as an overlay and a five-line file; the control plane reads the destination out of that file rather than from a central list.</figcaption>
</figure>

Argo CD runs on the companion server and reconciles both its own cluster and the Spark. Two ApplicationSets scan the git repository for a marker file, and generate an Application for every one they find.

```
apps-hub       scans  apps/*/overlays/prod/deploy.yaml
apps-clusters  scans  apps/*/overlays/<cluster>-*/deploy.yaml
```

Each overlay declares where it belongs, in five lines:

```
name: vllm
project: inference
cluster: spark
env: shared
namespace: inference
```

So adding a workload to the machine is a kustomize overlay plus that file. An overlay without one is simply not deployed, which is how something needing a hand-written Application opts out. Cluster-level infrastructure that cannot come from this pattern — the device plugin, the sealed-secrets controller — is declared as explicit Applications instead.

### Secrets

Vendor API keys and the model server key are SealedSecrets. Plaintext exists only on the machine itself; a script seals it against the in-cluster controller public key, and only ciphertext is committed. The controller decrypts it into a real Secret at apply time.

> **Why this matters beyond tidiness**
>
> No client anywhere holds a vendor key. That is what makes the spend ceiling in the next section impossible to route around: a client that cannot reach a vendor directly cannot leak the credential and cannot bypass the limit.

## 8. The router decides what may leave

Routing used to live in each client configuration. One chat gateway had a fallback chain, an editor had another, and anything new would have needed a third. That is one decision written in several places, which is how they drift — and it meant every client needed a vendor key to reach the cloud at all.

Now there is one OpenAI-compatible endpoint: a **LiteLLM** proxy running beside the model server. It speaks the OpenAI API on both sides — clients call it as though it were OpenAI, and it calls vLLM, Together AI and OpenRouter the same way. That symmetry is the point: a local model and a hosted vendor become interchangeable behind one name, so the choice between them stops being a client concern and becomes a line in one table.

> **Disclosure**
>
> I contributed the OCI provider upstream to LiteLLM, so I am not a disinterested party about the project. The argument that follows is about topology rather than about which proxy you pick — it holds for any local router.

Three things sit behind that endpoint, and they are not interchangeable with each other:

|  | What it is for |
| --- | --- |
| vLLM on the GPU | The default. No per-token charge, no request leaving the machine, and fast enough that nothing else is needed for ordinary work. Electricity and the hardware are not free; the marginal token is. |
| Together AI | Named rungs for open-weight models this machine cannot hold — larger Mixture-of-Experts models, and anything needing more memory than 128 GB. Priced per token, cheap by construction. |
| OpenRouter | One key in front of hundreds of models from many vendors, including frontier ones. Used as a passthrough for names the other two cannot serve, under a hard price ceiling. |

The distinction that matters: The Together AI entries are a small number of models I chose deliberately and named individually. The OpenRouter entry is a catalogue I have not read, reachable by pattern. The first is a decision; the second is an escape hatch, and it is capped precisely because it is not a decision.

### What the table actually says

| Model name asked for | Served by | Why it exists |
| --- | --- | --- |
| the default name | the local GPU | **Falls back to Together AI if the machine cannot answer at all** — so the default trades a guarantee for availability. Use the pinned name when the prompt must not leave. |
| the pinned name | the local GPU | No fallback chain. It fails rather than send the prompt anywhere. |
| cloud- prefix | Together AI | Named rungs, chosen in advance. Cheap by construction. |
| heavy- prefix | Together AI | Larger open models, reachable only by name. Nothing routes here on its own. |
| openrouter/* | OpenRouter | Hundreds of models behind one pattern, price-capped. |
| anything unrecognised | the local GPU | A name nobody configured is served locally rather than refused. |

> **Why an unknown name is answered rather than refused**
>
> This is the entry I am least sure of, and the one that most nearly contradicts section 12. An unrecognised name is almost always a typo or a client with a stale default, and on a single-user machine the cheapest recovery is an answer from the local model rather than an error at three in the morning. The cost is exactly the failure that section warns about: a plausible answer from a model you did not ask for. What makes it tolerable here is that the catch-all resolves *inward* — the wrong answer is local and private, never a silent spill to a vendor — and that anything requiring a specific model asks for the pinned name, which has no fallback. On a shared deployment I would make it fail loudly instead.

> **The pinned name is the one worth arguing for**
>
> Privacy cannot be inferred from content — a model cannot tell that this paragraph is the sensitive one. So it has to be selectable, and it has to be a guarantee rather than a preference. That entry has no fallback chain: it fails rather than quietly sending the conversation to a vendor at three in the morning. What leaves in a fallback is the prompt, before any answer exists, which is why the default name's fallback is a disclosure decision and not a availability one.

<figure>
  <img src="/static/img/d4_sequence.webp" alt="A single request, step by step. Step 3 is the one that matters: the decision about whether the prompt may leave is taken locally, before any network call.">
  <figcaption><strong>Figure 5.</strong> A single request, step by step. Step 3 is the one that matters: the decision about whether the prompt may leave is taken locally, before any network call.</figcaption>
</figure>

### Why OpenRouter cannot replace the local router

This is the question worth answering, because OpenRouter already does much of what LiteLLM does here — one endpoint, many providers, automatic failover, a single key. The difference is not features. It is that **a hosted gateway cannot reach this GPU**, because the model server binds loopback and nothing routes to it from outside the machine. "Local first, always" is therefore a policy that can only be evaluated here.

That is a property of this topology, not a law. Expose the model server on a private network, or put a tunnel in front of it, and a hosted gateway could route to it — at the cost of the thing the loopback bind was for. The argument is not that a remote gateway can never reach a local model. It is that the decision about whether a request may leave should not itself be taken somewhere the request has already gone.

So the two layers answer different questions, and both are needed. The local router decides whether a request may leave at all. OpenRouter decides which vendor serves it once that decision has already gone against staying home. Collapsing them would mean every request leaving the building to be told it should have stayed.

### The price ceiling belongs at the vendor

Opening the OpenRouter passthrough puts hundreds of models one string away, including frontier models at roughly 10 and 25 dollars per million tokens. LiteLLM cannot cap that here: its budget features require a database, and this deployment runs without one, so those settings silently do nothing. Rather than attach a database for one feature, the ceiling went where refusals actually happen — OpenRouter refuses the request itself when no provider meets the price.

Be precise about what that buys. `max_price` is a **per-token price filter**, not a spend cap: it refuses a request no provider will serve at or under the named rate. It says nothing about how many requests are sent, so volume at an allowed rate still adds up, and a runaway loop against a cheap model is not something this stops. It removes the failure mode where one string silently selects a model costing twenty times the intended rate. The other failure mode, too many requests, needs a counter, which needs the database this deployment does not have.

```
"provider": { "max_price": { "prompt": 1, "completion": 3 } }

a small fast model    $0.09 / $0.30   allowed
the best-value model  $0.91 / $2.86   allowed   <- the line
a frontier model      $2.00 / $10.00  refused
a flagship model      $5.00 / $25.00  refused
```

## 9. Reaching the open web without the model touching it

A useful agent needs current information, and a local model has none: its knowledge stops at its training cutoff, and it has no way to fetch anything. The obvious fix — give the model internet access — would undo the boundary this whole design is built around.

The Model Context Protocol offers a better shape. MCP lets an agent call tools that run outside the model, as ordinary local processes, and receive their output as text. The model asks for a search; a separate process performs it; the results arrive as text the model can read.

It is worth being exact about what that does and does not buy. The model not opening a socket is not itself the boundary — a model has no sockets to open in either design. The boundary is that every byte leaving the machine passes through a process I wrote the configuration for, whose network path and logs exist independently of the model. What matters is not who dials, but what the tool is permitted to transmit, and whether that permission is inspectable. A tool given an unrestricted HTTP client would move the boundary back to where it was, with extra steps.

### How it is wired here

The agent runs a small MCP server as a child process, speaking over standard input and output — no network listener, no exposed port. That server queries a self-hosted metasearch instance running as an ordinary workload in the application cluster, which in turn talks to public search engines and returns aggregated results.

```
agent  --stdio-->  MCP search server  --HTTP-->  self-hosted metasearch
                                                        |
                                                        +--> public search engines

the model itself opens no connection at any point
```

Three properties fall out of that arrangement, and each of them is the reason to prefer it over simply letting the model browse.

- Searching is a decision, not a capability. The agent must choose to call the tool, and that call is visible in the transcript. Nothing happens implicitly in the middle of generating an answer.
- The query leaves; the conversation does not. What crosses the boundary is a search string, not the transcript it came from, and a self-hosted metasearch layer means the engines see that instance rather than the person. This is a reduction in what leaves, not a guarantee. The agent composes the query, and an agent can copy a client name, a file path or a line of unreleased code straight into it. Today nothing validates or redacts that string, which makes the tool boundary a place where an obvious control is missing rather than one where the problem is solved: the queries are logged and reviewable after the fact, and that is all. A pattern filter on outbound queries is the next thing to build here.
- The tool runs where policy can reach it. It is an ordinary process on a machine that is administered, with its own network path and its own logs, rather than an opaque capability inside a model.

> **A detail that generalises**
>
> The search service is fronted by single sign-on for humans, which returns a redirect to anything without a session — including the MCP bridge. Rather than open a path through that sign-on, the bridge is pointed at the address the proxy itself forwards to, on the same host. Be exact about what that is: it **bypasses** the sign-on check rather than satisfying it. It is defensible because the backend binds an address reachable only from that host and the bridge is a process on it — the same position the proxy occupies — so nothing on the network gained access. It is still a second door, and it holds only as long as that bind stays local and the host stays trusted. The alternative, widening the sign-on to admit a service account, would have been a change to the door everyone uses.

The same pattern extends to every other tool an agent is given: a filesystem, a ticket tracker, a database. The question is never whether the model may reach it, but which process reaches it on the model’s behalf, and what that process is allowed to do.

## 10. What the machine measures

Same prompt, 400 tokens generated, varying the number of simultaneous requests:

| Concurrency | Aggregate throughput | Per stream | Note |
| --- | --- | --- | --- |
| 1 stream | 99 tokens/sec | 99 | Bandwidth-bound. |
| 8 streams | 304–354 tokens/sec | 38–44 | |
| 16 streams | 459–469 tokens/sec | 29 | 4.7x aggregate, and each stream at 29 percent of solo speed. |

The second column is the one a person feels. Sixteen streams do 4.7 times the total work, and each of them generates at under a third of the rate it would get alone — so a single answer takes roughly three times longer to finish. That is the trade: **aggregate throughput up, individual answers slower**, and which you care about depends entirely on whether anyone is waiting on one of them.

The per-stream column is aggregate throughput divided by the number of streams, not a separately measured per-request rate. The harness records both; the runs quoted here kept only the aggregate, so treat the per-stream figure as the mean it is rather than as something measured per request.

Time to first token, by prompt size:

| Prompt | First token | What dominates |
| --- | --- | --- |
| 500 tokens | 0.23 s | Nothing. Instant. |
| 4,000 tokens | 0.81 s | Prefill begins to show. |
| 16,000 tokens | 0.80–2.80 s | Depends on cache state. |
| 40,000 tokens | 6.23 s | Prefill, which is compute-bound. |

### Decode speed depends on what is being generated

A separate single-stream profile — 400 tokens, `min_tokens` and `ignore_eos`, temperature 0, median of three with the warm-up discarded:

| What it is generating | tokens/sec |
| --- | --- |
| prose, thinking on | 105.9 |
| prose, thinking **off** | 88.8 |
| code | 110.6 |

**Thinking on is faster per token than thinking off**, which is the opposite of the intuition. It is a drafter effect: multi-token prediction proposes several tokens per weight read and reasoning text is repetitive and self-similar, so more of each proposed block survives verification than does on a polished final answer. vLLM's own counters put it at 2.84 tokens accepted per forward pass, 62 percent of those drafted.

That is a per-token credit and not a total one — extended thinking still emits far more tokens, so it costs more wall time overall. But the rate at which they arrive goes up, not down. At temperature 0.7, which is what real clients send, prose measures 90–99 tokens/sec; the 99 quoted throughout this article is that case.

### Two things follow

**Batching amortises the expensive part.** Decoding is dominated by reading weights, and one forward pass reads them once for the whole batch — so the cost that dominates at one stream is shared at sixteen. It is not free: attention is per-sequence and grows with context, the scheduler does more work, and in a Mixture-of-Experts model different sequences in a batch can route to different experts, so the set of weights a batched pass actually touches can be larger than the ~1.5 GB one sequence needs. I did not measure expert overlap or real memory traffic, so read the 4.7x as the measured outcome and the explanation as the mechanism it is consistent with. The first sweep peaked at eight streams and then *fell* at twelve — the signature of requests queuing behind a concurrency cap rather than hardware saturating. The cap was the setting, not the GPU. Raising it moved peak throughput up 31 percent.

**For a single user, none of that matters.** One session gets 99 tokens per second, and none of the concurrency settings move it: that number is bandwidth divided by bytes read per token, and it is already about fifteen times faster than a person reads. One thing does move it — multi-token prediction, which yields several tokens per weight read and is why the figure is 99 rather than nearer 70. What is fixed is the ceiling that bandwidth sets, not the number itself. The concurrency headroom is real, but it only pays if the work fans out: parallel agents, batch jobs, overnight processing.

## 11. How these numbers were taken

Numbers without a protocol are anecdotes. This is the whole of it, and the harness, the manifest and the raw runs are at [kamelhar/blog-code](https://github.com/kamelhar/blog-code) — including a generic version of the concurrency harness that takes any OpenAI-compatible endpoint, so you can run it against your own server rather than take my word for mine.

**What was served.** `Qwen3.6-35B-A3B` quantised to NVFP4, read from local disk rather than pulled at start, served as `qwen3.6-35b`. The server is the upstream vLLM OpenAI image, pinned by digest rather than by tag, so the build cannot move under a rerun:

```
vllm/vllm-openai@sha256:c5fa18e5360a929262f55c697ea53d963288535fa0b238c0c47e9267a16e13b9
```

**The launch, in full.** Every flag section 6 argues for, in the order the container receives them:

```
vllm serve /models/Qwen3.6-35B-A3B-NVFP4 \
  --served-model-name qwen3.6-35b \
  --host 127.0.0.1 --port 8000 \
  --gpu-memory-utilization 0.50 \
  --max-model-len 131072 \
  --max-num-batched-tokens 16384 \
  --max-num-seqs 16 \
  --enable-auto-tool-choice \
  --enable-prefix-caching \
  --moe-backend marlin \
  --attention-backend flashinfer \
  --load-format fastsafetensors \
  --kv-cache-dtype fp8 \
  --async-scheduling \
  --tool-call-parser qwen3_xml \
  --reasoning-parser qwen3 \
  --default-chat-template-kwargs '{"enable_thinking": true}' \
  --speculative-config '{"method":"mtp","num_speculative_tokens":3,"moe_backend":"triton"}'
```

**The workload.** A concurrency sweep fires N identical-shaped requests at once against the OpenAI chat endpoint and reports time to first token, wall time, per-stream decode rate and the aggregate across streams. Output is fixed at 400 tokens with `min_tokens` and `ignore_eos` set, so every request does the same amount of decoding and a short answer cannot flatter a run. Temperature 0. Median of three, warm-up discarded.

**Cache state, which changes everything.** Each request gets a unique leading string, so the sixteen streams do **not** share a warmed prefix. This matters more than it sounds: an earlier sweep that sent the identical 16,000-token prompt to every request measured 5.01 s at concurrency 2 against 15.41 s with unique prompts, because it was measuring vLLM's prefix cache rather than the machine. Roughly a threefold difference, entirely an artefact of the harness. The concurrency numbers above are the unique-prompt ones.

That cuts the other way in the time-to-first-token table, where the 16,000-token row reads 0.80–2.80 s. The spread is exactly the cache: the low end is a warm prefix, the high end a cold one. Prefix caching is why a stable system prompt pays for itself in a real conversation, and why a benchmark that leaves it warm is not measuring the hardware.

**What is not here.** Per-request latency percentiles under concurrency are collected by the harness but not reproduced above; p95 diverges from p50 substantially once the box is saturated, which is the number to look at before putting interactive users behind a busy machine. The sweep was run on an otherwise idle GPU, with speech-to-text and text-to-speech scaled down — real contention costs more than these tables show.

## 12. Three beliefs that measurement contradicted

Each of these passed a plausibility check and failed an experiment. On a machine like this the characteristic failure is not a crash: it is a setting that looks correct, answers confidently, and is quietly doing nothing.

### "Per-deployment limits need a shared counter"

An earlier experiment had shown a requests-per-minute limit doing nothing, and that result was generalised to all concurrency settings. Then eight simultaneous requests against the capped router serialised into four clean waves of two, while the same eight against the uncapped one ran together. A parallel-request cap is an in-process semaphore that a single instance holds perfectly well; requests-per-minute is a rolling window that does need shared state. Right about one setting, wrong to extend it to both.

### "The more specific pattern obviously wins"

Adding a passthrough created two matching patterns: a vendor prefix and a catch-all. Had the catch-all won, every cloud request would have been served by the local model, silently, with no error — plausible answers from the wrong model. It was measured before it was trusted, and the result pinned with a test.

### "Declared capability means it works"

One model advertised tool-calling support in both the proxy metadata and the vendor catalogue, and still failed: the only endpoint left after filtering required a credential that was not configured. No catalogue field predicts who is actually serving a model at this moment. Filtering shrinks the surface; it cannot remove it.

## 13. What this design is for

A DGX Spark is not a small data-centre GPU; it is a differently shaped one, and that shape is the whole interest of it. Enormous capacity read deliberately rewards a sparse model, and a sparse model is what makes a machine you can put under a desk hold something genuinely capable, permanently, answering faster than anyone can read, with the conversation staying on the premises unless a request asks for a name that says otherwise.

**What this article does not establish.** It opened asking whether one machine can serve the models a small team actually uses, and every number in it answers a narrower question: how fast this configuration produces tokens. Speed is necessary and it is not sufficient. Whether tool calls succeed on real agent tasks, whether a coding or investigation task completes, and what NVFP4 quantisation costs in answer quality are all unmeasured here — and quantisation quality is exactly the kind of thing that does not show up in a throughput sweep. A small labelled task set is the obvious next piece of work, and until it exists, read this as an architecture and serving report rather than an answer about usefulness.

The parts worth copying are not the hardware choices. They are the boundary that is enforced rather than intended, the limit placed where refusals actually happen, the single endpoint that keeps one decision in one place, and the habit of measuring a setting instead of trusting that it works.

---

All measurements taken September 2026 on a DGX Spark with a GB10 Grace Blackwell Superchip. NVIDIA, vLLM and Qwen are trademarks of their respective owners; the logos appear here to identify the technologies described.
