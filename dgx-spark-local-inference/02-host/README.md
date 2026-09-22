# Step 2 — the host

Run in order on the GPU machine:

```sh
sudo ./05-nvidia-container-runtime.sh
sudo LAN_ROUTER_IP=192.0.2.1 CONTROL_PLANE_IP=192.0.2.10 ./40-k3s.sh
kubectl apply -f runtimeclass-nvidia.yaml
```

1. Installs and verifies the NVIDIA container runtime, so containerd can hand a
   container the GPU.
2. Installs k3s directly on the host — no Docker, no VM — with Traefik and the
   service load balancer disabled, and labels the node `nvidia.com/gpu.present=true`.
   k3s finds the NVIDIA runtime and registers it with containerd. The API is
   admitted only from `CONTROL_PLANE_IP` and, if Tailscale is up, the tailnet.
   It skips the install if k3s is already running.
3. Creates the `nvidia` RuntimeClass. GPU pods ask for it by name
   (`runtimeClassName: nvidia`); without it the vLLM pod will not schedule.
