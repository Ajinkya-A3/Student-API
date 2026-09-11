# Decoupling `values.kps.yaml` content into standalone manifests

Each section below: the exact block to remove from `values.kps.yaml`, what stays,
the manifest that replaces it, and the setup needed to make it live — sized to
drop into your existing `k8s/student-api` chart, since ArgoCD already syncs
that path on every commit.

---

## 1. PrometheusRule (alerting rules)

### Remove from `values.kps.yaml`
```yaml
additionalPrometheusRulesMap:
  student-api-alerts:
    groups:
      - name: node-resources
        rules: [...]
      - name: app-http
        rules: [...]
      - name: critical-pod-restarts
        rules: [...]
```

### Keep in `values.kps.yaml` (already present, no change)
```yaml
prometheus:
  prometheusSpec:
    ruleSelectorNilUsesHelmValues: false
    ruleSelector: {}
    ruleNamespaceSelector: {}
```
`ruleSelector: {}` (an empty object, not `nil`) already tells the Prometheus
Operator to watch every `PrometheusRule` in every namespace — that's *why*
this decoupling works with zero further changes here.

### New file: `k8s/student-api/templates/prometheusrule.yaml`
```yaml
{{- if .Values.application.alerts.enabled }}
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: {{ .Values.application.name }}-alerts
  namespace: {{ .Values.namespace }}
  labels:
    {{- include "student-api.labels" . | nindent 4 }}
    release: kps
spec:
  groups:
    - name: app-http
      rules:
        - alert: HighErrorRate
          expr: |
            sum(rate(http_requests_total{status=~"5..", namespace="{{ .Values.namespace }}"}[10m]))
            / sum(rate(http_requests_total{namespace="{{ .Values.namespace }}"}[10m])) > 0.05
          for: 5m
          labels: {severity: critical}
          annotations:
            summary: "5xx error rate above 5% over the last 10m"

        - alert: HighLatencyP90
          expr: histogram_quantile(0.90, sum(rate(http_request_duration_seconds_bucket{namespace="{{ .Values.namespace }}"}[5m])) by (le)) > 0.5
          for: 5m
          labels: {severity: info}
          annotations:
            summary: "p90 request latency above 500ms"

        - alert: HighLatencyP95
          expr: histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{namespace="{{ .Values.namespace }}"}[5m])) by (le)) > 0.8
          for: 5m
          labels: {severity: warning}
          annotations:
            summary: "p95 request latency above 800ms"

        - alert: HighLatencyP99
          expr: histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{namespace="{{ .Values.namespace }}"}[5m])) by (le)) > 1
          for: 5m
          labels: {severity: critical}
          annotations:
            summary: "p99 request latency above 1s"

        - alert: RequestVolumeSpike
          expr: sum(rate(http_requests_total{namespace="{{ .Values.namespace }}"}[5m])) > 100
          for: 2m
          labels: {severity: info}
          annotations:
            summary: "Request rate spiked above 100 req/s"
{{- end }}
```

The `HighCPUUsage` / `HighDiskUsage` / `CriticalServiceRestarted` rules are
cluster-wide, not `student-api`-specific — those belong in a separate
`k8s/helm/kube-prometheus-stack/templates/` style location (or their own
tiny "platform-alerts" chart), not in the app chart. Keep those as a second,
similarly-shaped `PrometheusRule` owned by whoever owns cluster infra.

### Add to `k8s/student-api/values.yaml`
```yaml
application:
  alerts:
    enabled: true
```

### Setup needed
- None beyond a normal ArgoCD sync — `ruleSelector: {}` is already set, so
  the moment this template exists under `k8s/student-api/templates/`, the
  next sync of the `student-api` Application creates it and Prometheus
  picks it up automatically. No `helm upgrade` of the `kps` release required.

---

## 2. Probe (blackbox uptime check)

Your blackbox exporter is currently configured through that chart's own
`serviceMonitor.targets` list in `values.blackbox.yaml` — already one level
more decoupled than `values.kps.yaml` (separate chart, separate ArgoCD
ApplicationSet entry), but still a hardcoded static list owned by whoever
touches the observability stack. The CRD-native version moves ownership of
"is my endpoint up" to the app repo itself.

