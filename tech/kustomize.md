# Kustomize

## 1. What it is

Kustomize is a tool, built into `kubectl` (`kubectl kustomize` and `kubectl apply -k`), that builds the final Kubernetes YAML from a **base** (the normal manifests) plus small **overlays** (a folder that says "take that base and change this"). It works without templates: the base stays valid YAML on its own, and overlays are patches. The file that drives it is always named `kustomization.yaml`.

## 2. Why this repository uses it

The lesson has four versions of the same service, each adding one thing: naive, graceful, rolling-tuned and production. Copying whole manifests four times would hide the single change that matters. With Kustomize each step is a short overlay that builds on the previous one, so the file *is* the lesson: step 2 is exactly "readiness probe, `preStop`, grace period"; step 3 is exactly "the rollout strategy"; step 4 to 6 add the autoscaler, the budget and the spread. A policy test proves each overlay adds only what it claims.

## 3. Where it appears

| File | Role |
| --- | --- |
| `manifests/base/kustomization.yaml` | Lists the base files, sets the namespace and the image tags. |
| `manifests/overlays/naive/` | Step 1 (the base unchanged). |
| `manifests/overlays/graceful/` | Step 2, built on `naive`. |
| `manifests/overlays/rolling-tuned/` | Step 3, built on `graceful`. |
| `manifests/overlays/production/` | Steps 4 to 6, built on `rolling-tuned`. |
| `scripts/deploy.sh`, `Makefile` target `conform` | Apply and validate them. |

The overlays' contents are explained in `rolling-updates.md`, `autoscaling.md` and `disruption-budget.md`.

## 4. How it works underneath

`kubectl kustomize <folder>` reads `<folder>/kustomization.yaml`. `resources:` lists files or folders to include (a folder must have its own kustomization). Kustomize loads them, applies the transformations in the file, and prints one big YAML stream. `kubectl apply -k <folder>` does the same and sends the result to the cluster.

Transformations used here:

- `namespace:` sets the namespace of every object.
- `images:` rewrites the image name and tag wherever an image is used, so the Deployment can say `image: counter-app` and the tag lives in one place.
- `configMapGenerator` builds a ConfigMap from literals or files and adds a **hash suffix** to its name (`web-config-7k2m`), rewriting every reference. When the content changes the name changes, which makes the pods roll. `behavior: merge` in an overlay changes keys of a ConfigMap generated in the base.
- `patches:` with a `target` selects an object and applies a **JSON6902 patch**: a list of operations (`op`: `replace`, `add`, `remove`) at a `path` such as `/spec/template/spec/containers/0/resources/limits/memory`. The `0` is a list index.

An overlay that lists `../local` under `resources:` starts from that folder's result, then applies its own changes.

## 5. Walkthrough of this repository's use

### `manifests/base/kustomization.yaml`

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: zero-downtime
resources:
  - namespace.yaml
  - serviceaccounts.yaml
  - web.yaml
  - loadgen.yaml
images:
  - name: zero-downtime-web
    newName: zero-downtime-web
    newTag: 0.1.0
  - name: zero-downtime-loadgen
    newName: zero-downtime-loadgen
    newTag: 0.1.0
```

`apiVersion: kustomize.config.k8s.io/v1beta1` and `kind: Kustomization` identify the file. `namespace: zero-downtime` sets the namespace of every namespaced object listed below (it does not cover objects an overlay adds). `resources:` lists the four base files. `images:` has two entries that take the image name as written in a Deployment (`zero-downtime-web`, `zero-downtime-loadgen`) and set `newName` and `newTag: 0.1.0`, so the tag lives in one place.


## 6. Try it yourself

```bash
kubectl kustomize manifests/overlays/naive | head -40
```

Prints the base's objects with the image tags added (`zero-downtime-web:0.1.0`).

```bash
diff <(kubectl kustomize manifests/overlays/naive) <(kubectl kustomize manifests/overlays/graceful)
```

Shows exactly step 2's additions: the probes, the `preStop` sleep and the grace period.

```bash
diff <(kubectl kustomize manifests/overlays/rolling-tuned) <(kubectl kustomize manifests/overlays/production)
```

Shows the autoscaler, the budget, the namespace on them, the missing `replicas` line and the topology spread constraint.

## 7. Mistakes people make

- **Patching the wrong list index.** `containers/0` is the first container; with a sidecar it might not be the one meant.
- **Adding new objects in an overlay and forgetting the namespace.** The base's `namespace:` does not cover objects added by an overlay; the production overlay sets its own (this was a real bug in this project).
- **Leaving a replica count next to an autoscaler.** Each apply resets it.
- **Expecting `kubectl apply` to delete what an overlay no longer contains.** It does not; `deploy.sh` deletes the autoscaler and budget when going back to an earlier step.
- **Editing the rendered output.** Change the source files, not the output.

## 8. Check yourself

1. What does an overlay need to build on another folder? *It lists the folder under `resources:`.*
2. Why does the Deployment have `image: zero-downtime-web` with no tag? *The `images:` block in the base supplies the tag.*
3. What does a JSON6902 `op: remove` need? *A `path`.*
4. Why does the production overlay set `namespace:`? *The autoscaler and the budget are new objects outside the base's namespace setting.*
5. How is "each step adds one thing" enforced? *A policy test strips each step's additions and compares with the previous step.*

## 9. Further reading

- Kustomize documentation: https://kubectl.docs.kubernetes.io/references/kustomize/
- `kustomization` file reference: https://kubectl.docs.kubernetes.io/references/kustomize/kustomization/
- Patches: https://kubectl.docs.kubernetes.io/references/kustomize/kustomization/patches/
- Kubernetes task page: https://kubernetes.io/docs/tasks/manage-kubernetes-objects/kustomization/
