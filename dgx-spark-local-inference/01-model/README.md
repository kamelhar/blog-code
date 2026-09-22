# Step 1 — the model

`models.lock` pins the served model: Hugging Face repo, revision, and every
file's size and SHA-256. Download that revision, then prove the directory holds
exactly it:

```sh
hf download nvidia/Qwen3.6-35B-A3B-NVFP4 \
    --revision 491c2f1ea524c639598bf8fa787a93fed5a6fbce \
    --local-dir ~/models/Qwen3.6-35B-A3B-NVFP4
./verify-models.sh            # model paths in the lock are relative to $HOME
```

Full verification hashes every byte (~22 GB, a few minutes). Change the model
only together with the image and flags in `04-vllm/`.
