# Kubernetes

## 1. What it is

Kubernetes is a system that runs containers (see `docker.md`) across one or more computers and keeps them running the way you described. You do not tell it "start this program"; you give it a **desired state** ("two copies of this image, each with at most 128 MiB of memory") and Kubernetes keeps working until reality matches, and keeps checking afterwards. This repository runs Kubernetes 1.35.8 (k3s) on four nodes.

Words used throughout:

- **Cluster**: the whole Kubernetes system. **Node**: one computer (here one container pretending to be a computer) in it. The **control plane** is the part that stores the desired state and makes decisions; the **API server** is its front door, the thing `kubectl` talks to.
- **Object**: one stored description, written in YAML (`yaml.md`): a Deployment, a Service, a ConfigMap and so on. Every object has `apiVersion`, `kind`, `metadata` (name, labels) and usually a `spec` (what you want).
- **Pod**: the smallest thing that runs: one or more containers that share a network address. **Namespace**: a named folder that groups objects; this project uses `zero-downtime`.
- **Label**: a key and value stuck on an object (`app: web`). A **selector** finds objects by their labels.
- **Deployment**: keeps N identical pods running and replaces them safely when you change the template. **Service**: a stable name and address that spreads traffic over the pods matching a selector. **ConfigMap** and **Secret**: key/value data given to pods (Secrets are for sensitive values). **ServiceAccount**: the identity a pod uses when it talks to the API server. **PodDisruptionBudget**: a promise about how many pods may be down during a voluntary disruption such as a node drain.
- **kubectl**: the command-line client.

## 2. Why this repository uses it

The lesson is about *changing a running service without dropping requests*: rolling updates, autoscaling, node maintenance. Those are Kubernetes behaviours, so the project deploys a six-replica web service and a load generator on a four-node cluster and shows what each setting does to the error count. The base manifests in this note are "the service as the lesson's first step deploys it": defaults everywhere that matter to a rollout. Each overlay (see `rolling-updates.md`, `autoscaling.md`, `disruption-budget.md`) adds one thing.

## 3. Where it appears

| File | Contents |
| --- | --- |
| `manifests/base/namespace.yaml` | The namespace and its Pod Security level. |
| `manifests/base/serviceaccounts.yaml` | One service account per workload. |
| `manifests/base/web.yaml` | The service's Deployment (six replicas) and Service. |
| `manifests/base/loadgen.yaml` | The load generator's Deployment and Service. |
| `manifests/overlays/*` | The four steps (see the notes named above). |
| `scripts/*.sh` | Call `kubectl` (see `bash.md`). |

## 4. How it works underneath

You send an object to the API server (`kubectl apply`). The API server checks it and stores it. **Controllers** are loops that compare stored desired state with what exists and act. The Deployment controller creates a **ReplicaSet** (a set of identical pods) and the ReplicaSet controller creates Pod objects. The **scheduler** picks a node for each pod that has none, using the pod's resource **requests** (the amount it asks to reserve). On the chosen node the **kubelet** (the node's agent) asks the container runtime to pull the image and start the containers, then keeps reporting the pod's status.

Resources: a **request** is what the scheduler reserves; a **limit** is the ceiling. Going over the memory limit makes the kernel kill the container (reason `OOMKilled`, exit code 137). Going over the CPU limit slows it down instead.

**Probes** are checks the kubelet runs. A *liveness* probe failing makes the kubelet restart the container. A *readiness* probe failing removes the pod from the Service's list of **endpoints** (the addresses that receive traffic) without restarting it. That distinction is the heart of this lesson.

Typical pod problems have named states. `ImagePullBackOff`: the image could not be fetched. `CrashLoopBackOff`: the container keeps exiting and Kubernetes waits longer between restarts. `Pending`: no node can take the pod. `Running` but not `Ready`: started, readiness failing.

