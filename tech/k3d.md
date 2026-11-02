# k3d and k3s (the local cluster)

## 1. What it is

**k3s** is a small, complete, certified Kubernetes (`kubernetes.md`) packaged as a single program. **k3d** is a command-line tool that starts k3s inside Docker containers: a "node" becomes one Docker container. The result is a real Kubernetes API on your laptop that you can create in a couple of minutes and throw away afterwards. This repository uses k3d 5.9.0 with the image `rancher/k3s:v1.35.8-k3s1`, which is Kubernetes 1.35.8.

## 2. Why this repository uses it

The lesson needs several nodes: node maintenance (a drain) and pod spreading only mean something with more than one worker. k3d creates one server and three agents, each a Docker container running k3s, in a couple of minutes, and deletes them cleanly afterwards. It also bundles a metrics server, which the autoscaler needs for its CPU readings.

## 3. Where it appears

| File | Role |
| --- | --- |
| `cluster/k3d-config.yaml` | The cluster's description: one server, three agents. |
| `scripts/cluster-start.sh` | Creates the cluster (under a machine-wide lock). |
| `scripts/cluster-down.sh` | Deletes it. |
| `scripts/tools.sh` | Downloads the pinned `k3d` binary. |
| `scripts/build-image.sh` | Imports both images into every node. |

## 4. How it works underneath

`k3d cluster create --config file` reads the YAML, asks Docker to start a container from the k3s image (a **server**, which runs the control plane and, with zero **agents**, also runs the pods), waits until the Kubernetes API answers and writes a kubeconfig (the file with the address and credentials `kubectl` needs). Because the "node" is a Docker container, an image built on your machine is not automatically visible to it; `k3d image import` copies the image into the node's container runtime.

k3s bundles extras (Traefik for ingress, a service load balancer, local storage, a Helm controller, a metrics server and a network-policy controller; here the metrics server stays on). Each runs as pods and costs memory; the config switches off the ones this lesson does not use.

The **kubelet** (see `kubernetes.md`) has built-in protections: when a disk is 85 percent full it taints the node `disk-pressure` so pods stop scheduling, and it deletes unused images. The settings `eviction-hard` and `image-gc-*thresholds` relax those so a busy laptop disk does not make the demo hang. Real nodes should keep the defaults.

## 5. Walkthrough of this repository's use

### `cluster/k3d-config.yaml`

```yaml
# One server node and three agents, as the lesson asks for three worker nodes. Each node is a Docker
# container running k3s, a certified Kubernetes distribution in a single binary (Kubernetes 1.35.8, k3s
# v1.35.8-k3s1). Facts the lesson depends on:
#   - The server is tainted so web pods run only on the three agents. The load generator tolerates the
#     taint and is pinned to the server, which is never drained.
#   - k3s bundles metrics-server (the autoscaler's CPU readings), so none is installed here.
#   - Traefik, the service load balancer, local storage and the Helm controller are switched off; nothing here needs
#     them. There is no k3d load balancer either, so the API server's port is published on the server node (a
#     random free port).
# Disk thresholds: the kubelet normally refuses to schedule pods (a disk-pressure taint) and deletes unused
# images once the disk is 85% full. Docker Desktop's disk is shared with every image and volume on the
# machine and is often fuller than that, so a local cluster that obeyed the defaults would sit Pending, or lose the
# images just imported into it. Both limits are relaxed here; a real node keeps the defaults.
apiVersion: k3d.io/v1alpha5
kind: Simple
metadata:
  name: zero-downtime
servers: 1
agents: 3
image: rancher/k3s:v1.35.8-k3s1
options:
  k3d:
    wait: true
    timeout: 15m0s
    disableLoadbalancer: true
  k3s:
    extraArgs:
      - arg: --kubelet-arg=eviction-hard=nodefs.available<1%,imagefs.available<1%,nodefs.inodesFree<1%
```

