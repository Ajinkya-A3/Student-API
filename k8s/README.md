# GitOps Sync Ordering — Known Gap & Evaluated Fix

## The issue

`infrastructure-applicationset.yaml`, `observability-applicationset.yaml`, and
`argo-manifests/student-api-application.yaml` are three independent top-level
ArgoCD objects. Each is reconciled by its own controller loop, all with
`syncPolicy.automated: {prune: true, selfHeal: true}`. Nothing in this repo
enforces an order between them, or between the Applications an
ApplicationSet generates internally:

- **Within `observability`**: `kube-prometheus-stack` owns the
  `ServiceMonitor`/`Probe` CRDs. `loki`, `alloy`, `blackbox-exporter`,
  `postgres-exporter`, and `grafana-dashboards` all reference those CRDs, but
  sync in parallel with the chart that installs them.
- **Across groups**: nothing sequences `infrastructure` before
  `observability`, or either before `student-api`.

(Vault vs. External Secrets ordering inside `infrastructure` was reviewed
too, but isn't included here — it hasn't produced any observed issue in
practice, so it's not treated as part of this gap.)

**Why this hasn't caused a visible failure:** `selfHeal: true` means every
Application keeps retrying its own reconcile loop on a timer. Anything that
fails on its first sync because a dependency isn't ready yet tends to
succeed on a later retry once that dependency catches up — so a
from-scratch bootstrap *usually* converges, but not deterministically, and
not without transient errors on the first pass. That's a real gap on a
fresh cluster, a DR restore, or a cluster with tighter startup timeouts.

**Desired order:** `infrastructure` → `observability` → `student-api`.

## Why it's left as an ApplicationSet for now

The flat list-generator ApplicationSet is what makes adding a new chart a
one-line diff (`- name: foo / path: k8s/helm/foo / valuesFile: values.foo.yaml`)
instead of a new hand-written Application file per chart. For a
learning-project deploy cadence where charts get added/swapped often, that
ergonomics win outweighs the ordering risk today — especially since
`selfHeal` papers over the ordering gap in practice, as described above.
This is a conscious tradeoff, not an oversight.

## Fixes evaluated

Several ways to get real ordering were reviewed. None are implemented yet —
both a full fix and a lighter partial fix are documented below, in case
either gets picked up later.

| Approach | Verdict |
|---|---|
| `argocd.argoproj.io/sync-wave` directly on ApplicationSet-generated Applications | **Doesn't work.** Sync-wave orders resources *within one Application's sync operation*. Each generated Application is its own independent top-level object with its own sync operation — there's no shared operation for the annotation to order across. |
| ApplicationSet `strategy.type: RollingSync` | **Works, but the wrong tool here.** Confirmed against the [official docs](https://argo-cd.readthedocs.io/en/latest/operator-manual/applicationset/Progressive-Syncs/): RollingSync **forces `automated` sync off** on every Application it manages (and logs a warning if you leave it configured) — it drives syncs itself the same way a manual CLI/UI sync would. Any Application not matched by a `rollingSync.steps` label is silently excluded and needs manual syncing. Trading away `selfHeal` on the entire infra/observability layer just to get ordering is a bad trade for a cluster you want to self-correct. |
| A small Helm chart that renders child `Application` CRDs, deployed as one root App-of-Apps | **Full fix — deterministic, covers every gap.** Documented below. |
| Manually pull `kube-prometheus-stack` out and sync it before the rest of `observability` | **Partial fix — quick, but bounded.** Documented below. |

### Option A: a Helm chart that renders the child Applications

This is the [officially documented "App of Apps" pattern](https://argo-cd.readthedocs.io/en/latest/operator-manual/cluster-bootstrapping/)
combined with Helm templating — a common, well-established variant (used
in ArgoCD's own bootstrapping guide and widely written up elsewhere), not a
bespoke workaround.

**Scope: `infrastructure` and `observability` only — not `student-api`.**
`student-api`'s `ExternalSecret` needs `external-secrets` (and Vault behind
it) already up and serving before it can resolve `DB_PASSWORD` — so it still
has a real ordering dependency on this chart. But `student-api` is already
deployed as its own standalone `Application`
(`argo-manifests/student-api-application.yaml`), not an ApplicationSet — so
there's nothing broken there to fix by folding it into this chart too. The
simpler move is to leave `student-api-application.yaml` exactly as it is,
and apply/sync it *after* this chart reports healthy (specifically, after
the `external-secrets` wave). That gets the same effective ordering with a
smaller diff: this chart only needs to absorb the two ApplicationSets that
actually have the internal ordering problem.

### Why it solves the problem

All the generated `Application` objects become **resources inside one
parent Application's single sync operation** — the same relationship a
Deployment or Secret has to the Application that owns it. ArgoCD has a
built-in health check for the `Application` CRD itself, so:

- `sync-wave` on each child Application genuinely orders them.
- ArgoCD won't advance to the next wave until the current wave's child
  Application reports `Healthy`, not just `Synced`.
- Each child Application keeps its own `automated: {prune: true, selfHeal: true}`
  — nothing is forced off, unlike RollingSync.

### Sketch

```
k8s/argocd-apps/                 # new: a small Helm chart, not deployed by hand
├── Chart.yaml
├── values.yaml                  # the ordered list of everything to deploy
└── templates/
    └── application.yaml         # one template, ranges over values.yaml
```

`values.yaml` — the entire desired topology in one place, wave number
explicit and readable:

```yaml
applications:
  # --- infrastructure ---
  - name: vault
    path: k8s/helm/vault
    namespace: vault
    valuesFile: values.vault.yaml
    wave: 0

  - name: external-secrets
    path: k8s/helm/external-secrets
    namespace: external-secrets
    valuesFile: values.eso.yaml
    wave: 1

  # --- observability ---
  - name: kube-prometheus-stack
    path: k8s/helm/kube-prometheus-stack
    namespace: observability
    valuesFile: values.kps.yaml
    wave: 2                      # owns the ServiceMonitor/Probe CRDs

  - name: loki
    path: k8s/helm/loki
    namespace: observability
    valuesFile: values.loki.yaml
    wave: 3
  - name: alloy
    path: k8s/helm/alloy
    namespace: observability
    valuesFile: values.alloy.yaml
    wave: 3
  - name: blackbox-exporter
    path: k8s/helm/prometheus-blackbox-exporter
    namespace: observability
    valuesFile: values.blackbox.yaml
    wave: 3
  - name: postgres-exporter
    path: k8s/helm/prometheus-postgres-exporter
    namespace: observability
    valuesFile: values.postgres.yaml
    wave: 3
  - name: grafana-dashboards
    path: k8s/helm/grafana-dashboards
    namespace: observability
    valuesFile: values.yaml
    wave: 3
```

`templates/application.yaml` — one template rendered once per entry:

```yaml
{{- range .Values.applications }}
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: {{ .name }}
  namespace: argocd
  annotations:
    argocd.argoproj.io/sync-wave: "{{ .wave }}"
spec:
  project: default
  source:
    repoURL: https://github.com/Ajinkya-A3/Student-API.git
    targetRevision: main
    path: {{ .path }}
    helm:
      releaseName: {{ .name }}
      valueFiles:
        - {{ .valuesFile }}
  destination:
    server: https://kubernetes.default.svc
    namespace: {{ .namespace }}
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
      - ServerSideApply=true
---
{{- end }}
```

One root `Application` (created once, by hand or via `argocd app create`)
points at `k8s/argocd-apps`, syncs it, and ArgoCD takes it from there —
every future infra/observability addition is a new entry in `values.yaml`,
same ergonomics as the ApplicationSet list generator has today.

`student-api-application.yaml` stays exactly as it is today, untouched, and
gets applied as a separate step once this chart's root Application is
`Healthy` (as it need the external secret to be present also the CRDs for the Servicemonitor). That's a manual sequencing step,
same caveat as Option B below: ArgoCD doesn't enforce it, it just needs to
be the documented deploy order.

### Tradeoffs of this fix, honestly

**Gains:**
- Deterministic, health-gated ordering within the chart: `infrastructure` → `observability`.
- `selfHeal`/`automated` stays on for every component in the chart — no regression there.
- Bootstrap failure mode becomes legible: `kubectl get applications -n argocd`
  shows exactly which wave is stuck and why, instead of a pile of Applications
  independently retrying against missing dependencies.
- `student-api`'s dependency on `external-secrets` is satisfiable by a single
  documented manual step (apply it after this chart is healthy) instead of
  relying on `selfHeal` retries to eventually converge.

**Costs:**
- One more indirection layer (a chart whose only job is to emit `Application`
  manifests) versus the ApplicationSet's native list generator.
- Loses ApplicationSet's dedicated grouping in the Argo UI (`ApplicationSet`
  resource view) — the generated Applications still show up individually,
  just not clustered under one ApplicationSet object.
- `helm template` has no equivalent to ApplicationSet's `matrix`/`git`/
  `cluster` generators — irrelevant here since none are used, but would
  matter if this repo grew multi-cluster.

### Option B: manually sequence `kube-prometheus-stack` first

A lighter alternative for just the observability CRD race: pull
`kube-prometheus-stack` out of `observability-applicationset.yaml` into its
own standalone `Application`, apply/sync it first, then apply the
ApplicationSet for the remaining five charts (`loki`, `alloy`,
`blackbox-exporter`, `postgres-exporter`, `grafana-dashboards`) afterward.
By the time those five sync, the `ServiceMonitor`/`Probe` CRDs already
exist, so the race that would otherwise happen on the first bootstrap pass
doesn't.

**What this is, precisely:**
- An **operational convention**, not something ArgoCD enforces. Nothing
  stops someone applying both objects at once on a future re-bootstrap (new
  cluster, DR restore, a slightly different runbook) — the ordering only
  holds if whoever deploys it remembers the two-step sequence and waits
  between them. No health-gate, unlike sync-wave or RollingSync.
- **Scoped to one gap.** It only addresses the `kube-prometheus-stack` CRD
  race inside `observability`. It does nothing for `infrastructure` →
  `observability` → `student-api` ordering across groups.
- **Not actually new risk-reduction, mechanically.** Because `selfHeal: true`
  is already on every chart, `alloy`/`blackbox-exporter`/`postgres-exporter`
  failing their first sync (CRD not found yet) and succeeding on a retry a
  few seconds later once `kube-prometheus-stack` lands is *already what
  happens today* — with or without manually sequencing it. What this option
  buys is a **cleaner first pass**: no transient error events in the ArgoCD
  UI/logs, no wasted reconcile attempts. It moves the outcome from
  "eventually consistent" to "eventually consistent, faster and quieter,"
  not to "guaranteed correct on the first try."

**Cost:** none beyond the one-time manual step of applying two objects in
order instead of one.

**Verdict:** worth doing as a cheap, low-effort cleanup for the noisiest part
of the gap. Not a substitute for Option A if genuine cross-group ordering is
ever required — it's a bootstrap-hygiene fix, not a dependency guarantee.

## Verdict

Both options are sound and neither is implemented yet. The ApplicationSets
are left as-is because the current setup already converges correctly via
retry/selfHeal in practice — this is a test/demo-grade deployment, not a
production cluster with tight startup SLAs — and the remaining bootcamp
timeline is better spent on fundamentals than restructuring a working (if
non-deterministic) deploy path. Tracked here explicitly rather than left
implicit, so the tradeoff is a stated decision and not a gap discovered
later.