A Service gets a cluster-internal address and a DNS name (`web` inside the `zero-downtime` namespace, fully `web.zero-downtime.svc.cluster.local`). `port` is what clients call; `targetPort` is the container port the traffic is forwarded to.

## 5. Walkthrough of this repository's use

### `manifests/base/namespace.yaml`

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: zero-downtime
  labels:
    app.kubernetes.io/part-of: zero-downtime
    pod-security.kubernetes.io/enforce: restricted
    pod-security.kubernetes.io/enforce-version: latest
```

`apiVersion: v1` and `kind: Namespace` say what this is; `metadata.name: zero-downtime` is the namespace's name. The labels: `app.kubernetes.io/part-of` is a plain tag that the scripts use to find the project's objects; the two `pod-security.kubernetes.io/` labels switch on **Pod Security Admission**: `enforce: restricted` refuses any pod that is not non-root, drops no capabilities or lacks a seccomp profile, and `enforce-version: latest` uses the current Kubernetes rules. That is why the pods below carry such a long `securityContext`.

### `manifests/base/serviceaccounts.yaml`

```yaml
# One account per workload, none of them with API permissions and none with a mounted token.
apiVersion: v1
kind: ServiceAccount
metadata:
  name: web
  labels:
    app.kubernetes.io/name: web
    app.kubernetes.io/part-of: zero-downtime
automountServiceAccountToken: false
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: loadgen
  labels:
    app.kubernetes.io/name: loadgen
    app.kubernetes.io/part-of: zero-downtime
automountServiceAccountToken: false
```

Two ServiceAccounts separated by `---`, `web` and `loadgen`, each with labels and `automountServiceAccountToken: false`, which stops Kubernetes from putting an API credential inside the pod. Neither program talks to the Kubernetes API.

### `manifests/base/web.yaml`

```yaml
# The service as the lesson's first step deploys it: six replicas, defaults everywhere that matters to a
# rollout (no readiness probe, no preStop hook, the default 30-second grace period, the default 25% surge
# and 25% unavailable). Each overlay adds one thing the lesson teaches.
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
  labels:
    app: web
    app.kubernetes.io/name: web
    app.kubernetes.io/part-of: zero-downtime
spec:
  replicas: 6
  selector:
    matchLabels:
      app: web
  template:
    metadata:
      labels:
        app: web
        app.kubernetes.io/name: web
        app.kubernetes.io/part-of: zero-downtime
    spec:
      serviceAccountName: web
      enableServiceLinks: false
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
```

The comment says what this file is: the service as the lesson's first step deploys it, with six replicas and defaults everywhere that matters to a rollout (no readiness probe, no `preStop` hook, the default 30-second grace period, the default 25% surge and 25% unavailable); each overlay adds one thing. `kind: Deployment` named `web`, `replicas: 6`, a `selector` and a pod `template` whose labels match it. In the pod spec: `serviceAccountName: web`, `enableServiceLinks: false` (no injected Service variables), `automountServiceAccountToken: false`, and the start of the pod `securityContext` with `runAsNonRoot: true`.

```yaml
        runAsUser: 10001
        runAsGroup: 10001
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: web
          image: zero-downtime-web
          ports:
            - name: http
              containerPort: 8080
          env:
            - name: STARTUP_DELAY_SECONDS
              value: "1"
            - name: LATENCY_MIN_MS
              value: "50"
            - name: LATENCY_MAX_MS
              value: "200"
            - name: CPU_WORK_MS
              value: "2"
          resources:
            requests:
              cpu: 150m
              memory: 64Mi
            limits:
              cpu: 500m
              memory: 128Mi
          securityContext:
            allowPrivilegeEscalation: false
