# Step 3 — one GPU, advertised as four

An Argo CD Application installing the NVIDIA device plugin (chart 0.20.0) with
time-slicing: the node reports `nvidia.com/gpu: 4`, so vLLM, speech-to-text and
text-to-speech can each claim one.

Time-slicing shares the GPU; it does not isolate it. Every replica sees all of
the memory, and each workload keeps its own budget through its own flags.
Without Argo CD, install the chart directly with the same `values:` block.
