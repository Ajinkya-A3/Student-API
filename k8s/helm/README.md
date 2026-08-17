# Vault + External Secrets Operator — Setup Guide

Standalone HashiCorp Vault (non-dev, Raft storage, manual Shamir unseal) backing
secrets for the `student-api` app via External Secrets Operator (ESO).

**Cluster:** 3-node minikube — `minikube` (control-plane, `workload=application`),
`minikube-m02` (`workload=database`), `minikube-m03` (`workload=dependencies`).

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Architecture](#2-architecture)
3. [Helm Values — Vault](#3-helm-values--vault)
4. [Helm Values — ESO](#4-helm-values--eso)
5. [Install](#5-install)
6. [Initialize & Unseal Vault](#6-initialize--unseal-vault)
7. [Enable KV Secrets Engine](#7-enable-kv-secrets-engine)
8. [Kubernetes Auth Method](#8-kubernetes-auth-method)
9. [Policy & Role](#9-policy--role)
10. [ESO ↔ Vault RBAC](#10-eso--vault-rbac)
11. [ClusterSecretStore & ExternalSecret](#11-clustersecretstore--externalsecret)
12. [Verify End-to-End](#12-verify-end-to-end)
13. [Vault UI](#13-vault-ui)
14. [Troubleshooting Log](#14-troubleshooting-log)

---

## 1. Prerequisites

| Tool | Purpose |
|---|---|
| `kubectl` | cluster management |
| `helm` | chart installs |
| `jq` | parsing `vault-init.json` (`sudo apt install -y jq` if missing) |
| minikube, 3 nodes, labeled per above | target cluster |

Confirm a `WaitForFirstConsumer`-capable storage class exists before starting
(see [§14](#14-troubleshooting-log) for why this matters):

```bash
minikube addons enable csi-hostpath-driver
kubectl get sc
```

---

## 2. Architecture

```
Vault (secret/student-api/db, path secret/, KV v2)
   │  kubernetes auth (role: student-api-role)
   ▼
ESO ClusterSecretStore (vault-backend)
   │  authenticates AS eso-vault-auth SA (student-api ns),
   │  token minted by ESO's own controller via TokenRequest API
   ▼
ExternalSecret (student-api-secret, student-api ns)
   │  syncs into
   ▼
K8s Secret: student-api-secret  ──►  consumed by Deployment/StatefulSet envFrom
```

Two separate trust chains, easy to conflate:

- **Vault → Kubernetes**: Vault calls the K8s `TokenReview` API to validate SA
  tokens presented to it. Requires Vault's own SA to have `system:auth-delegator`
  — **the vault-helm chart creates this ClusterRoleBinding automatically**, no
  manual step needed.
- **ESO → eso-vault-auth**: ESO's controller SA calls the K8s `TokenRequest` API
  to mint a short-lived token *for* `eso-vault-auth`, then presents that token to
  Vault. This one **does** need manual RBAC (§10) — it's unrelated to the point above.

---

## 3. Helm Values — Vault

`values-vault.yaml`:

```yaml
server:
  dev:
    enabled: false

  standalone:
    enabled: true
    config: |
      ui = true

      listener "tcp" {
        address     = "[::]:8200"
        cluster_address = "[::]:8201"
        tls_disable = 1
      }

      storage "raft" {
        path = "/vault/data"
      }

  dataStorage:
    enabled: true
    size: 1Gi
    storageClass: csi-hostpath-sc-wait

  ha:
    enabled: false

  nodeSelector:
    workload: dependencies

  service:
    enabled: true

ui:
  enabled: true
  serviceType: ClusterIP

injector:
  enabled: false
```

| Key | Value | Why |
|---|---|---|
| `server.dev.enabled` | `false` | No in-memory storage, no auto-unseal, no printed root token. Requires real init + manual unseal — the whole point of not using dev mode. |
| `server.standalone.enabled` | `true` | Single-server mode. Combined with `ha.enabled: false`, guarantees exactly 1 pod. |
| `server.standalone.config` | HCL block | Vault's actual server config: `ui = true` enables the web UI; `listener "tcp"` sets the API listener (`tls_disable = 1` is fine for cluster-internal minikube traffic — terminate real TLS here in production); `storage "raft"` persists state via integrated Raft storage. |
| `server.dataStorage.*` | `enabled: true, 1Gi, csi-hostpath-sc-wait` | PVC backing `/vault/data`. Without it, Raft state (and your unseal progress) lives on ephemeral pod storage and vanishes on restart. |
| `server.ha.enabled` | `false` | This — not `standalone` alone — is what pins you to 1 pod. `replicas` is ignored outside HA mode. |
| `server.nodeSelector` | `workload: dependencies` | Pins the pod to `minikube-m03`. |
| `server.service.enabled` | `true` | Creates the `vault` ClusterIP Service — the DNS name (`vault.vault.svc`) ESO's `ClusterSecretStore` points at. |
| `ui.enabled` / `serviceType` | `true` / `ClusterIP` | UI reachable in-cluster / via port-forward only, not internet-facing. |
| `injector.enabled` | `false` | Disables the Vault Agent Injector (sidecar-based secret injection) — not needed since ESO delivers secrets as native K8s Secrets instead. |

---

## 4. Helm Values — ESO

`values-eso.yaml`:

```yaml
installCRDs: true

webhook:
  port: 9443

certController:
  create: true

resources:
  requests:
    cpu: 10m
    memory: 32Mi
  limits:
    cpu: 100m
    memory: 128Mi

nodeSelector:
  workload: dependencies
```

| Key | Value | Why |
|---|---|---|
| `installCRDs` | `true` | Installs `ExternalSecret`/`SecretStore`/`ClusterSecretStore` CRDs with the release — no separate apply needed. |
| `webhook.port` | `9443` | Port for the validating admission webhook that checks ES/SS specs at `kubectl apply` time. |
| `certController.create` | `true` | Deploys the controller that generates and rotates the webhook's TLS cert. Without it the webhook has no cert and fails closed. |
| `resources.*` | small requests/limits | ESO's controller is lightweight — mostly polling Vault and writing Secrets. |
| `nodeSelector` | `workload: dependencies` | Same node as Vault — fine for this single-node-per-role cluster. |

---

## 5. Install

```bash
helm repo add hashicorp https://helm.releases.hashicorp.com
helm repo add external-secrets https://charts.external-secrets.io
helm repo update

kubectl apply -f storageclass-wait.yaml   # WaitForFirstConsumer SC, see §14

helm install vault hashicorp/vault \
  -n vault --create-namespace \
  -f values-vault.yaml

helm install external-secrets external-secrets/external-secrets \
  -n external-secrets --create-namespace \
  -f values-eso.yaml

kubectl get pods -n vault
kubectl get pods -n external-secrets
```

---

## 6. Initialize & Unseal Vault

One-time. Vault starts `Sealed: true` — it can't do anything until unsealed with
enough Shamir key shares.

```bash
kubectl exec -n vault vault-0 -- vault operator init \
  -key-shares=5 -key-threshold=3 -format=json > vault-init.json

# never commit this file — it holds your unseal keys and root token
echo "vault-init.json" >> .gitignore
```

```bash
kubectl exec -n vault vault-0 -- vault operator unseal "$(jq -r '.unseal_keys_b64[0]' vault-init.json)"

kubectl exec -n vault vault-0 -- vault operator unseal "$(jq -r '.unseal_keys_b64[1]' vault-init.json)"

kubectl exec -n vault vault-0 -- vault operator unseal "$(jq -r '.unseal_keys_b64[2]' vault-init.json)"
```

Confirm:
```bash
kubectl exec -n vault vault-0 -- vault status
# Sealed: false
```

Log in as root for the setup steps below (persists in the pod for subsequent `exec` calls):
```bash
export VAULT_ROOT_TOKEN=$(jq -r '.root_token' vault-init.json)
echo "$VAULT_ROOT_TOKEN"   # sanity check — should print hvs.xxxxx, not empty

kubectl exec -n vault vault-0 -- vault login "$VAULT_ROOT_TOKEN"
```

> **Restarting `vault-0` reseals it** (standalone mode, no auto-unseal configured).
> Re-run the 3 `vault operator unseal` commands after any restart — init only
> happens once, unseal happens every time the pod restarts.

---

## 7. Enable KV Secrets Engine

```bash
kubectl exec -n vault vault-0 -- vault secrets enable -path=secret kv-v2

kubectl exec -n vault vault-0 -- vault kv put secret/student-api/db password=postgres

kubectl exec -n vault vault-0 -- vault kv get secret/student-api/db
```

---

## 8. Kubernetes Auth Method

```bash
kubectl exec -n vault vault-0 -- vault auth enable kubernetes

kubectl exec -n vault vault-0 -- vault write auth/kubernetes/config \
  kubernetes_host="https://kubernetes.default.svc:443"
```

No `token_reviewer_jwt` needed — Vault auto-detects its own pod's in-cluster
service account for this. That SA needs `system:auth-delegator` to call
`TokenReview`, which the **vault-helm chart provisions automatically**
(`<release>-server-binding` ClusterRoleBinding) — confirmed via the chart's
[`server-clusterrolebinding.yaml`](https://github.com/hashicorp/vault-helm/blob/main/templates/server-clusterrolebinding.yaml)
template. No manual binding required.

---

## 9. Policy & Role

Least-privilege: scoped to just this app's path, bound to a per-app SA/namespace
rather than ESO's own controller identity.

```bash
kubectl exec -i -n vault vault-0 -- vault policy write student-api-policy - <<EOF
path "secret/data/student-api/*" {
  capabilities = ["read"]
}
EOF
```
> `-i` is required — without it, `kubectl exec`'s stdin isn't piped through and
> the heredoc content never reaches `vault policy write`, giving a confusing
> `'policy' parameter not supplied or empty` error.

```bash
kubectl exec -n vault vault-0 -- vault write auth/kubernetes/role/student-api-role \
  bound_service_account_names=eso-vault-auth \
  bound_service_account_namespaces=student-api \
  policies=student-api-policy \
  ttl=1h
```

Confirm both:
```bash
kubectl exec -n vault vault-0 -- vault policy read student-api-policy
kubectl exec -n vault vault-0 -- vault read auth/kubernetes/role/student-api-role
```

---

## 10. ESO ↔ Vault RBAC

`eso-vault-rbac.yaml`:

```yaml
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: eso-vault-auth
  namespace: student-api

---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: eso-token-creator
  namespace: student-api
rules:
  - apiGroups: [""]
    resources: ["serviceaccounts/token"]
    resourceNames: ["eso-vault-auth"]
    verbs: ["create"]

---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: eso-token-creator-binding
  namespace: student-api
subjects:
  - kind: ServiceAccount
    name: external-secrets
    namespace: external-secrets
roleRef:
  kind: Role
  name: eso-token-creator
  apiGroup: rbac.authorization.k8s.io
```

```bash
kubectl apply -f eso-vault-rbac.yaml
kubectl get sa eso-vault-auth -n student-api
```

`eso-vault-auth` is a bare identity — Vault only cares that a token exists
*for* it (validated via the role in §9). The `Role`/`RoleBinding` above is what
lets ESO's controller SA mint that token via the K8s `TokenRequest` API.

---

## 11. ClusterSecretStore & ExternalSecret

`clustersecretstore.yaml`:
```yaml
apiVersion: external-secrets.io/v1
kind: ClusterSecretStore
metadata:
  name: vault-backend
spec:
  provider:
    vault:
      server: "http://vault.vault.svc:8200"
      path: "secret"
      version: "v2"
      auth:
        kubernetes:
          mountPath: "kubernetes"
          role: "student-api-role"
          serviceAccountRef:
            name: eso-vault-auth
            namespace: student-api
```

`app-externalsecret.yaml`:
```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: student-api-secret
  namespace: student-api
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault-backend
    kind: ClusterSecretStore
  target:
    name: student-api-secret
    creationPolicy: Owner
  data:
    - secretKey: DB_PASSWORD
      remoteRef:
        key: secret/student-api/db
        property: password
```

```bash
kubectl apply -f clustersecretstore.yaml
kubectl get clustersecretstore vault-backend   # expect Valid / READY=True

kubectl delete secret student-api-secret -n student-api --ignore-not-found
kubectl apply -f app-externalsecret.yaml
kubectl get externalsecret -n student-api      # expect SecretSynced / READY=True
```

---

## 12. Verify End-to-End

```bash
# secret value actually landed
kubectl get secret student-api-secret -n student-api \
  -o jsonpath='{.data.DB_PASSWORD}' | base64 -d; echo

# confirm ESO owns it, not a stale manual Secret
kubectl get secret student-api-secret -n student-api -o yaml | grep -A3 ownerReferences

# rotation round-trip
kubectl exec -n vault vault-0 -- vault kv put secret/student-api/db password=rotated123
kubectl annotate externalsecret student-api-secret -n student-api force-sync=$(date +%s) --overwrite
sleep 3
kubectl get secret student-api-secret -n student-api \
  -o jsonpath='{.data.DB_PASSWORD}' | base64 -d; echo   # -> rotated123

# put it back
kubectl exec -n vault vault-0 -- vault kv put secret/student-api/db password=postgres
kubectl annotate externalsecret student-api-secret -n student-api force-sync=$(date +%s) --overwrite
```

---

## 13. Vault UI

```bash
kubectl port-forward -n vault svc/vault 8200:8200
```

Open **http://localhost:8200/ui**

- Method: **Token**
- Token: value of `root_token` in `vault-init.json` (`jq -r '.root_token' vault-init.json`)

> Root token is fine for poking around a bootcamp cluster, but isn't how you'd
> log into a real prod Vault day-to-day — that'd be a scoped auth method
> (userpass/OIDC/LDAP), with the root token revoked (`vault token revoke`)
> after initial bootstrap.

---

## 14. Troubleshooting Log

Real issues hit during this setup, kept here for reference / interview prep.

**`Error initializing storage of type raft: ... open /vault/data/node-id: permission denied`**
Vault runs as non-root (`runAsUser: 100`, `fsGroup: 1000`). Minikube's default
`standard` storage class (`k8s.io/minikube-hostpath`) doesn't reliably apply
`fsGroup` ownership, so the mounted dir came up root-owned. Fixed by switching
to the CSI hostpath driver (`minikube addons enable csi-hostpath-driver`).

**`0/3 nodes are available: ... didn't match PersistentVolume's node affinity`**
The CSI hostpath storage class defaults to `volumeBindingMode: Immediate` —
provisions the volume *before* the pod is scheduled, so it lands on whichever
node the CSI plugin runs on, ignoring the pod's `nodeAffinity`/`nodeSelector`.
Fixed with a custom StorageClass (`csi-hostpath-sc-wait`) using
`volumeBindingMode: WaitForFirstConsumer`, which defers provisioning until
after the scheduler picks a node.