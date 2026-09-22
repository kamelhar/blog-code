#!/usr/bin/env bash
#
# DGX Spark - wire the NVIDIA Container Toolkit into Docker
# Host: spark-c809
#
# Run with:  sudo bash 05-nvidia-container-runtime.sh
#
# WHY
#   DGX OS ships nvidia-container-toolkit without registering it with Docker:
#   `docker info` lists runtimes "runc io.containerd.runc.v2" only and
#   /etc/docker/daemon.json does not exist, so `docker run --gpus all` fails. Every containerised model server (vLLM, Ollama, NIM, NGC
#   images) needs this.
#
# WHAT IT DOES
#   1. Registers the `nvidia` runtime with the Docker daemon.
#   2. Generates a CDI spec at /etc/cdi/nvidia.yaml. CDI is NVIDIA's current
#      recommended mechanism and is the one that handles integrated-memory
#      parts (GB10) correctly, rather than the legacy discrete-GPU hook path.
#   3. Verifies a container can actually see the GPU, three ways.
#
# IDEMPOTENT: safe to re-run.
#
# FULLY REVERSIBLE:
#   sudo rm /etc/docker/daemon.json /etc/cdi/nvidia.yaml
#   sudo systemctl restart docker
#
set -euo pipefail

# The account that will run containers against the GPU. Defaults to you.
SPARK_USER="${SPARK_USER:-$USER}"
TARGET_USER="$SPARK_USER"
LOG="/home/${TARGET_USER}/Projects/cimientos/hosts/spark/05-nvidia-container-runtime.log"
TEST_IMAGE="ubuntu:24.04"

exec > >(tee -a "$LOG") 2>&1
echo "================================================================"
echo "NVIDIA CONTAINER RUNTIME: $(date -Is)"
echo "================================================================"

die() { echo; echo "*** ABORTED: $1"; exit 1; }

[ "$(id -u)" -eq 0 ] || die "must run as root (use: sudo bash $0)"

echo
echo "---- PRE-FLIGHT ----"
command -v nvidia-ctk >/dev/null || die "nvidia-ctk not found - install nvidia-container-toolkit"
echo "  nvidia-ctk:  $(nvidia-ctk --version | head -1)"
echo "  docker:      $(docker --version)"
echo "  driver:      $(nvidia-smi --query-gpu=driver_version --format=csv,noheader)"
echo "  runtimes before: $(docker info --format '{{.Runtimes}}' 2>/dev/null)"

# Back up an existing daemon.json before nvidia-ctk edits it in place, so a
# hand-written Docker config is never lost to this script.
if [ -f /etc/docker/daemon.json ]; then
    BACKUP="/etc/docker/daemon.json.bak.$(date +%Y%m%d-%H%M%S)"
    cp -a /etc/docker/daemon.json "$BACKUP"
    echo "  backed up existing daemon.json -> $BACKUP"
fi

echo
echo "---- 1. REGISTER nvidia RUNTIME WITH DOCKER ----"
nvidia-ctk runtime configure --runtime=docker
echo "  /etc/docker/daemon.json now:"
sed 's/^/    /' /etc/docker/daemon.json

echo
echo "---- 2. CDI SPEC (via NVIDIA's own refresh service) ----"
# Do NOT hand-write /etc/cdi/nvidia.yaml. The toolkit (>=1.18.0) ships
# nvidia-cdi-refresh.service, which regenerates /var/run/cdi/nvidia.yaml on
# toolkit install, driver upgrade, and boot. A second hand-made spec in
# /etc/cdi goes stale on the next driver bump and then shadows the good one -
# that is exactly how this box lost GPU containers once already.
if systemctl list-unit-files 2>/dev/null | grep -q '^nvidia-cdi-refresh\.service'; then
    systemctl enable --now nvidia-cdi-refresh.path >/dev/null 2>&1 || true
    systemctl start nvidia-cdi-refresh.service || true
    echo "  used nvidia-cdi-refresh.service -> /var/run/cdi/nvidia.yaml"
else
    echo "  refresh service absent (toolkit <1.18.0); generating manually"
    mkdir -p /var/run/cdi
    nvidia-ctk cdi generate --output=/var/run/cdi/nvidia.yaml
fi

if [ -f /etc/cdi/nvidia.yaml ]; then
    echo "  removing stale duplicate /etc/cdi/nvidia.yaml"
    rm -f /etc/cdi/nvidia.yaml
    rmdir /etc/cdi 2>/dev/null || true
fi

echo "  devices exposed:"
nvidia-ctk cdi list | sed 's/^/    /'

echo
echo "---- 3. RESTART DOCKER ----"
systemctl restart docker
sleep 3
systemctl is-active --quiet docker || die "docker failed to restart - check: journalctl -u docker -n 50"
echo "  docker active"
echo "  runtimes after: $(docker info --format '{{.Runtimes}}')"

echo
echo "---- 4. VERIFY GPU IS VISIBLE INSIDE A CONTAINER ----"
# A plain ubuntu image is used deliberately: the toolkit injects the driver
# libraries and nvidia-smi binary itself, so this tests the wiring rather
# than whatever happens to be baked into a CUDA image tag.
docker pull -q "$TEST_IMAGE" >/dev/null 2>&1 || echo "  (pull failed, trying cached image)"

PASS=0
run_test() {
    local label="$1"; shift
    echo
    echo "  [$label]"
    if timeout 120 docker run --rm "$@" "$TEST_IMAGE" nvidia-smi -L 2>&1 | sed 's/^/    /'; then
        echo "    => PASS"
        PASS=$((PASS+1))
    else
        echo "    => FAIL"
    fi
}

run_test "--gpus all"            --gpus all
run_test "--runtime=nvidia"      --runtime=nvidia -e NVIDIA_VISIBLE_DEVICES=all
run_test "CDI nvidia.com/gpu=all" --device nvidia.com/gpu=all

echo
echo "================================================================"
if [ "$PASS" -gt 0 ]; then
    echo "DONE - $PASS/3 access methods working."
else
    echo "DONE - but NO access method worked. GPU containers will not run."
    echo "Check: journalctl -u docker -n 50; nvidia-ctk cdi list"
fi
echo "================================================================"
echo
echo "Undo:"
echo "  sudo rm /etc/docker/daemon.json /etc/cdi/nvidia.yaml && sudo systemctl restart docker"
echo
echo "Log saved to: $LOG"
