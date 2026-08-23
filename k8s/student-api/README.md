# `student-api` Helm chart

Helm chart for the Student API application — the FastAPI backend plus its PostgreSQL database — converted 1:1 from the raw manifests that used to live under `k8s/manifests/` (`namespace.yml`, `configmap.yml`, `secret.yml`, `app-deployment.yml`, `app-service.yml`, `postgres-statefulset.yml`, `postgres-service.yml`, `sc-wait.yaml`, `eso-components/*`). It's the chart ArgoCD's `student-api-application.yaml` (`k8s/argo-manifests/`) points at, and the one CI's "Bump image tag" job edits on every merge to `main`.

| | |
|---|---|
| Chart name | `student-api` |
| Chart version | `0.1.0` |
| App version | `1.0.0` |
| Type | `application` |
| Dependencies | none (no `Chart.yaml` `dependencies:` block) |

## Table of Contents

- [What this chart deploys](#what-this-chart-deploys)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Install](#install)
- [Values reference](#values-reference)
  - [Top level](#top-level)
  - [`application`](#application)
  - [`config`](#config)
  - [`secret`](#secret)
  - [`database`](#database)
  - [`storageClass`](#storageclass)
  - [`externalSecrets`](#externalsecrets)
- [Conditional rendering — what turns what on/off](#conditional-rendering--what-turns-what-onoff)
- [Security posture](#security-posture)
- [Scheduling — node affinity](#scheduling--node-affinity)
- [Observability — ServiceMonitor](#observability--servicemonitor)
- [Secrets — plain Secret vs. Vault/ESO](#secrets--plain-secret-vs-vaultesо)
- [Known caveats / things to check before a real deploy](#known-caveats--things-to-check-before-a-real-deploy)
- [Common overrides — example scenarios](#common-overrides--example-scenarios)
- [Uninstall](#uninstall)

## What this chart deploys

| Template | Resource | Gated by |
|---|---|---|
| `namespace.yaml` | `Namespace` | `.Values.namespaceCreate` |
| `configmap.yaml` | `ConfigMap` (app + DB env vars) | `.Values.config.enabled` |
| `secret.yaml` | `Secret` (`DB_PASSWORD`, base64-encoded from plain text) | `.Values.secret.enabled` **and** `externalSecrets.enabled == false` |
| `app-deployment.yaml` | `Deployment` for the API, with an `initContainer` running `alembic upgrade head` | always |
| `app-service.yaml` | `ClusterIP` (default) `Service` for the API | always |
| `app-hpa.yaml` | `HorizontalPodAutoscaler` | `.Values.application.hpa.enabled` |
| `servicemonitor.yaml` | Prometheus Operator `ServiceMonitor` | `.Values.application.serviceMonitor.enabled` |
| `postgres-statefulset.yaml` | `StatefulSet` for Postgres, with a `volumeClaimTemplate` | `.Values.database.enabled` |
| `postgres-service.yaml` | Headless `Service` (`clusterIP: None`) for the StatefulSet | `.Values.database.enabled` |
| `storageclass.yaml` | `StorageClass` (`csi-hostpath-sc-wait`) | `.Values.storageClass.create` |
| `eso-components/cluster-secretstore.yaml` | `ClusterSecretStore` pointing at Vault | `.Values.externalSecrets.enabled` |
| `eso-components/eso-service-account.yaml` | `ServiceAccount` + `Role` + `RoleBinding` for ESO's token exchange | `.Values.externalSecrets.enabled` |
| `eso-components/external-secret.yaml` | `ExternalSecret` that syncs `DB_PASSWORD` from Vault into a `Secret` | `.Values.externalSecrets.enabled` |

`_helpers.tpl` defines a `student-api.labels` named template (standard `app.kubernetes.io/*` + `helm.sh/chart` labels), but no template in the chart currently calls it — every resource labels itself with a plain `app: {{ .Values.application.name }}` / `app: {{ .Values.database.name }}` instead. Worth knowing if you're relying on the standard Helm labels for tooling (e.g. `helm.sh/chart`) — they aren't actually applied to any object yet.

## Architecture

```
                         ┌─────────────────────────┐
                         │  student-api Namespace  │
                         └─────────────────────────┘
ConfigMap (student-api-config) ──┐
                                 ├──► envFrom ──► initContainer (alembic upgrade head)
Secret (student-api-secret)  ────┘                        │
   ▲ either:                                              ▼
   │  - rendered directly from secret.data (plain text)  Deployment: student-api
   │  - OR owned by ExternalSecret, synced from Vault        │
   └── ClusterSecretStore(vault-backend) ── ExternalSecret   │
                                                             ▼
                                                     Service: student-api (ClusterIP:80 → 8000)
                                                             │
                                                             ▼
                                            (optional) ServiceMonitor → Prometheus scrapes /metrics

ConfigMap ──┐
Secret   ───┴──► env ──► StatefulSet: postgres ──► headless Service: postgres
                                │
                                ▼
                     PVC (StorageClass: csi-hostpath-sc-wait)
```

The API's `Deployment` and Postgres's `StatefulSet` are two independent objects deployed by the same chart — there's no chart dependency on an external Postgres subchart (e.g. Bitnami); the DB is hand-rolled here specifically so it could be converted 1:1 from the original raw manifest.

## Prerequisites

- A Kubernetes cluster with a `StorageClass` supporting `WaitForFirstConsumer` binding (the chart can create one itself — see [`storageClass`](#storageclass) — but the underlying CSI driver, e.g. `csi-hostpath-driver` on minikube, must already be enabled on the cluster).
- Nodes labeled `workload=application` and `workload=database` if you leave `nodeAffinity.enabled: true` on both the app and the database (see [Scheduling](#scheduling--node-affinity)) — `k8s/script.sh` does this labeling for a 3-node minikube cluster.
- If `application.serviceMonitor.enabled: true` (the default): the Prometheus Operator CRDs (`ServiceMonitor`) must already be installed — e.g. via the `kube-prometheus-stack` chart in `k8s/helm/kube-prometheus-stack`, deployed by the `observability` ArgoCD ApplicationSet. Without the CRD, this template will fail to apply.
- If `externalSecrets.enabled: true` (the default): Vault and External Secrets Operator must already be running, with the `student-api-role` Vault role and `secret/student-api/db` KV path already provisioned. See **[`k8s/helm/README.md`](../helm/README.md)** for that full setup — this chart only creates the `ClusterSecretStore`/`ExternalSecret`/RBAC glue, not Vault itself.

## Install

```bash
helm install student-api . -n student-api --create-namespace
```

`namespaceCreate: true` in `values.yaml` also creates the `Namespace` resource itself via the chart, so `--create-namespace` is optional/redundant here — either works, and using both is harmless (Helm just no-ops if the namespace already exists).

Or, as this repo actually runs it: **not by hand at all**. `k8s/argo-manifests/student-api-application.yaml` is an ArgoCD `Application` pointing at this chart's path with automated `prune`/`selfHeal` sync — once that `Application` is applied, ArgoCD installs/upgrades this release on every push to `main`, sourced entirely from `values.yaml` in this directory (no separate `values.argo.yaml` override file for this particular chart, unlike some of the vendored charts under `k8s/helm/`).

To render manifests locally without installing (useful for reviewing what a values change will actually produce):
```bash
helm template student-api . -n student-api
```

## Values reference

### Top level

| Key | Default | Purpose |
|---|---|---|
| `namespace` | `student-api` | Namespace every namespaced resource in this chart is placed into. |
| `namespaceCreate` | `true` | Whether the chart creates the `Namespace` object itself. |

### `application`

| Key | Default | Purpose |
|---|---|---|
| `application.name` | `student-api` | Used as the resource name and the `app` label selector for the Deployment/Service/ServiceMonitor. |
| `application.replicaCount` | `1` | Deployment replica count (static — ignored once `hpa.enabled: true` takes over scaling). |
| `application.image.repository` | `at1asflame/student-api` | Image repo. |
| `application.image.tag` | `1c15017` | Image tag — this is the field CI's "Bump image tag" job patches with the new commit SHA on every merge to `main`. |
| `application.image.pullPolicy` | `IfNotPresent` | Standard pull policy. |
| `application.service.type` | `ClusterIP` | Service type — internal-only by default; put an Ingress/Gateway in front for external access. |
| `application.service.port` | `80` | Service port. |
| `application.service.targetPort` | `8000` | Container port `uvicorn` actually listens on (matches the Dockerfile's `EXPOSE 8000`). |
| `application.service.portName` | `http` | Named port — required so the `ServiceMonitor` can reference the port by name rather than number. |
| `application.serviceMonitor.enabled` | `true` | Deploy a Prometheus Operator `ServiceMonitor` for this Service. |
| `application.serviceMonitor.path` | `/metrics` | Scrape path — assumes `prometheus-fastapi-instrumentator` (or equivalent) is wired into the app and actually serving `/metrics`. |
| `application.serviceMonitor.interval` | `15s` | Scrape interval. |
| `application.envFrom.configMapName` | `student-api-config` | Name of the ConfigMap injected via `envFrom`. Must match `config.name` if you rename it. |
| `application.envFrom.secretName` | `student-api-secret` | Name of the Secret injected via `envFrom`. Must match `secret.name` **and** `eso-components/external-secret.yaml`'s hardcoded target name if you rename it (see [caveats](#known-caveats--things-to-check-before-a-real-deploy)). |
| `application.podSecurityContext` | `runAsUser/runAsGroup/fsGroup: 1001`, `fsGroupChangePolicy: OnRootMismatch` | Pod-level security context — **new versus the original raw manifest**, which had none. |
| `application.containerSecurityContext` | `runAsNonRoot: true`, `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true`, `capabilities.drop: [ALL]` | Container-level hardening — **new versus the raw manifest**. Note `readOnlyRootFilesystem: true` here, unlike the database's (see below); this works because the app itself doesn't write to its own rootfs at runtime. |
| `application.resources.requests` | `cpu: 250m`, `memory: 256Mi` | |
| `application.resources.limits` | `cpu: 500m`, `memory: 512Mi` | |
| `application.nodeAffinity.enabled` | `true` | Pin app pods to nodes matching the key/values below via `requiredDuringSchedulingIgnoredDuringExecution`. |
| `application.nodeAffinity.key` | `workload` | Node label key. |
| `application.nodeAffinity.values` | `[application]` | App pods only schedule onto nodes labeled `workload=application`. |
| `application.probes.path` | `/api/v1/health` | Both readiness and liveness probes hit this same path (the liveness probe, not `/api/v1/ready`, since liveness shouldn't fail on a transient DB blip — only readiness typically should, and this chart currently points both at the same lightweight liveness-style endpoint). |
| `application.probes.initialDelaySeconds` | `15` | |
| `application.probes.periodSeconds` | `10` | |
| `application.probes.timeoutSeconds` | `5` | |
| `application.probes.failureThreshold` | `5` | ~50s of failures tolerated before the probe trips. |
| `application.initContainer.enabled` | `true` | Runs `alembic upgrade head` as an `initContainer` before the app container starts, so migrations are guaranteed to complete first, on every pod (re)start. |
| `application.initContainer.command` | `["python", "-m", "alembic", "upgrade", "head"]` | |
| `application.hpa.enabled` | `false` | Toggle the `HorizontalPodAutoscaler`. Requires a metrics pipeline (e.g. `metrics-server`) in the cluster to actually function — not bundled by this chart. |
| `application.hpa.minReplicas` / `maxReplicas` | `1` / `3` | |
| `application.hpa.cpuUtilization` | `70` | Target average CPU utilization %. |

### `config`

| Key | Default | Purpose |
|---|---|---|
| `config.enabled` | `true` | Deploy the `ConfigMap`. |
| `config.name` | `student-api-config` | Must match `application.envFrom.configMapName` and `database.env.configMapName`. |
| `config.data.*` | `POSTGRES_DB=student_db`, `POSTGRES_USER=postgres`, `APP_NAME=Student API`, `APP_VERSION=1.0.0`, `APP_ENV=production`, `DEBUG=false`, `LOG_LEVEL=INFO`, `DB_DRIVER=postgresql+psycopg`, `DB_HOST=postgres`, `DB_PORT=5432`, `DB_NAME=student_db`, `DB_USER=postgres` | Every non-secret env var the app and Postgres need. `DB_HOST: postgres` resolves via the headless `postgres` Service's in-cluster DNS — same pattern as `DB_HOST: postgres` in the root `docker-compose.yaml`, just Kubernetes Service DNS instead of Compose DNS. Note the Postgres StatefulSet's readiness/liveness probes read `POSTGRES_USER`/`POSTGRES_DB` **directly off this map at template-render time** (`.Values.config.data.POSTGRES_USER`), not through `database.env.dbUserKey`/`dbNameKey` — so if you rename those keys in `config.data`, update the probe commands' literal Helm references too, not just `database.env`. |

### `secret`

| Key | Default | Purpose |
|---|---|---|
| `secret.enabled` | `true` | Deploy the plain `Secret` template. **Ignored whenever `externalSecrets.enabled: true`** — see [Secrets](#secrets--plain-secret-vs-vaultesо). |
| `secret.name` | `student-api-secret` | Must match `application.envFrom.secretName`, `database.env.secretName`, and the `target.name` hardcoded in `eso-components/external-secret.yaml`. |
| `secret.data.DB_PASSWORD` | `"postgres"` | **Plain text** in `values.yaml` — the template base64-encodes it for you (`{{ $value \| b64enc }}`). **Change this before any real deploy**; it's committed to git as-is otherwise. |

### `database`

| Key | Default | Purpose |
|---|---|---|
| `database.enabled` | `true` | Deploy the Postgres `StatefulSet` + headless `Service`. |
| `database.name` | `postgres` | Resource name; also the DNS name other pods reach it at (`postgres.<namespace>.svc`). |
| `database.replicas` | `1` | This is a bare single-instance Postgres StatefulSet — no replication/HA, no operator. Fine for a bootcamp/dev cluster, not a production Postgres topology. |
| `database.image.repository` / `.tag` | `postgres` / `17` | Matches the version pinned in the root `docker-compose.yaml`. |
| `database.service.port` | `5432` | |
| `database.persistence.storageClassName` | `csi-hostpath-sc-wait` | Must match `storageClass.name` if you rename it. |
| `database.persistence.size` | `1Gi` | |
| `database.persistence.accessMode` | `ReadWriteOnce` | |
| `database.resources.requests` | `cpu: 250m`, `memory: 512Mi` | |
| `database.resources.limits` | `cpu: 500m`, `memory: 1Gi` | |
| `database.podSecurityContext` | `runAsUser/runAsGroup/fsGroup: 999` | UID/GID `999` matches the official `postgres` image's built-in `postgres` user — **new versus the raw manifest**. |
| `database.containerSecurityContext.readOnlyRootFilesystem` | `false` | Left `false` by default: the official Postgres image writes to several paths at startup (`initdb`, locale/config files). Flip to `true` if you want a fully read-only rootfs — the template then mounts writable `emptyDir`s at `/tmp` and `/var/run/postgresql` for you automatically. |
| `database.nodeAffinity.enabled` / `.key` / `.values` | `true` / `workload` / `[database]` | DB pods only schedule onto nodes labeled `workload=database` — deliberately a *different* node pool from the app (`workload=application`), so a noisy-neighbor app pod can't starve the database node. |
| `database.env.configMapName` / `.secretName` | `student-api-config` / `student-api-secret` | Source of `POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD` env vars via `valueFrom`. |
| `database.env.dbNameKey` / `.dbUserKey` / `.dbPasswordKey` | `POSTGRES_DB` / `POSTGRES_USER` / `DB_PASSWORD` | Which keys inside the ConfigMap/Secret map to which Postgres env var. |

The StatefulSet also sets `PGDATA=/var/lib/postgresql/data/pgdata` (a subdirectory of the mounted volume root) rather than mounting the PVC with a `subPath`. This is a deliberate fix for a real issue hit during development: `subPath` directories aren't reliably `fsGroup`-owned by kubelet on creation ([kubernetes/kubernetes#57923](https://github.com/kubernetes/kubernetes/issues/57923)), which caused `initdb`'s `chmod` to fail with `Operation not permitted`. Letting Postgres create `pgdata` itself inside the already-correctly-owned mount root avoids that entirely.

### `storageClass`

| Key | Default | Purpose |
|---|---|---|
| `storageClass.create` | `true` | Deploy the `StorageClass` object. |
| `storageClass.name` | `csi-hostpath-sc-wait` | Must match `database.persistence.storageClassName`. |
| `storageClass.provisioner` | `hostpath.csi.k8s.io` | The minikube CSI hostpath driver's provisioner — **not portable to a real cloud cluster** (EBS/PD/Azure Disk have their own provisioners); set `storageClass.create: false` and point `database.persistence.storageClassName` at your cluster's real StorageClass instead when deploying anywhere but minikube. |
| `storageClass.reclaimPolicy` | `Delete` | PV is deleted when the PVC is deleted — fine for dev, possibly not what you want for a real database. |
| `storageClass.volumeBindingMode` | `WaitForFirstConsumer` | Defers volume provisioning until a pod is actually scheduled, so the volume lands on whichever node the scheduler picks — critical when combined with `nodeAffinity`, otherwise the CSI driver's default `Immediate` binding mode can provision the volume on a node the pod's affinity rules then can't use. |
| `storageClass.allowVolumeExpansion` | `false` | PVC size is fixed at `database.persistence.size` after creation. |

### `externalSecrets`

| Key | Default | Purpose |
|---|---|---|
| `externalSecrets.enabled` | `true` | When `true`: deploys `ClusterSecretStore` (`vault-backend`) + `ExternalSecret` (`student-api-secret`) + the `eso-vault-auth` ServiceAccount/Role/RoleBinding, and **skips** the plain `secret.yaml` template entirely — `DB_PASSWORD` is sourced live from Vault (`secret/student-api/db`, property `password`) instead of the value in `secret.data.DB_PASSWORD`. |

The three `eso-components/*` templates hardcode `namespace: student-api` in several places (the `ServiceAccount`, `Role`, `RoleBinding`, and the `ExternalSecret`'s own `metadata.namespace`) rather than referencing `.Values.namespace` — see [caveats](#known-caveats--things-to-check-before-a-real-deploy) if you ever deploy this chart into a namespace other than the default.

## Conditional rendering — what turns what on/off

A few interactions between values are easy to miss:

- **`secret.yaml` renders only if `secret.enabled: true` AND `externalSecrets.enabled: false`.** With the shipped defaults (`secret.enabled: true`, `externalSecrets.enabled: true`), the plain Secret template is actually skipped — Vault/ESO wins. To fall back to the plain in-values password, set `externalSecrets.enabled: false`.
- **`servicemonitor.yaml` has no dependency check on `database.enabled` or anything else** — it's gated purely on `application.serviceMonitor.enabled`, but will fail at `helm template`/`apply` time if the `ServiceMonitor` CRD isn't installed in the cluster, since Helm doesn't validate CRD availability itself for non-CRD charts.
- **`app-hpa.yaml`** is independent of `application.replicaCount` — if you enable the HPA, `replicaCount` becomes the *starting* replica count only; the HPA's `minReplicas`/`maxReplicas` govern actual scaling from then on.
- **`postgres-statefulset.yaml`'s probes read `config.data.POSTGRES_USER`/`POSTGRES_DB` directly** (not via `database.env.dbUserKey`/`dbNameKey`) — those two value paths must stay in sync manually if either is renamed.

## Security posture

Both workloads run as non-root, fixed UIDs, with `allowPrivilegeEscalation: false` and all Linux capabilities dropped:

| | App pod | Postgres pod |
|---|---|---|
| `runAsUser`/`runAsGroup`/`fsGroup` | `1001` | `999` (matches the official image's built-in `postgres` user) |
| `readOnlyRootFilesystem` | `true` (default) | `false` (default) — see rationale above |
| `capabilities.drop` | `[ALL]` | `[ALL]` |

Neither of these security contexts existed in the original raw manifests this chart was converted from — they were added as part of the Helm conversion. If your cluster enforces Pod Security Standards (`restricted` or similar), this chart's defaults should already satisfy them for the app pod; for the database pod, either accept `readOnlyRootFilesystem: false` or flip `database.containerSecurityContext.readOnlyRootFilesystem: true` (the chart handles mounting the extra `emptyDir`s for you).

## Scheduling — node affinity

The chart assumes a cluster with nodes labeled by role:

```
workload=application   →  student-api pods
workload=database       →  postgres pods
```

`k8s/script.sh` applies exactly these two labels (plus a third, `workload=dependencies`, used by the charts under `k8s/helm/` for Vault/ESO/observability) to a 3-node minikube cluster. On any other cluster, either replicate that labeling scheme or set `application.nodeAffinity.enabled: false` / `database.nodeAffinity.enabled: false` to let the scheduler place pods freely.

## Observability — ServiceMonitor

With the default `application.serviceMonitor.enabled: true`, this chart declares a `ServiceMonitor` selecting the `student-api` Service's named `http` port, scraping `/metrics` every `15s`. This assumes:

1. The Prometheus Operator's CRDs are already installed (via `kube-prometheus-stack`, deployed by the `observability` ArgoCD `ApplicationSet` in `k8s/argo-manifests/`).
2. The app itself actually exposes Prometheus-format metrics at `/metrics` (e.g. via `prometheus-fastapi-instrumentator`) — the chart only wires up the scrape target, it doesn't add instrumentation to the app code.

If neither is true yet, set `application.serviceMonitor.enabled: false` to avoid a dangling/erroring `ServiceMonitor`.

## Secrets — plain Secret vs. Vault/ESO

Two mutually exclusive ways `DB_PASSWORD` reaches the app and database, switched by a single flag:

**`externalSecrets.enabled: false`** (simplest, fine for a quick local minikube spin-up):
- `secret.yaml` renders a static `Secret` from `secret.data.DB_PASSWORD` (base64-encoded by the template).
- No dependency on Vault/ESO being installed at all.

**`externalSecrets.enabled: true`** (the shipped default — production-shaped):
- `secret.yaml` does **not** render.
- `eso-components/cluster-secretstore.yaml` creates a `ClusterSecretStore` (`vault-backend`) authenticating to Vault via Kubernetes auth, using the `eso-vault-auth` ServiceAccount and Vault role `student-api-role`.
- `eso-components/eso-service-account.yaml` creates that `ServiceAccount` plus the `Role`/`RoleBinding` that let ESO's controller SA (`external-secrets`, in the `external-secrets` namespace) mint a token for it via the Kubernetes `TokenRequest` API.
- `eso-components/external-secret.yaml` creates an `ExternalSecret` that syncs `secret/student-api/db`'s `password` field from Vault into a Kubernetes `Secret` named `student-api-secret` (same name the Deployment/StatefulSet already reference via `envFrom`/`valueFrom` — so nothing else in the chart needs to change when you flip this flag), refreshed hourly.

This mode assumes Vault is already unsealed, has the KV v2 engine enabled at `secret/`, the `student-api-role` Kubernetes-auth role already written, and `secret/student-api/db` already populated — none of which this chart does for you. Full walkthrough: **[`k8s/helm/README.md`](../helm/README.md)**.

## Known caveats / things to check before a real deploy

- **Default DB password is `postgres`**, committed in plain text in `values.yaml`. Fine as a fallback when `externalSecrets.enabled: false` for local dev; override it (`--set secret.data.DB_PASSWORD=<real-password>` or a values override file) for anything else, and prefer leaving `externalSecrets.enabled: true` so the real value never has to live in `values.yaml` at all.
- **`storageClass.provisioner: hostpath.csi.k8s.io` is minikube-specific.** Set `storageClass.create: false` and point `database.persistence.storageClassName` at a real StorageClass (`gp3`, `pd-ssd`, etc.) on any cloud cluster.
- **The `eso-components/*` templates hardcode `namespace: student-api`** in the `ServiceAccount`/`Role`/`RoleBinding`/`ExternalSecret` objects rather than templating `.Values.namespace`. If you ever deploy this release into a different namespace, these three templates won't follow — you'd need to patch them (or open a small PR to template that field) before `externalSecrets.enabled: true` would work correctly outside the `student-api` namespace.
- **No `metrics-server` bundled.** `application.hpa.enabled: true` will create a `HorizontalPodAutoscaler` object, but it won't actually scale anything without a metrics pipeline already present in the cluster.
- **Single-instance Postgres, no backups, no HA.** `database.replicas` beyond `1` isn't meaningful here (this is a bare StatefulSet, not a replicated Postgres operator) — treat this as a dev/bootcamp database, not a production one.
- **`_helpers.tpl`'s `student-api.labels` template is unused** — no resource in the chart currently applies it, so don't rely on `helm.sh/chart`/`app.kubernetes.io/*` labels being present on these objects yet.

## Common overrides — example scenarios

**Local minikube, no Vault, just get something running:**
```bash
helm install student-api . -n student-api --create-namespace \
  --set externalSecrets.enabled=false \
  --set secret.data.DB_PASSWORD=<a-real-local-password>
```

**Deploying onto a cloud cluster (no hostpath CSI, real StorageClass already exists):**
```bash
helm install student-api . -n student-api --create-namespace \
  --set storageClass.create=false \
  --set database.persistence.storageClassName=<your-cloud-storageclass> \
  --set application.nodeAffinity.enabled=false \
  --set database.nodeAffinity.enabled=false
```

**Turning on autoscaling (assuming metrics-server is already installed):**
```bash
helm upgrade student-api . -n student-api \
  --set application.hpa.enabled=true \
  --set application.hpa.maxReplicas=5
```

**Disabling Prometheus scraping (Prometheus Operator CRDs not installed yet):**
```bash
helm install student-api . -n student-api --create-namespace \
  --set application.serviceMonitor.enabled=false
```

## Uninstall

```bash
helm uninstall student-api -n student-api
```

The Postgres `PersistentVolumeClaim` created via `volumeClaimTemplates` is **not** deleted by `helm uninstall` (StatefulSet PVCs are intentionally left behind by Helm/Kubernetes to prevent accidental data loss) — delete it manually if you actually want the data gone:
```bash
kubectl delete pvc -n student-api -l app=postgres
```
With `storageClass.reclaimPolicy: Delete` (the default), deleting the PVC also deletes the underlying `PersistentVolume` and its data.

If this release was installed via the ArgoCD `Application` in `k8s/argo-manifests/student-api-application.yaml` rather than by hand, delete the `Application` object itself (or set its sync policy to non-automated first) — otherwise ArgoCD's `selfHeal` will simply reinstall the release the next time it reconciles.