### Remove from `k8s/helm/prometheus-blackbox-exporter/values.blackbox.yaml`
```yaml
serviceMonitor:
  targets:
    - name: student-api
      url: http://student-api.student-api.svc.cluster.local/api/v1/health
      module: http_2xx
```
(Leave the `argocd-server` and `vault` entries — those are genuinely
platform-owned targets and have nowhere else to live.)

### Keep in `values.kps.yaml` (already present, no change)
```yaml
prometheus:
  prometheusSpec:
    probeSelectorNilUsesHelmValues: false
    probeSelector: {}
    probeNamespaceSelector: {}
```

### New file: `k8s/student-api/templates/probe.yaml`
```yaml
{{- if .Values.application.probe.enabled }}
apiVersion: monitoring.coreos.com/v1
kind: Probe
metadata:
  name: {{ .Values.application.name }}-uptime
  namespace: {{ .Values.namespace }}
  labels:
    {{- include "student-api.labels" . | nindent 4 }}
spec:
  jobName: blackbox
  interval: 30s
  module: http_2xx
  prober:
    url: prometheus-blackbox-exporter.observability.svc.cluster.local:9115
  targets:
    staticConfig:
      static:
        - http://{{ .Values.application.name }}.{{ .Values.namespace }}.svc.cluster.local{{ .Values.application.serviceMonitor.path | default "/api/v1/health" }}
      labels:
        target: {{ .Values.application.name }}
{{- end }}
```

### Add to `k8s/student-api/values.yaml`
```yaml
application:
  probe:
    enabled: true
```

### Setup needed
- `http_2xx` module must already exist in the blackbox-exporter's own
  `config.modules` (it does, in `values.blackbox.yaml` — unchanged).
- Nothing else — `probeSelector: {}` already matches cluster-wide, same
  mechanism as rules above.

---

## 3. AlertmanagerConfig (Slack routing)

This is the one where the CRD approach actually removes the bug class you
documented, not just moves the YAML.

### Replace in `values.kps.yaml`
```yaml
# BEFORE
alertmanager:
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
            title: '{{ .CommonLabels.alertname }} ({{ .CommonLabels.severity }})'
            text: '{{ range .Alerts }}{{ .Annotations.summary }}\n{{ end }}'
```

```yaml
# AFTER
alertmanager:
  alertmanagerSpec:
    alertmanagerConfigSelector: {}
    alertmanagerConfigNamespaceSelector: {}
    alertmanagerConfigMatcherStrategy:
      type: None      # don't force a namespace-label matcher onto child configs

  config:
    route:
      receiver: "null"      # top-level route still needs a default receiver
      group_by: ['alertname', 'severity']
    receivers:
      - name: "null"
```
Note what's gone: the `api_url_file` secret-mount plumbing, the Slack
receiver, and — because there's no longer a second custom entry in
`receivers`, only `"null"` — you no longer need the comment explaining
why `"null"` must stay. Helm's array-replace behavior never gets
triggered because you're not touching `receivers` beyond its default shape.

### New Secret (lives with the app, not in `observability`)
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: student-api-slack-webhook
  namespace: student-api
type: Opaque
stringData:
  webhook-url: "https://hooks.slack.com/services/REPLACE/ME"
```
Put this through External Secrets Operator (you already have ESO wired up
for the app's DB creds) rather than a plain committed Secret — a
`ClusterSecretStore` reference here keeps it consistent with the rest of
your Vault setup.

### New file: `k8s/student-api/templates/alertmanagerconfig.yaml`
```yaml
{{- if .Values.application.alerting.enabled }}
apiVersion: monitoring.coreos.com/v1alpha1
kind: AlertmanagerConfig
metadata:
  name: {{ .Values.application.name }}-slack
  namespace: {{ .Values.namespace }}
  labels:
    {{- include "student-api.labels" . | nindent 4 }}