```

`runAsUser` and `runAsGroup` 10001 (the number baked into the image) and the default seccomp profile. The container `web` uses the image `zero-downtime-web` with no tag (the Kustomization supplies it), names port 8080 `http`, and sets four environment variables as quoted text: `STARTUP_DELAY_SECONDS=1` (the warm-up before the port opens), `LATENCY_MIN_MS=50` and `LATENCY_MAX_MS=200` (the range of each request's latency) and `CPU_WORK_MS=2` (the part of it that is real computation). `resources`: request 100 millicores of CPU and 64 MiB; limits 500m and 128 MiB. The autoscaler measures CPU use as a percentage of that 100m request. The container `securityContext` begins with `allowPrivilegeEscalation: false`.

```yaml
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          volumeMounts:
            - name: tmp
              mountPath: /tmp
      volumes:
        - name: tmp
          emptyDir:
            sizeLimit: 16Mi
---
apiVersion: v1
kind: Service
metadata:
  name: web
  labels:
    app.kubernetes.io/name: web
    app.kubernetes.io/part-of: zero-downtime
spec:
  selector:
    app: web
  ports:
    - name: http
      port: 80
      targetPort: http
```

`readOnlyRootFilesystem: true` and `capabilities: drop: ["ALL"]`. The `tmp` volume (an `emptyDir` of 16 MiB) is mounted at `/tmp`, the only writable path (gunicorn's heartbeat files). After `---` the Service `web`: it selects `app: web` and publishes port 80 forwarding to the container port named `http`; this is the address the load generator calls.

### `manifests/base/loadgen.yaml`

```yaml
# The load generator runs inside the cluster so that its requests go through the Service and kube-proxy to
# whichever pod is next, like any client would. (kubectl port-forward would pin all traffic to one pod.)
# It is pinned to the server node (k3s labels it node-role.kubernetes.io/control-plane), which is never drained,
# so draining a worker cannot disturb the thing that is counting errors.
apiVersion: apps/v1
kind: Deployment
metadata:
  name: loadgen
  labels:
    app: loadgen
    app.kubernetes.io/name: loadgen
    app.kubernetes.io/part-of: zero-downtime
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: loadgen
  template:
    metadata:
      labels:
        app: loadgen
        app.kubernetes.io/name: loadgen
        app.kubernetes.io/part-of: zero-downtime
    spec:
      serviceAccountName: loadgen
      enableServiceLinks: false
```

The comment explains why the generator runs inside the cluster (its requests go through the Service and kube-proxy to whichever pod is next, like any client; a port-forward would pin all traffic to one pod) and why it is pinned to the server node (which is never drained, so draining a worker cannot disturb the thing that is counting errors). Deployment `loadgen`, one replica, `strategy: Recreate` (stop the old pod before starting the new one, so there are never two generators), selector and labels, `serviceAccountName: loadgen`, `enableServiceLinks: false`.

```yaml
      automountServiceAccountToken: false
      affinity:
        nodeAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            nodeSelectorTerms:
              - matchExpressions:
                  - key: node-role.kubernetes.io/control-plane
                    operator: Exists
      tolerations:
        - key: node-role.kubernetes.io/control-plane
          operator: Exists
          effect: NoSchedule
      terminationGracePeriodSeconds: 30
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        runAsGroup: 10001
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: loadgen
          image: zero-downtime-loadgen
          ports:
            - name: control
              containerPort: 9100
          env:
            - name: TARGET_URL
              value: http://web.zero-downtime.svc.cluster.local/
```

`automountServiceAccountToken: false`. `affinity.nodeAffinity.requiredDuringSchedulingIgnoredDuringExecution` is a hard rule that the pod must go to a node having the label `node-role.kubernetes.io/control-plane` (`operator: Exists`): the server node. The `tolerations` entry lets it run despite the `NoSchedule` taint on that node. `terminationGracePeriodSeconds: 30` lets it print its final counts on `SIGTERM`. The pod `securityContext` is non-root 10001 with the default seccomp profile. The container `loadgen` uses the image `zero-downtime-loadgen`, names the control port 9100 and starts the environment with `TARGET_URL=http://web.zero-downtime.svc.cluster.local/`: the web Service's full DNS name.