The comment block explains the choices: one server and three agents (the lesson asks for three worker nodes), each node a Docker container running k3s (Kubernetes 1.35.8, `v1.35.8-k3s1`). Three facts the lesson depends on: the server is tainted so web pods run only on the three agents (the load generator tolerates the taint and is pinned to the server, which is never drained); k3s bundles the metrics server (the autoscaler's CPU readings); and the bundled Traefik, service load balancer, local storage and Helm controller are switched off, with no k3d load balancer either, so the API port is published on the server node. The last paragraph explains the relaxed disk thresholds (the kubelet normally taints a node `disk-pressure` and deletes unused images at 85% disk use, which on a shared Docker disk would leave pods Pending or lose the imported images; a real node keeps the defaults). The settings begin at `apiVersion: k3d.io/v1alpha5` and `kind: Simple`; `metadata.name` is `zero-downtime`; `servers: 1` and `agents: 3`; `image` is the exact k3s release. `options.k3d`: `wait: true`, `timeout: 15m0s`, `disableLoadbalancer: true`. Under `options.k3s.extraArgs` each item has an `arg` and `nodeFilters`. The first, `--kubelet-arg=eviction-hard=nodefs.available<1%,imagefs.available<1%,nodefs.inodesFree<1%`, makes the kubelet treat the disk as under pressure only when less than one percent is free.

```yaml
        nodeFilters:
          - all
      - arg: --kubelet-arg=image-gc-high-threshold=100
        nodeFilters:
          - all
      - arg: --kubelet-arg=image-gc-low-threshold=99
        nodeFilters:
          - all
      - arg: --disable=traefik
        nodeFilters:
          - server:*
      - arg: --disable=servicelb
        nodeFilters:
          - server:*
      - arg: --disable=local-storage
        nodeFilters:
          - server:*
      - arg: --disable-helm-controller
        nodeFilters:
          - server:*
      - arg: --node-taint=node-role.kubernetes.io/control-plane=true:NoSchedule
        nodeFilters:
          - server:*
```

The `all` filter of that item, then `--kubelet-arg=image-gc-high-threshold=100` and `--kubelet-arg=image-gc-low-threshold=99` (together they stop the kubelet deleting unused images, so the imported ones stay), each applying to all nodes. The rest apply to the server only (`server:*`): `--disable=traefik`, `--disable=servicelb`, `--disable=local-storage`, `--disable-helm-controller` and `--node-taint=node-role.kubernetes.io/control-plane=true:NoSchedule`, which keeps ordinary pods off the server.


## 6. Try it yourself

```bash
make tools && .bin/k3d version
```

Prints `k3d version v5.9.0` and `k3s version v1.35.x`.

```bash
make up
kubectl get nodes -o wide
```

Four nodes: `k3d-zero-downtime-server-0` (control plane) and `k3d-zero-downtime-agent-0` to `-2`, all `Ready`, version `v1.35.8+k3s1`.

```bash
docker ps --filter name=k3d-zero-downtime --format '{{.Names}}'
```

Shows the four containers that are the nodes (and no load-balancer container).

```bash
make down && docker ps --filter name=k3d-zero-downtime
```

The cluster and its containers are gone.

## 7. Mistakes people make

- **Forgetting to import the image.** The pod then sits in `ErrImagePull` because the node cannot see your local Docker images.
- **Running two cluster creations at once.** The host runs out of CPU and both time out; `scripts/cluster-start.sh` takes a lock so only one runs at a time.
- **Relaxing disk protections on a real node.** Only acceptable on a throwaway laptop cluster.
- **Using the wrong kubeconfig.** This project writes `.kube/config` and exports `KUBECONFIG`; a plain `kubectl` in another shell talks to some other cluster.

## 8. Check yourself

1. What is a k3d "node"? *A Docker container running k3s.*
2. Why three agents? *The lesson spreads six pods over three workers and drains one of them.*
3. What does the `NoSchedule` taint on the server do? *Ordinary pods are not scheduled there; only pods that tolerate it (the load generator) are.*
4. Why is the metrics server not disabled here? *The autoscaler reads CPU use from it.*
5. Why must the images be imported into every node? *Each node has its own image store, separate from the host's Docker.*

## 9. Further reading

- k3d documentation: https://k3d.io/stable/
- k3d config file (v1alpha5): https://k3d.io/stable/usage/configfile/
- k3s documentation: https://docs.k3s.io/
- k3s server options: https://docs.k3s.io/cli/server
- kubelet eviction: https://kubernetes.io/docs/concepts/scheduling-eviction/node-pressure-eviction/
