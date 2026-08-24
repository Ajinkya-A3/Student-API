# Observability stack



**A note on the log-shipping component: this stack uses Grafana Alloy, not Promtail.** Promtail — Loki's original, purpose-built log-shipping agent — was put into a feature-freeze/maintenance-only state by Grafana Labs and is on a defined path to end-of-life, with Alloy named as its official replacement. Since this stack was being built from scratch rather than migrated off an existing Promtail install, there was no reason to start on the component Grafana itself is deprecating — `alloy/` is deployed here instead, configured (see below) to do exactly the job Promtail would have: tail container logs and push them to Loki. This is why the acronym "PLG" (Prometheus, Loki, **Grafana**) fits better than the older "PLG-with-Promtail" framing you'll see in a lot of older tutorials — the collector is swapped, everything else about the stack (Loki as the log store, Grafana as the query/visualization layer) is the same shape.

## Table of Contents

- [Stack overview](#stack-overview)
- [1. kube-prometheus-stack — metrics, alerting, dashboards](#1-kube-prometheus-stack--metrics-alerting-dashboards)
- [2. Loki — log storage](#2-loki--log-storage)
- [3. Alloy — log collection (replaces Promtail)](#3-alloy--log-collection-replaces-promtail)
- [4. blackbox-exporter — external/HTTP probing](#4-blackbox-exporter--externalhttp-probing)
- [5. postgres-exporter — database metrics](#5-postgres-exporter--database-metrics)
- [6. grafana-dashboards — dashboard delivery](#6-grafana-dashboards--dashboard-delivery)
- [How the six pieces fit together](#how-the-six-pieces-fit-together)
- [Shared conventions across every values file](#shared-conventions-across-every-values-file)
- [Adding the Slack webhook as a Vault-backed secret](#adding-the-slack-webhook-as-a-vault-backed-secret)
- [Things to change before a real deploy](#things-to-change-before-a-real-deploy)

## Stack overview

| Component | Role | Chart version | App version |
|---|---|---|---|
| `kube-prometheus-stack` | Metrics (Prometheus), alerting (Alertmanager), dashboards (Grafana), cluster/node metrics collectors | `88.5.0` | `v0.93.1` |
| `loki` | Log storage & query backend | `7.3.0` | `3.6.12` |
| `alloy` | Log collection agent — tails pod logs, ships to Loki (replaces Promtail) | `1.11.1` | `v1.18.1` |
| `prometheus-blackbox-exporter` | External black-box HTTP/TCP probing of key services | `11.17.2` | `v0.28.0` |
| `prometheus-postgres-exporter` | Postgres-specific metrics exporter | `8.2.0` | `v0.20.1` |
| `grafana-dashboards` | Small, locally-authored chart that ships this project's Grafana dashboard JSON as labeled ConfigMaps | `0.1.0` | — |

All six are vendored upstream charts (grafana-dashboards excepted, which is authored for this repo) — see each chart's own bundled `README.md` for full upstream documentation; this README explains the `values.*.yaml` override files actually used to deploy them into this cluster, and why each choice was made.

## 1. kube-prometheus-stack — metrics, alerting, dashboards

`k8s/helm/kube-prometheus-stack/values.kps.yaml` — installs Prometheus Operator, Prometheus, Alertmanager, node-exporter, kube-state-metrics, and Grafana together. This is the anchor of the whole stack: every other component here either feeds Prometheus (postgres-exporter, blackbox-exporter), feeds Grafana (Loki as a datasource, grafana-dashboards as content), or is scraped by it.

### node-exporter

```yaml
nodeExporter:
  enabled: true

prometheus-node-exporter:
  hostRootFsMount:
    enabled: true
```

Gives Prometheus per-node OS metrics — CPU, memory, disk, network — as opposed to per-pod metrics (which come from cAdvisor/kubelet, not node-exporter). It runs as a `DaemonSet`, one pod per node. `hostRootFsMount.enabled: true` bind-mounts the **node's real root filesystem** (read-only) into the exporter container — without it, node-exporter would only ever see the container's own tiny overlay filesystem's disk usage, not the actual node's. This is what feeds the `HighDiskUsage`/`HighCPUUsage` alert rules below.

### kube-state-metrics

```yaml
kubeStateMetrics:
  enabled: true

kube-state-metrics:
  metricLabelsAllowlist:
    - pods=[app.kubernetes.io/name]
```

The other half of cluster visibility: not resource usage, but **object state** — Deployment replica counts, pod restart counts, PVC phase — read from the Kubernetes API rather than cAdvisor. `metricLabelsAllowlist` explicitly opts `pods=[app.kubernetes.io/name]` into being surfaced as a Prometheus label; kube-state-metrics doesn't expose arbitrary Kubernetes labels by default (unbounded cardinality risk), so only the one this cluster actually filters/groups by in dashboards and alerts is allow-listed.

### Prometheus

```yaml
prometheus:
  prometheusSpec:
    retention: 15d
    nodeSelector:
      workload: dependencies
```

- `retention: 15d` overrides the chart's own default of `120h` (5 days) — two weeks is enough to compare week-over-week patterns without unbounded storage growth on a single-node PVC.
- `nodeSelector: workload: dependencies` pins Prometheus to the node `k8s/script.sh` labels `workload=dependencies` — the same node Vault, ESO, and the rest of the observability stack live on, deliberately separate from `workload=application`/`workload=database` so a metrics/alerting outage doesn't compete for resources with the app or its data.

```yaml
    serviceMonitorSelectorNilUsesHelmValues: false
    serviceMonitorSelector: {}
    serviceMonitorNamespaceSelector: {}
    podMonitorSelectorNilUsesHelmValues: false
    ruleSelectorNilUsesHelmValues: false
    ruleNamespaceSelector: {}
    probeSelectorNilUsesHelmValues: false
    probeSelector: {}
    probeNamespaceSelector: {}
```

The single most important setting in this whole stack. By default, `kube-prometheus-stack` only picks up `ServiceMonitor`/`PodMonitor`/`PrometheusRule`/`Probe` objects carrying **this Helm release's own** labels — fine for one umbrella chart, actively wrong here, where `student-api`'s `ServiceMonitor`, `postgres-exporter`'s `ServiceMonitor`, and `blackbox-exporter`'s `Probe` are all separate Helm releases/ArgoCD Applications with no knowledge of `kps`'s release labels. Setting every `*SelectorNilUsesHelmValues` to `false` with empty `{}` selectors makes Prometheus watch **every** matching object cluster-wide, regardless of which release created it — the thing that actually makes cross-chart service discovery work in a GitOps setup like this one.

```yaml
    storageSpec:
      volumeClaimTemplate:
        spec:
          storageClassName: csi-hostpath-sc-wait
          accessModes: ["ReadWriteOnce"]
          resources:
            requests:
              storage: 10Gi
    resources:
      requests:
        cpu: 200m
        memory: 512Mi
      limits:
        memory: 1Gi
  service:
    type: ClusterIP
```

- `csi-hostpath-sc-wait` — the same `WaitForFirstConsumer` StorageClass used everywhere else in this cluster, so PV binding is deferred until the pod is actually scheduled and lands on the node the `nodeSelector` above picks.
- No CPU `limits`, only a `request` — Prometheus's CPU spikes hard during rule evaluation/compaction; capping it risks throttling exactly when it's doing real work. A memory limit exists because an OOM-killed Prometheus is recoverable (WAL replay) while CPU throttling just causes slow, confusing scrape delays.
- `service.type: ClusterIP` — internal only; reach it via port-forward, not exposed by default.

### Alertmanager

```yaml
alertmanager:
  alertmanagerSpec:
    nodeSelector:
      workload: dependencies
    resources:
      requests:
        cpu: 50m
        memory: 64Mi
    secrets:
      - alertmanager-slack-webhook
```

Same node pinning as Prometheus, deliberately small resources (Alertmanager evaluates routing rules and fires webhooks — it doesn't ingest or store time series). `secrets: [alertmanager-slack-webhook]` mounts an **existing** Kubernetes `Secret` of that name into `/etc/alertmanager/secrets/alertmanager-slack-webhook/` — this chart doesn't create that Secret itself. See [Adding the Slack webhook](#adding-the-slack-webhook-as-a-vault-backed-secret) below for how it's provisioned via Vault + ESO instead of committed to git.

```yaml
  config:
    route:
      receiver: slack-default
      group_by: ['alertname', 'severity']
      group_wait: 30s
      group_interval: 5m
      repeat_interval: 4h
    receivers:
      - name: "null"
      - name: slack-default
        slack_configs:
          - api_url_file: /etc/alertmanager/secrets/alertmanager-slack-webhook/webhook-url
            channel: '#alerts'
            send_resolved: true
```

- **The `"null"` receiver is load-bearing, not decoration.** `kube-prometheus-stack`'s own default config routes its built-in `Watchdog`/`InfoInhibitor` meta-alerts to a receiver literally named `"null"`. Helm deep-merges the `route` map (so that default child route survives) but **fully replaces** the `receivers` **list** — arrays overwrite, they don't merge. Omitting `"null"` here would leave the merged config still routing to a receiver that no longer exists, and Alertmanager refuses to reconcile with `undefined receiver "null" used in route`. This was hit and diagnosed during actual setup — don't remove this entry, it looks like dead config but isn't.
- `api_url_file` (not `api_url`) points at the *file* the mounted Secret produces rather than embedding the webhook URL as a literal string — the entire point being that the URL never appears in this values file, the stored Helm release, or git.
- `group_by`/`group_wait`/`group_interval`/`repeat_interval` batch simultaneous alerts into one Slack message, wait 30s to catch stragglers before the first send, cap updates to once per 5 minutes per group, and re-notify a still-firing alert at most every 4 hours.

### PrometheusRule groups (alerting rules)

Declared under `additionalPrometheusRulesMap.student-api-alerts`:

| Group | Alert | Condition | Severity | Notes |
|---|---|---|---|---|
| `node-resources` | `HighCPUUsage` | Avg idle CPU < 20% for 5m, per node | `warning` | From node-exporter's `node_cpu_seconds_total`. |
| `node-resources` | `HighDiskUsage` | Available filesystem < 15% for 5m, excluding `tmpfs`/`overlay` | `warning` | Filter avoids false alarms on ephemeral/overlay mounts. |
| `app-http` | `HighErrorRate` | 5xx > 5% of request rate over 10m | `critical` | Needs the app's own HTTP metrics middleware, not node/kube-state metrics. |
| `app-http` | `HighLatencyP90` | p90 latency > 500ms for 5m | `info` | A slow tail alone isn't necessarily an incident. |
| `app-http` | `HighLatencyP95` | p95 latency > 800ms for 5m | `warning` | |
| `app-http` | `HighLatencyP99` | p99 latency > 1s for 5m | `critical` | Escalating severity p90→p95→p99 is deliberate — a slow tail is normal, a slow median-adjacent tail is not. |
| `critical-pod-restarts` | `CriticalServiceRestarted` | Any restart in 10m for pods matching `postgres.*\|vault.*\|argocd.*` | `critical` | Scoped only to stateful/critical infra — app pod restarts (rollouts, HPA, evictions) are routine and deliberately excluded. |

Every rule uses `for: <duration>` before firing — this keeps Prometheus from flapping on a single noisy scrape.

### Grafana

```yaml
grafana:
  nodeSelector:
    workload: dependencies
  adminPassword: admin
  sidecar:
    dashboards:
      enabled: true
      label: grafana_dashboard
      folder: /tmp/dashboards
      searchNamespace: ALL
      folderAnnotation: grafana_folder
      provider:
        foldersFromFilesStructure: true
    datasources:
      enabled: true
```

- **The dashboard sidecar, not manually imported dashboards.** `sidecar.dashboards.enabled: true` runs a small controller alongside Grafana watching for `ConfigMap`s labeled `grafana_dashboard: "1"` **cluster-wide** (`searchNamespace: ALL`) and mounts their contents into Grafana's dashboard folder automatically — this is exactly how the `grafana-dashboards` chart (§6 below) ships this project's dashboards. No dashboard JSON is manually clicked through the UI.
- `folderAnnotation`/`foldersFromFilesStructure` let each dashboard ConfigMap declare its target Grafana folder via annotation, instead of everything dumping into one flat list.
- `datasources.enabled: true` is the same sidecar pattern for datasource definitions.
- The Prometheus datasource is added automatically by this chart itself; **`additionalDataSources: [Loki]`** is the only one declared by hand, pointed at `loki-gateway.observability.svc.cluster.local` (the log store from §2), with `uid: loki` giving it a stable reference so dashboards can point at it by UID rather than by name.
- `adminPassword: admin` is a placeholder, committed in plain text, so the cluster is immediately usable without a first-login flow during the bootcamp — see [caveats](#things-to-change-before-a-real-deploy).
- `persistence.enabled: true` on the same `csi-hostpath-sc-wait` StorageClass — without it, dashboard edits vanish on pod restart.
- `service.type: ClusterIP` — internal only, same reasoning as Prometheus.

## 2. Loki — log storage

`k8s/helm/loki/values.loki.yaml` — the log-storage backend Alloy ships to and Grafana queries.

```yaml
loki:
  auth_enabled: false
  commonConfig:
    replication_factor: 1
  storage:
    type: filesystem
```

- `auth_enabled: false` — no multi-tenant auth between components; every write/query uses the implicit `fake` tenant. Correct for a single-cluster, single-team setup like this one — multi-tenancy exists in Loki for genuinely multi-team SaaS-style deployments, which this isn't.
- `replication_factor: 1` — no replication of log data across multiple Loki instances. Consistent with running a single binary (see `deploymentMode` below); replication only makes sense once you're horizontally scaling ingesters.
- `storage.type: filesystem` — logs land on local disk (via the PVC below), not an object store (S3/GCS/Azure Blob). The simplest possible backend, appropriate for a single-node deployment; swapping to `type: s3` (or similar) is the change needed to make this durable beyond a single PVC.

```yaml
  schemaConfig:
    configs:
      - from: "2024-01-01"
        store: tsdb
        object_store: filesystem
        schema: v13
        index:
          prefix: loki_index_
          period: 24h
```

`store: tsdb` + `schema: v13` is Loki's current-generation indexing scheme (superseding the older `boltdb-shipper`/`v11` combination still seen in a lot of older tutorials) — better index compression and query performance. `period: 24h` rotates to a fresh index table daily, which keeps any single index file from growing unbounded and makes retention-based cleanup (below) operate on whole daily chunks rather than needing to rewrite a single giant index.

```yaml
  limits_config:
    retention_period: 168h        # 7 days
    ingestion_rate_mb: 10
    ingestion_burst_size_mb: 20
    max_label_names_per_series: 15
    max_streams_per_user: 10000
```

- `retention_period: 168h` (7 days) — deliberately shorter than Prometheus's 15-day metric retention: logs are far higher volume than metrics for the same time window, so a tighter window keeps the single-node filesystem store from filling up, on the assumption that anything needing longer-than-a-week log retrospection would already have been caught by an alert (which does persist longer, since Alertmanager notifications land in Slack, outside Loki entirely).
- `ingestion_rate_mb`/`ingestion_burst_size_mb` — caps sustained (10MB/s) and burst (20MB/s) ingestion per tenant, protecting Loki from being overwhelmed by a runaway logging loop in the app.
- `max_label_names_per_series`/`max_streams_per_user` — cardinality guardrails. Loki's cost model is dominated by the number of unique *label combinations* (streams), not log volume — an unbounded label set (e.g. accidentally labeling by request ID or timestamp) is the single most common way to blow up a Loki deployment's memory and query latency. Capping both here is a deliberate ceiling matched to Alloy's relabeling rules (§3), which only ever attach a small, fixed set of labels (`namespace`, `pod`, `container`, `node`, `app`).

```yaml
deploymentMode: SingleBinary

singleBinary:
  replicas: 1
  nodeSelector:
    workload: dependencies
  persistence:
    enabled: true
    storageClass: csi-hostpath-sc-wait
    size: 10Gi
  resources:
    requests:
      cpu: 200m
      memory: 256Mi
    limits:
      memory: 512Mi

read:
  replicas: 0
write:
  replicas: 0
backend:
  replicas: 0
```

`deploymentMode: SingleBinary` runs every Loki component (distributor, ingester, querier, compactor) in one process/pod, rather than Loki's `SimpleScalable` or `Distributed` modes which split those into separate scalable deployments — appropriate at this cluster's log volume, and simpler to operate. Explicitly zeroing `read`/`write`/`backend` replicas matters: those are the component groups the `SimpleScalable` mode uses, and leaving them at the chart's own defaults alongside `SingleBinary` would deploy *both* topologies' pods redundantly.

```yaml
gateway:
  enabled: true
  nodeSelector:
    workload: dependencies

monitoring:
  serviceMonitor:
    enabled: true
  selfMonitoring:
    enabled: false
  lokiCanary:
    enabled: false
```

- `gateway.enabled: true` — an nginx reverse proxy in front of Loki's actual API, giving a single stable Service name (`loki-gateway`) to route both writes (Alloy) and reads (Grafana) through, rather than every client needing to know Loki's internal component topology. This is the exact hostname Alloy's `loki.write` config (§3) and Grafana's `additionalDataSources` (§1) both point at.
- `selfMonitoring.enabled: false` / `lokiCanary.enabled: false` — the chart's built-in Grafana Agent-based self-monitoring and synthetic canary-log generator are both switched off. Self-monitoring would otherwise deploy its own separate log-shipping agent just to monitor Loki — redundant here since Alloy already ships every pod's logs (including Loki's own), and the canary is a synthetic log-volume generator mostly useful for validating a brand-new production Loki install, not needed for this cluster's actual traffic.
- `monitoring.serviceMonitor.enabled: true` — the one piece of self-monitoring kept: exposes Loki's own operational metrics (ingestion rate, query latency, etc.) to Prometheus, picked up automatically thanks to §1's `serviceMonitorSelector: {}` override.

## 3. Alloy — log collection (replaces Promtail)

`k8s/helm/alloy/values.alloy.yaml` — the agent that actually reads container logs off each node and ships them to Loki. As covered at the top of this README, this is Alloy rather than Promtail because Promtail is in Grafana's own EOL/maintenance-freeze path and Alloy is its designated successor; there was no reason to build new on the deprecated path.

```yaml
controller:
  type: daemonset
```

Runs one Alloy pod per node — required, since container log files live on the node's local disk (`/var/log/pods/...`), not anywhere centrally accessible; a `Deployment` wouldn't have visibility into every node's logs the way a `DaemonSet` does.

```yaml
alloy:
  enableReporting: false
```

Disables Alloy's own phone-home usage-reporting to Grafana Labs — an operational-privacy choice with no functional effect on log shipping.

```yaml
  mounts:
    varlog: true
    dockercontainers: true
```

Mounts `/var/log` (where kubelet symlinks each pod's log files) and `/var/lib/docker/containers` (where the *actual* log files the symlinks point to physically live, container-runtime-dependent) into the Alloy pod. Both are needed together: `/var/log/pods/.../*.log` on most nodes is a symlink chain that ultimately resolves into the container runtime's own log directory, and without the second mount those symlinks would dangle inside Alloy's container even though `varlog: true` alone looks sufficient. This exact gap — Alloy able to see the `/var/log/pods` paths but failing to actually read any log content because the symlink targets weren't mounted — was a real issue hit while setting up this stack, which is why both mounts are listed explicitly here rather than assuming one implies the other.

```yaml
  configMap:
    create: true
    content: |-
      logging {
        level  = "info"
        format = "logfmt"
      }

      loki.write "default" {
        endpoint {
          url = "http://loki-gateway.observability.svc.cluster.local/loki/api/v1/push"
        }
      }
```

Alloy's own config language ("River"/Alloy syntax, not YAML) is embedded as a raw multi-line string here. `loki.write "default"` is the sink — every log line collected below eventually flows into this one push endpoint, `loki-gateway` (§2's gateway Service), over Loki's native push API.

```yaml
      discovery.kubernetes "pods" {
        role = "pod"
      }

      discovery.relabel "pod_logs" {
        targets = discovery.kubernetes.pods.targets

        rule { source_labels = ["__meta_kubernetes_namespace"];              target_label = "namespace" }
        rule { source_labels = ["__meta_kubernetes_pod_name"];               target_label = "pod" }
        rule { source_labels = ["__meta_kubernetes_pod_container_name"];     target_label = "container" }
        rule { source_labels = ["__meta_kubernetes_pod_node_name"];          target_label = "node" }
        rule { source_labels = ["__meta_kubernetes_pod_label_app"];          target_label = "app" }

        rule {
          source_labels = ["__meta_kubernetes_pod_uid", "__meta_kubernetes_pod_container_name"]
          separator     = "/"
          regex         = "(.*)/(.*)"
          target_label  = "__path__"
          replacement   = "/var/log/pods/*$1/$2/*.log"
        }

        rule { source_labels = ["__meta_kubernetes_namespace"];          regex = "student-api"; action = "keep" }
        rule { source_labels = ["__meta_kubernetes_pod_container_name"]; regex = "student-api"; action = "keep" }
      }
```

`discovery.kubernetes "pods"` watches the Kubernetes API for every pod on the node (Alloy's equivalent of Promtail's `kubernetes_sd_config`). `discovery.relabel` then does two jobs at once:
1. **Attaches the fixed, low-cardinality label set** (`namespace`, `pod`, `container`, `node`, `app`) that Loki's `limits_config.max_label_names_per_series: 15` (§2) is sized around — deliberately not attaching anything higher-cardinality (request IDs, timestamps, etc.).
2. **Builds the on-disk log file glob** (`__path__`) from the pod UID + container name, matching how kubelet actually names container log directories under `/var/log/pods/`.
3. **Filters down to only the `student-api` namespace and container**, via two `keep` rules at the end. This stack deliberately does **not** ship every pod's logs cluster-wide to Loki — only the application's own logs. Everything else (Postgres, Vault, ArgoCD, the observability stack's own components) is left to Prometheus/Alertmanager for metrics-based visibility instead; this keeps Loki's log volume and cardinality bounded to exactly the one component whose logs are actually useful to grep through (`app/logger.py`'s structured JSON events), rather than ingesting noisy infrastructure logs nobody queries.

```yaml
      local.file_match "pod_logs" {
        path_targets = discovery.relabel.pod_logs.output
      }

      loki.source.file "pod_logs" {
        targets    = local.file_match.pod_logs.targets
        forward_to = [loki.process.application_logs.receiver]
      }

      loki.process "application_logs" {
        stage.cri {}
        forward_to = [loki.write.default.receiver]
      }
```

- **`local.file_match` is not optional boilerplate** — `discovery.relabel` only produces a `__path__` *glob pattern* (`/var/log/pods/*<uid>/student-api/*.log`), it does not itself expand that glob into real files on disk. `local.file_match` is the component that actually resolves the glob into concrete file paths that exist right now on this node. Omitting it (an easy mistake to make coming from Promtail, where this expansion happens implicitly inside the scrape config) means `loki.source.file` receives zero real targets and silently ships nothing — this was one of the real failure modes hit configuring Alloy for this project, alongside the `dockercontainers` mount gap above.
- `loki.source.file` is the actual tailer — reads from the resolved file targets, forwards raw lines onward.
- `stage.cri {}` parses the CRI/containerd log line format (timestamp + stream + tag prefix that every container runtime wraps log lines in) before the line reaches Loki, so what lands in Loki is the application's actual JSON log line, not `2026-08-24T10:00:00.123456789Z stdout F {"event": "..."}` with the CRI wrapper still attached.

## 4. blackbox-exporter — external/HTTP probing

`k8s/helm/prometheus-blackbox-exporter/values.blackbox.yaml` — synthetic, black-box HTTP/TCP probes of a fixed set of endpoints, complementing the (white-box, in-process) metrics the app/Postgres exporters expose. The distinction matters: white-box metrics can only tell you a service is unhealthy if the process is still alive enough to serve `/metrics` at all; blackbox probing catches the case where the process is completely down, DNS is broken, or the network path itself is the problem — from outside the process.

```yaml
config:
  modules:
    http_2xx:
      prober: http
      timeout: 5s
      http:
        preferred_ip_protocol: ip4
        valid_status_codes: [200, 301, 302]
    tcp_connect:
      prober: tcp
      timeout: 5s
    http_2xx_tls_skip_verify:
      prober: http
      timeout: 5s
      http:
        preferred_ip_protocol: ip4
        tls_config:
          insecure_skip_verify: true
```

Three probe modules, only two currently used (see `targets` below):
- `http_2xx` — a plain HTTP probe accepting `200`/`301`/`302` as healthy, `preferred_ip_protocol: ip4` to avoid any IPv6 resolution weirdness on a cluster that isn't dual-stack.
- `tcp_connect` — a raw TCP connect probe, defined but not currently wired to a target; kept available for probing something that doesn't speak HTTP.
- `http_2xx_tls_skip_verify` — kept as an unused spare specifically for when ArgoCD's or Vault's UI TLS gets turned back on (both currently run plain HTTP in this cluster, per the targets below) — at that point the plain `http_2xx` module would fail TLS verification against self-signed/internal certs, and this module exists ready to swap in without needing to author it from scratch under time pressure.

```yaml
serviceMonitor:
  enabled: true
  defaults:
    interval: 30s
    scrapeTimeout: 10s
    module: http_2xx
  targets:
    - name: argocd-server
      url: http://argocd-server.argocd.svc.cluster.local
      module: http_2xx
    - name: vault
      url: http://vault.vault.svc.cluster.local:8200/v1/sys/health
      module: http_2xx
    - name: student-api
      url: http://student-api.student-api.svc.cluster.local/api/v1/health
      module: http_2xx
  selfMonitor:
    enabled: true
```

Three targets, all core control-plane/data-plane components this cluster can't function without: ArgoCD (if it's down, GitOps reconciliation stops), Vault (if it's down, ESO-synced secrets go stale), and the app itself (probed via the same `/api/v1/health` liveness path the Kubernetes probes in the `student-api` chart use — but from Prometheus's perspective as an external client, not kubelet's). `vault`'s URL specifically hits `/v1/sys/health` rather than the bare root — Vault's health endpoint returns meaningful HTTP status codes based on seal/standby state, which a plain root-path probe wouldn't surface.

## 5. postgres-exporter — database metrics

`k8s/helm/prometheus-postgres-exporter/values.postgres.yaml` — the white-box counterpart to blackbox-exporter's external Postgres... except this stack doesn't actually probe Postgres via blackbox (no `tcp_connect` target defined for it); this exporter is Postgres's only real observability path, using Postgres's own internal statistics views (`pg_stat_activity`, `pg_stat_database`, etc.) that only a real authenticated connection can read.

```yaml
nodeSelector:
  workload: dependencies

config:
  datasource:
    host: postgres.student-api.svc.cluster.local
    user: postgres
    password: postgres
    port: "5432"
    database: student_db
    sslmode: disable

serviceMonitor:
  enabled: true
  namespace: observability
  interval: 30s
```

- `host: postgres.student-api.svc.cluster.local` — this exporter deliberately reaches **across namespaces**: it runs in `observability` (pinned, like everything else here, to the `workload=dependencies` node) but connects to the headless `postgres` Service living in the `student-api` namespace, using Kubernetes' standard cross-namespace Service DNS form (`<service>.<namespace>.svc...`).
- `sslmode: disable` — matches the app's own Postgres connection, which also doesn't use TLS in-cluster (in-cluster traffic isn't encrypted anywhere in this stack currently — see caveats).
- **`password: postgres` is a plain-text credential committed directly in this values file** — unlike the app's own `DB_PASSWORD`, which is Vault/ESO-backed by default in the `student-api` chart (`externalSecrets.enabled: true`). This exporter's config currently bypasses that entirely and hardcodes the same default local dev password. See [caveats](#things-to-change-before-a-real-deploy) — the fix is the same Vault/ESO pattern already used for the app's own secret and demonstrated for the Slack webhook below, just pointed at `config.datasource.passwordSecret` (which this chart supports natively) instead of the inline `config.datasource.password`.
- `serviceMonitor.namespace: observability` is set explicitly (redundant given §1's `serviceMonitorNamespaceSelector: {}` already watches every namespace, but harmless and self-documenting).

## 6. grafana-dashboards — dashboard delivery

`k8s/helm/grafana-dashboards/` — a small, locally-authored chart (not vendored upstream, unlike the other five) whose entire job is turning a folder of dashboard JSON files into the labeled `ConfigMap`s Grafana's sidecar (§1) auto-discovers.

```yaml
# values.yaml
namespace: observability
grafanaFolder: "Observability"
```

Two values total. `grafanaFolder` sets the Grafana UI folder every dashboard from this chart lands in (via the `grafana_folder` annotation the template attaches — see below).

```yaml
# templates/dashboard-configmaps.yaml
{{- range $path, $_ := .Files.Glob "dashboards/*.json" }}
{{- $name := base $path | trimSuffix ".json" }}
apiVersion: v1
kind: ConfigMap
metadata:
  name: dashboard-{{ $name }}
  namespace: {{ $.Values.namespace }}
  labels:
    grafana_dashboard: "1"
  annotations:
    grafana_folder: {{ $.Values.grafanaFolder | quote }}
data:
  {{ $name }}.json: |
{{ $.Files.Get $path | nindent 4 }}
{{- end }}
```

The entire chart is this one template: `.Files.Glob "dashboards/*.json"` picks up every `.json` file dropped into `dashboards/` at chart authoring time (no values-file wiring needed to add a new dashboard — just add the file and it's included on the next `helm upgrade`/ArgoCD sync), and each one becomes its own `ConfigMap` named `dashboard-<filename>`, labeled `grafana_dashboard: "1"` — the exact label §1's `sidecar.dashboards.label` watches for.

Current dashboards shipped this way:

| File | Covers |
|---|---|
| `node-exporter-1860.json` | Standard community node-exporter dashboard (dashboard ID 1860 on grafana.com) — per-node CPU/memory/disk/network. |
| `kube-state-13332.json` | Standard community Kubernetes cluster-state dashboard (ID 13332) — pod/deployment/PVC status from kube-state-metrics. |
| `postgres-9628.json` | Standard community Postgres dashboard (ID 9628) — driven by postgres-exporter's metrics (§5). |
| `blackbox-7587.json` | Standard community blackbox-exporter dashboard (ID 7587) — probe success/latency for the three targets in §4. |
| `error-logs.json` | A custom, locally-authored dashboard querying Loki (not Prometheus) directly for the app's error-level log lines — the one dashboard here that exercises the Loki datasource rather than Prometheus. |

The numeric suffixes are the dashboards' IDs on grafana.com's public dashboard library — imported as-is rather than hand-built, standard practice for well-covered exporters (node-exporter, kube-state-metrics, postgres-exporter, blackbox-exporter all have mature, widely-used community dashboards); only `error-logs.json`, which is specific to this app's own log shape, was authored from scratch.

## How the six pieces fit together

```
                                      ┌─────────────────────────────────────┐
                                      │          student-api namespace      │
                                      │                                     │
                                      │  ┌─────────────┐   ┌──────────────┐ │
                                      │  │ Student API │   │   PostgreSQL │ │
                                      │  │    Pods     │   │ StatefulSet  │ │
                                      │  └──────┬──────┘   └───────┬──────┘ │
                                      └─────────┼──────────────────┼────────┘
                                                │ logs             │ metrics
                                                ▼                  ▼
                                      ┌────────────────┐   ┌─────────────────┐
                                      │     Alloy      │   │ PostgreSQL      │
                                      │   DaemonSet    │   │   Exporter      │
                                      │                │   └────────┬────────┘
                                      │ student-api    │            │
                                      │ logs only      │            │
                                      └───────┬────────┘            │
                                              │ push logs           │ scrape
                                              ▼                     │
                                      ┌────────────────┐            │
                                      │      Loki      │            │
                                      │  SingleBinary  │            │
                                      │   + Gateway    │            │
                                      └───────┬────────┘            │
                                              │ query               │
                                              │                     │
                                              ▼                     ▼
                                      ┌─────────────────────────────────┐
                                      │           Prometheus            │
                                      │                                 │
                                      │  • PostgreSQL Exporter          │
                                      │  • Node Exporter (every node)   │
                                      │  • kube-state-metrics           │
                                      │  • Blackbox Exporter            │
                                      │    (Argo CD / Vault / App)      │
                                      └───────────────┬─────────────────┘
                                                      │
                                              metrics / queries
                                                      │
                         ┌────────────────────────────┴───────────────────┐
                         │                                                │
                         ▼                                                ▼
                 ┌─────────────────┐                              ┌──────────────┐
                 │     Grafana     │                              │ Alertmanager │
                 │                 │                              │              │
                 │ Prometheus DS   │                              │ Alert rules  │
                 │ Loki DS         │                              │              │
                 │                 │                              └──────┬───────┘
                 │ Dashboards ◄────┼── ConfigMap + sidecar               │
                 └─────────────────┘                                     │
                                                                         │ alerts
                                                                         ▼
                                                                  ┌──────────────┐
                                                                  │    Slack     │
                                                                  │  #alerts     │
                                                                  └──────────────┘
```

Metrics and logs travel two entirely separate paths (Prometheus pull-scrapes vs. Alloy push-ships), converge only at Grafana as two datasources, and alerting is a side-branch off Prometheus's own rule evaluation — Loki has no alerting path wired up in this stack (Loki *can* generate alerts via its own ruler, but that isn't configured here; all current `PrometheusRule`s in §1 are metrics-based).

## Shared conventions across every values file

A few patterns repeat across all six components, deliberately:

- **`nodeSelector: workload: dependencies`** on every component with a schedulable pod (Prometheus, Alertmanager, Grafana, Loki's `singleBinary`/`gateway`, postgres-exporter) — everything observability-related is co-located on the one node `k8s/script.sh` labels `workload=dependencies`, kept separate from `workload=application` and `workload=database` so an observability-stack issue can't starve the app or its data of resources (and vice versa). Alloy is the one exception, by necessity — as a `DaemonSet` it must run on every node, including the app and database nodes, to actually see their pods' logs; only pods in the `student-api` namespace/container are kept from its relabel filter, but the collector itself has to be everywhere.
- **`csi-hostpath-sc-wait` StorageClass** everywhere persistence is needed (Prometheus, Loki, Grafana) — the same `WaitForFirstConsumer` StorageClass the `student-api` chart defines, minikube-specific (see caveats).
- **`serviceMonitor.enabled: true`** on every component that exposes its own metrics (Loki, blackbox-exporter, postgres-exporter) — each relies on §1's cluster-wide `serviceMonitorSelector: {}` override to actually be picked up, since none of them are the `kube-prometheus-stack` Helm release itself.
- **Everything runs plain HTTP internally** — no in-cluster TLS between components (Postgres `sslmode: disable`, Vault/ArgoCD probed over `http://` in §4). Consistent across the whole stack, and flagged once here rather than repeated per component — see caveats for what closing this gap would look like.

## Adding the Slack webhook as a Vault-backed secret

The `alertmanager-slack-webhook` Secret `alertmanagerSpec.secrets` mounts (§1, Alertmanager) isn't created by `kube-prometheus-stack` itself, and shouldn't be created via a plain hand-typed `kubectl create secret` either — that would put the webhook URL in shell history or an unencrypted manifest. This repo already has a Vault + External Secrets Operator pipeline for exactly this pattern (see **[`k8s/helm/README.md`](README.md)** for the full Vault/ESO install this builds on) — the steps below extend that same pipeline to Alertmanager. This assumes Vault is already installed, initialized, and unsealed.

**1. Put the webhook URL into Vault** (inline, one command — nothing touches disk or git):
```bash
kubectl exec -n vault vault-0 -- vault kv put secret/observability/alertmanager \
  webhook-url="https://hooks.slack.com/services/T000000/B000000/XXXXXXXXXXXXXXXXXXXXXXXX"
```

**2. Write a least-privilege Vault policy scoped to just this path:**
```bash
kubectl exec -i -n vault vault-0 -- vault policy write alertmanager-policy - <<EOF
path "secret/data/observability/alertmanager" {
  capabilities = ["read"]
}
EOF
```
(`-i` is required for the heredoc's stdin to reach `vault policy write` through `kubectl exec`.)

**3. Create a Vault Kubernetes-auth role bound to a new, dedicated ServiceAccount:**
```bash
kubectl exec -n vault vault-0 -- vault write auth/kubernetes/role/alertmanager-role \
  bound_service_account_names=eso-alertmanager-auth \
  bound_service_account_namespaces=observability \
  policies=alertmanager-policy \
  ttl=1h
```

**4. Create that ServiceAccount and the RBAC letting ESO mint a token for it** (mirrors `k8s/student-api/templates/eso-components/eso-service-account.yaml`, re-namespaced):
```bash
cat <<'EOF' | kubectl apply -f -
apiVersion: v1
kind: ServiceAccount
metadata:
  name: eso-alertmanager-auth
  namespace: observability
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: eso-token-creator
  namespace: observability
rules:
  - apiGroups: [""]
    resources: ["serviceaccounts/token"]
    resourceNames: ["eso-alertmanager-auth"]
    verbs: ["create"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: eso-token-creator-binding
  namespace: observability
subjects:
  - kind: ServiceAccount
    name: external-secrets
    namespace: external-secrets
roleRef:
  kind: Role
  name: eso-token-creator
  apiGroup: rbac.authorization.k8s.io
EOF
```

**5. Create a `ClusterSecretStore` for this identity** (the existing `vault-backend` store used by `student-api` is pinned to a different SA/namespace, so this new identity needs its own store):
```bash
cat <<'EOF' | kubectl apply -f -
apiVersion: external-secrets.io/v1
kind: ClusterSecretStore
metadata:
  name: vault-backend-observability
spec:
  provider:
    vault:
      server: "http://vault.vault.svc:8200"
      path: "secret"
      version: "v2"
      auth:
        kubernetes:
          mountPath: "kubernetes"
          role: "alertmanager-role"
          serviceAccountRef:
            name: eso-alertmanager-auth
            namespace: observability
EOF
```

**6. Create the `ExternalSecret`** that syncs the webhook into the `alertmanager-slack-webhook` Secret Alertmanager expects:
```bash
cat <<'EOF' | kubectl apply -f -
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: alertmanager-slack-webhook
  namespace: observability
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault-backend-observability
    kind: ClusterSecretStore
  target:
    name: alertmanager-slack-webhook
    creationPolicy: Owner
  data:
    - secretKey: webhook-url
      remoteRef:
        key: secret/observability/alertmanager
        property: webhook-url
EOF
```
`secretKey: webhook-url` has to exactly match the filename `api_url_file` reads (`.../alertmanager-slack-webhook/webhook-url`) — the Secret's key becomes the mounted filename.

**7. Verify, then restart Alertmanager so it picks the mounted file up** (it only reads the mount at pod start):
```bash
kubectl get externalsecret alertmanager-slack-webhook -n observability   # expect SecretSynced / READY=True
kubectl rollout restart statefulset -n observability -l app.kubernetes.io/name=alertmanager
```

Force-fire a test alert to confirm delivery, without waiting for a real incident:
```bash
kubectl port-forward -n observability svc/kps-kube-promet-alertmanager 9093:9093 &
curl -H "Content-Type: application/json" -d '[{
  "labels": {"alertname": "TestAlert", "severity": "warning"},
  "annotations": {"summary": "Manual test alert — safe to ignore"}
}]' http://localhost:9093/api/v2/alerts
```

## Things to change before a real deploy

- **`grafana.adminPassword: admin`** (§1) and **`postgres-exporter`'s `config.datasource.password: postgres`** (§5) are both plain-text credentials committed to values files — the exact problem the Slack webhook walkthrough above solves. Grafana supports `admin.existingSecret`/`admin.existingSecretKey`; postgres-exporter supports `config.datasource.passwordSecret`. Both should point at Vault/ESO-managed Secrets the same way, rather than shipping real credentials in git.
- **`csi-hostpath-sc-wait` is minikube-specific** across every component that persists data (Prometheus, Loki, Grafana) — swap in a real cloud StorageClass for anything beyond this cluster, same caveat as the `student-api` chart.
- **No in-cluster TLS anywhere** — Postgres (`sslmode: disable`), Vault and ArgoCD's blackbox probes (plain `http://`), and every internal Service in this stack communicate unencrypted. Fine inside a single trusted cluster network for a bootcamp/dev setup; the `http_2xx_tls_skip_verify` blackbox module (§4) is specifically kept ready for the day ArgoCD/Vault TLS gets turned on.
- **Loki has no durable object-store backend** — `storage.type: filesystem` on a single PVC means log data doesn't survive losing that volume, and doesn't scale past what a single node's disk can hold. Moving to `SimpleScalable`/`Distributed` deployment mode with S3-compatible storage is the natural next step for anything beyond dev.
- **Alloy only ships `student-api` logs** — by design, per §3, but worth remembering if you ever need to debug Vault, ArgoCD, or the observability stack's own components via logs rather than metrics: none of that is in Loki today, only in `kubectl logs` directly.