```yaml
            - name: RATE_PER_SECOND
              value: "100"
            - name: TIMEOUT_SECONDS
              value: "2"
            - name: MAX_IN_FLIGHT
              value: "256"
          resources:
            requests:
              cpu: 250m
              memory: 128Mi
            limits:
              cpu: "1"
              memory: 256Mi
          readinessProbe:
            httpGet:
              path: /healthz
              port: control
            periodSeconds: 5
          livenessProbe:
            httpGet:
              path: /healthz
              port: control
            initialDelaySeconds: 5
            periodSeconds: 10
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
```

`RATE_PER_SECOND=100`, `TIMEOUT_SECONDS=2` (a slower request is counted as a timeout error), `MAX_IN_FLIGHT=256`. Resources: request 250m CPU and 128 MiB, limits one core (`"1"`) and 256 MiB. Both probes are HTTP GETs to `/healthz` on the control port (readiness every 5 s; liveness every 10 s after 5 s). The container `securityContext` begins: no privilege escalation, a read-only root file system and the `capabilities:` list.

```yaml
              drop: ["ALL"]
---
apiVersion: v1
kind: Service
metadata:
  name: loadgen
  labels:
    app.kubernetes.io/name: loadgen
    app.kubernetes.io/part-of: zero-downtime
spec:
  selector:
    app: loadgen
  ports:
    - name: control
      port: 9100
      targetPort: control
```

`drop: ["ALL"]` finishes the hardening. After `---` the Service `loadgen` publishes port 9100 to the container port named `control`; `loadctl.sh` reaches it through a port-forward.


## 6. Try it yourself

```bash
make up
kubectl -n zero-downtime get deploy,svc,hpa,pdb
```

Lists `web` (6/6 or more), `loadgen` (1/1), the two Services, the autoscaler and the disruption budget.

```bash
kubectl -n zero-downtime get pods -o wide
```

Six `web` pods spread over the three worker nodes (two on each when evenly spread) and the `loadgen` pod on the server node.

```bash
kubectl get nodes
kubectl describe node k3d-zero-downtime-server-0 | grep -A1 Taints
```

Four nodes; the server carries the taint `node-role.kubernetes.io/control-plane=true:NoSchedule`, which is why web pods run only on the three agents.

```bash
kubectl -n zero-downtime top pods
```

CPU and memory per pod (from the bundled metrics server); the CPU figure is what the autoscaler divides by the request.

## 7. Mistakes people make

- **No readiness probe.** A new pod receives traffic before it can serve.
- **No `preStop` delay.** A terminating pod is stopped before every node has stopped sending it traffic.
- **A pod grace period shorter than the shutdown work.** Kubernetes kills the container mid-request.
- **No resource requests.** The autoscaler's percentage is "of the request"; without one it cannot work.
- **Draining without a disruption budget.** Every pod of the service can be evicted at once.

## 8. Check yourself

1. Why does the base web Deployment have six replicas? *The lesson's rolling update, spike and drain all need enough pods for losing one or two to matter.*
2. What does the taint on the server node do? *Keeps ordinary pods off it; only the load generator tolerates it.*
3. Why is the load generator inside the cluster? *Its requests then go through the Service and kube-proxy to whichever pod is next, like any client; a port-forward would pin all traffic to one pod.*
4. Why `automountServiceAccountToken: false`? *Neither program calls the Kubernetes API, so no credential is mounted.*
5. Which settings does the naive step leave at their defaults? *No readiness probe, no `preStop`, the 30-second grace period and the default 25% surge and 25% unavailable.*

## 9. Further reading

- Kubernetes documentation (v1.35): https://kubernetes.io/docs/home/
- Deployments: https://kubernetes.io/docs/concepts/workloads/controllers/deployment/
- Probes: https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/
- Resource management: https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/
- Debugging pods: https://kubernetes.io/docs/tasks/debug/debug-application/debug-pods/