spec:
  route:
    receiver: slack-default
    groupBy: ['alertname', 'severity']
    groupWait: 30s
    groupInterval: 5m
    repeatInterval: 4h
  receivers:
    - name: slack-default
      slackConfigs:
        - apiURL:
            name: student-api-slack-webhook
            key: webhook-url
          channel: '#alerts'
          sendResolved: true
          title: '{{ "{{ .CommonLabels.alertname }} ({{ .CommonLabels.severity }})" }}'
          text: '{{ "{{ range .Alerts }}{{ .Annotations.summary }}\n{{ end }}" }}'
{{- end }}
```
(The double-brace escaping above is because Helm templating and
Alertmanager's own Go templating both use `{{ }}` — the outer
`{{ "..." }}` stops Helm from trying to render Alertmanager's template
syntax at chart-render time.)

### Add to `k8s/student-api/values.yaml`
```yaml
application:
  alerting:
    enabled: true
```

### Setup needed
1. Bump the `kube-prometheus-stack` chart version if `alertmanagerConfigMatcherStrategy` isn't in the version you're pinned to (added in operator ≥0.68 / chart ≥45.x) — check `k8s/helm/kube-prometheus-stack/Chart.yaml`.
2. One `helm upgrade`/ArgoCD sync of the `kps` release to pick up the new `alertmanagerConfigSelector` values — this part genuinely does need a kps-release change, since it's Alertmanager's own watch scope, same category as the rule/probe selectors.
3. After that one-time change, every future Slack-routing edit for `student-api` only touches `k8s/student-api/`, never `values.kps.yaml` again.

---

## 4. Grafana dashboards — already decoupled, nothing to change

```yaml
grafana:
  sidecar:
    dashboards:
      enabled: true
      label: grafana_dashboard
      searchNamespace: ALL
```
This already means: any `ConfigMap` anywhere in the cluster labeled
`grafana_dashboard: "1"` gets picked up. Your separate
`k8s/helm/grafana-dashboards` chart (with `Files.Glob` templating) is
already the standalone-manifest version of this — that's *why* it's a
separate chart in the ApplicationSet instead of an entry here. No further
action needed; just worth being able to say "I already applied this
pattern here" if asked.

---

## 5. Grafana datasource (Loki) — same sidecar mechanism, currently unused for this

### Remove from `values.kps.yaml`
```yaml
grafana:
  additionalDataSources:
    - name: Loki
      type: loki
      uid: loki
      access: proxy
      url: http://loki-gateway.observability.svc.cluster.local
      isDefault: false
      jsonData:
        maxLines: 1000
```

### Keep in `values.kps.yaml` (already present, no change)
```yaml
grafana:
  sidecar:
    datasources:
      enabled: true
```

### New file: `k8s/helm/loki/templates/grafana-datasource.yaml` (or a small standalone manifest synced alongside the Loki chart, since the datasource describes Loki, not the app)
```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: loki-grafana-datasource
  namespace: observability
  labels:
    grafana_datasource: "1"
data:
  loki-datasource.yaml: |
    apiVersion: 1
    datasources:
      - name: Loki
        type: loki
        uid: loki
        access: proxy
        url: http://loki-gateway.observability.svc.cluster.local
        isDefault: false
        jsonData:
          maxLines: 1000
```

### Setup needed
- None beyond the sidecar flag already being on. This one is lower-value
  to decouple than the others (Loki's URL essentially never changes, and
  it's genuinely one cluster-wide datasource, not per-team content) — worth
  knowing the mechanism exists, but not necessarily worth doing unless
  you want to demonstrate the pattern consistently.

---

## Summary table

| Content | Stays in `values.kps.yaml`? | Moves to | Needs a kps release change? |
|---|---|---|---|
| Alert rules | No | `PrometheusRule` in app chart | No — selector already open |
| Uptime checks | No (currently in blackbox chart values) | `Probe` in app chart | No |
| Slack routing | Route skeleton only | `AlertmanagerConfig` in app chart | **Yes, once** (enable selector) |
| Dashboards | No | separate chart (already done) | No |
| Loki datasource | Optional | sidecar-labeled ConfigMap | No |
