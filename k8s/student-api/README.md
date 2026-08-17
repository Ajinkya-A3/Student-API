# student-api Helm chart

Converted from the raw manifests (`namespace.yml`, `configmap.yml`, `secret.yml`,
`app-deployment.yml`, `app-service.yml`, `postgres-statefulset.yml`,
`postgres-service.yml`, `sc-wait.yaml`, `eso-components/*`).

## Install

```bash
helm install student-api . -n student-api --create-namespace
```

(`namespaceCreate: true` in values.yaml also creates the Namespace resource itself,
so `--create-namespace` is optional/redundant — either works.)

## Key values to change before a real deploy

- `secret.data.DB_PASSWORD` — currently defaults to `postgres`. Override with
  `--set secret.data.DB_PASSWORD=<real-password>` or a values override file.
- `application.image.tag` / `database.image.tag` — pin to the versions you want.
- `externalSecrets.enabled` — set to `true` to source `DB_PASSWORD` from Vault via
  External Secrets Operator instead of the plain `Secret` template.

## Toggles

| Value | Default | Purpose |
|---|---|---|
| `namespaceCreate` | `true` | Create the Namespace resource |
| `application.hpa.enabled` | `false` | HorizontalPodAutoscaler for the Deployment |
| `serviceAccount.create` | `false` | Dedicated ServiceAccount for the app pod |
| `storageClass.create` | `true` | Create the `csi-hostpath-sc-wait` StorageClass |
| `externalSecrets.enabled` | `false` | Use ESO + Vault instead of the plain Secret |
| `database.containerSecurityContext.readOnlyRootFilesystem` | `false` | Lock postgres rootfs (adds emptyDir mounts for `/tmp` and `/var/run/postgresql`) |
