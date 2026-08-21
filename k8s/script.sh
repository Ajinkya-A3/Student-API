#!/usr/bin/env bash
set -euo pipefail
# =========================================================
# Full setup script:
#   1. Docker Engine + Compose plugin
#   2. kubectl
#   3. Helm
#   4. Minikube (3-node cluster, docker driver)
#   5. CSI hostpath driver addon + StorageClass
#   6. Node labels: application / database / dependencies
# =========================================================
log() { echo -e "\n\033[1;32m==> $*\033[0m"; }
# -----------------------------
# 1. Docker Engine
# -----------------------------
log "Updating package index"
sudo apt-get update -y
log "Installing prerequisites"
sudo apt-get install -y \
  ca-certificates \
  curl \
  gnupg \
  lsb-release
log "Adding Docker's official GPG key"
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
log "Adding Docker's APT repository"
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
log "Updating package index again"
sudo apt-get update -y
log "Installing Docker Engine + CLI + containerd + Buildx + Compose"
sudo apt-get install -y \
  docker-ce \
  docker-ce-cli \
  containerd.io \
  docker-buildx-plugin \
  docker-compose-plugin
log "Enabling and starting Docker"
sudo systemctl enable docker
sudo systemctl start docker
log "Adding current user to docker group (avoids needing sudo for docker)"
sudo usermod -aG docker "$USER" || true
log "Verifying Docker"
sudo docker --version
sudo docker compose version
log "Applying docker group membership to current shell (newgrp docker)"
# newgrp starts a new subshell with the docker group active, so we re-exec
# the rest of this script inside that subshell using a marker env var to
# avoid an infinite loop.
if [ -z "${DOCKER_GROUP_APPLIED:-}" ]; then
  export DOCKER_GROUP_APPLIED=1
  exec sg docker "$0" "$@"
fi
# -----------------------------
# 2. kubectl
# -----------------------------
log "Installing kubectl"
KUBECTL_VERSION=$(curl -L -s https://dl.k8s.io/release/stable.txt)
curl -LO "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl"
curl -LO "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl.sha256"
echo "$(cat kubectl.sha256)  kubectl" | sha256sum --check
sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl
rm -f kubectl kubectl.sha256
kubectl version --client
# -----------------------------
# 3. Helm
# -----------------------------
log "Installing Helm"
curl -fsSL -o get_helm.sh https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3
chmod 700 get_helm.sh
sudo ./get_helm.sh
rm -f get_helm.sh
helm version
# -----------------------------
# 4. Minikube
# -----------------------------
log "Installing Minikube"
curl -LO https://storage.googleapis.com/minikube/releases/latest/minikube-linux-amd64
sudo install minikube-linux-amd64 /usr/local/bin/minikube
rm -f minikube-linux-amd64
minikube version
log "Starting 3-node Minikube cluster (docker driver)"
minikube start --nodes=3 --driver=docker
log "Cluster nodes:"
kubectl get nodes -o wide
# -----------------------------
# 5. CSI hostpath driver addon + StorageClass
# -----------------------------
log "Enabling csi-hostpath-driver addon"
minikube addons enable csi-hostpath-driver
log "Current storage classes:"
kubectl get sc
log "Deploying csi-hostpath-sc-wait StorageClass"
cat <<'EOF' > /tmp/csi-hostpath-sc-wait.yaml
---
# Source: student-api/templates/storageclass.yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: csi-hostpath-sc-wait
provisioner: hostpath.csi.k8s.io
reclaimPolicy: Delete
volumeBindingMode: WaitForFirstConsumer
allowVolumeExpansion: false
EOF
kubectl apply -f /tmp/csi-hostpath-sc-wait.yaml
rm -f /tmp/csi-hostpath-sc-wait.yaml
log "Storage classes after deployment:"
kubectl get sc
# -----------------------------
# 6. Label nodes by workload role
# -----------------------------
log "Labeling nodes"
kubectl label node minikube      workload=application  --overwrite
kubectl label node minikube-m02  workload=database      --overwrite
kubectl label node minikube-m03  workload=dependencies  --overwrite
log "Final node labels:"
kubectl get nodes --show-labels
log "Setup complete. NOTE: log out/in (or run 'newgrp docker') for the docker group change to take effect without sudo."