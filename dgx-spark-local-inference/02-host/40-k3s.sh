#!/usr/bin/env bash
#
# DGX Spark - single-node k3s for the inference layer
#
# Run with:  sudo bash 40-k3s.sh
#
# WHY
#   The Spark keeps inference only, deployed the same way as every app: Kubernetes workloads
#   reconciled by the Argo CD hub on the control-plane host from gitops (clusters/spark, apps/vllm,
#   apps/llm-router, apps/stt, apps/tts). k3s runs directly on the host, not in a VM: a VM would
#   split the GB10's unified memory into fixed slices and the GPU cannot be passed through
#   reliably.
#
# WHAT THIS DOES
#   - installs k3s K3S_VERSION as node "spark", without Traefik or the service load balancer,
#     with its own pod and service ranges (10.52.0.0/16, 10.53.0.0/16) clear of Docker's
#     172.17-172.20 bridges, the control plane's clusters and the tailnet
#   - k3s finds /usr/bin/nvidia-container-runtime and registers the `nvidia` containerd
#     runtime; pods ask for it through RuntimeClass `nvidia` (gitops clusters/spark); the node
#     carries nvidia.com/gpu.present=true so the NVIDIA device plugin schedules onto it
#   - ufw: routes the pod network and admits the API (6443) from the control-plane host and the tailnet,
#     so the control plane's Argo CD hub reaches it over the LAN
#   - leaves Docker and today's containers untouched; they leave in step S7
#   - writes /etc/rancher/k3s/k3s-the control-plane host.yaml: the kubeconfig the control-plane host copies to ~/.kube/spark.yaml
#     for hosts/the control-plane host/cluster/register-cluster.sh
#
# ROLLBACK
#   sudo /usr/local/bin/k3s-uninstall.sh
#   sudo ufw delete allow in on cni0; sudo ufw delete allow in on flannel.1
#   sudo ufw route delete allow from 10.52.0.0/16; sudo ufw route delete allow to 10.52.0.0/16
#   sudo ufw delete allow from "$CONTROL_PLANE_IP" to any port 6443 proto tcp
#   sudo ufw delete allow in on tailscale0 to any port 6443 proto tcp
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }
K3S_VERSION="${K3S_VERSION:-v1.36.4+k3s1}"
POD_CIDR=10.52.0.0/16
SERVICE_CIDR=10.53.0.0/16
# Two things this script needs to know about your network; set them before
# running. LAN_IP is then worked out from the route to your gateway. The k3s API
# is admitted only from CONTROL_PLANE_IP (the host that runs Argo CD) and, if
# Tailscale is up, the tailnet.
: "${LAN_ROUTER_IP:?set LAN_ROUTER_IP to your LAN gateway address}"
: "${CONTROL_PLANE_IP:?set CONTROL_PLANE_IP to the host allowed to reach the k3s API}"
LAN_IP=$(ip -4 route get "$LAN_ROUTER_IP" | awk '{for (i = 1; i <= NF; i++) if ($i == "src") {print $(i + 1); exit}}')
TS_IP=$(tailscale ip -4 2>/dev/null | head -1)
TS_NAME=$(tailscale status --json 2>/dev/null | python3 -c 'import json, sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || true)

say() { echo "  $*"; }
echo "== k3s $K3S_VERSION on $(hostname) (LAN $LAN_IP, tailnet ${TS_IP:-none})"

[ -x /usr/bin/nvidia-container-runtime ] || { echo "nvidia-container-runtime missing: run 05-nvidia-container-runtime.sh first"; exit 1; }

if systemctl is-active --quiet k3s; then
    say "k3s already running ($(k3s --version | head -1))"
else
    SANS="--tls-san $LAN_IP --tls-san spark"
    [ -n "$TS_IP" ] && SANS="$SANS --tls-san $TS_IP"
    [ -n "$TS_NAME" ] && SANS="$SANS --tls-san $TS_NAME"
    curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION="$K3S_VERSION" \
        INSTALL_K3S_EXEC="server --node-name spark --node-label nvidia.com/gpu.present=true --write-kubeconfig-mode 600 --disable traefik --disable servicelb --cluster-cidr $POD_CIDR --service-cidr $SERVICE_CIDR $SANS" sh -
fi
for _ in $(seq 1 60); do k3s kubectl get nodes 2>/dev/null | grep -q " Ready" && break; sleep 3; done
k3s kubectl get nodes -o wide | sed 's/^/  /'

echo "== nvidia runtime in k3s containerd"
if grep -q 'nvidia' /var/lib/rancher/k3s/agent/etc/containerd/config.toml 2>/dev/null; then
    say "registered"
else
    echo "  nvidia runtime not in k3s containerd config; check /var/lib/rancher/k3s/agent/etc/containerd/config.toml"
    exit 1
fi

echo "== GPU node label"
# The NVIDIA device plugin's DaemonSet schedules only onto nodes labelled as GPU nodes; without
# Node Feature Discovery that label is ours to set. --node-label covers a fresh install; this
# covers a node that already existed.
k3s kubectl label node spark nvidia.com/gpu.present=true --overwrite >/dev/null
say "nvidia.com/gpu.present=true"

echo "== ufw"
ufw allow in on cni0 >/dev/null
ufw allow in on flannel.1 >/dev/null
ufw route allow from "$POD_CIDR" >/dev/null
ufw route allow to "$POD_CIDR" >/dev/null
# The API from the LAN: the control-plane host only, not the whole subnet. The control plane's kubeconfig for this cluster names
# the Spark's LAN address (kube API over Ethernet, not the tailnet), so the control-plane host stays; guests and
# appliances on the same Wi-Fi have no reason to reach 6443.
ufw allow from "$CONTROL_PLANE_IP" to any port 6443 proto tcp >/dev/null
ufw allow in on tailscale0 to any port 6443 proto tcp >/dev/null
say "pod network routed; API 6443 from the control-plane host ($CONTROL_PLANE_IP) and the tailnet"

echo "== kubeconfig for the control-plane host"
sed "s#https://127.0.0.1:6443#https://$LAN_IP:6443#; s/default/spark/g" /etc/rancher/k3s/k3s.yaml > /etc/rancher/k3s/k3s-the control-plane host.yaml
chmod 600 /etc/rancher/k3s/k3s-the control-plane host.yaml
say "/etc/rancher/k3s/k3s-the control-plane host.yaml (server https://$LAN_IP:6443)"
say "from the control-plane host: ssh spark 'sudo cat /etc/rancher/k3s/k3s-the control-plane host.yaml' > ~/.kube/spark.yaml"
say "then: IP=$LAN_IP hosts/the control-plane host/cluster/register-cluster.sh spark"
