#!/usr/bin/env bash
#
# Verify served model weights against models.lock
#
# Run with:  ./verify-models.sh [ROOT]     (default: $HOME; model paths in the lock are relative to it)
#
# WHY
#   The model is the one input of the inference layer that is not code. models.lock pins the Hugging Face repo, revision, and every file's size and
#   SHA-256; this proves a directory holds exactly that, whether it was downloaded again, copied
#   back from the NAS, or has been sitting on disk. Full hashing reads every byte (~22 GB, a few
#   minutes); the nightly conformance check compares sizes only.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${1:-$HOME}"
python3 - "$HERE/models.lock" "$ROOT" <<'PY'
import hashlib, os, sys, yaml
lock, root = sys.argv[1], sys.argv[2]
bad = 0
for model in yaml.safe_load(open(lock))["models"]:
    base = os.path.join(root, model["path"].split("#")[0].strip())
    print(f"{model['repo']}@{model['revision'][:12]} in {base}")
    for f in model["files"]:
        path = os.path.join(base, f["name"])
        if not os.path.isfile(path):
            print(f"  MISSING  {f['name']}"); bad += 1; continue
        if os.path.getsize(path) != f["size"]:
            print(f"  SIZE     {f['name']}"); bad += 1; continue
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 24), b""):
                h.update(chunk)
        ok = h.hexdigest() == f["sha256"]
        print(f"  {'ok      ' if ok else 'SHA256  '} {f['name']}")
        bad += 0 if ok else 1
sys.exit(1 if bad else 0)
PY
