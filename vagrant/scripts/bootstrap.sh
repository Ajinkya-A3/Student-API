#!/usr/bin/env bash
set -euo pipefail

log() { echo -e "\n[bootstrap] $*\n"; }

update_system() {
  log "Updating apt package index"
  apt-get update -y
  apt-get upgrade -y
}

install_prereqs() {
  log "Installing prerequisite packages"
  apt-get install -y \
    ca-certificates \
    curl \
    gnupg \
    lsb-release
}

install_docker() {
  if command -v docker &>/dev/null; then
    log "Docker already installed, skipping"
    return
  fi

  log "Adding Docker's official GPG key and repo"
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc

  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
    $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
    tee /etc/apt/sources.list.d/docker.list > /dev/null

  apt-get update -y

  log "Installing Docker Engine + Compose plugin"
  apt-get install -y \
    docker-ce \
    docker-ce-cli \
    containerd.io \
    docker-buildx-plugin \
    docker-compose-plugin
}

enable_docker_service() {
  log "Enabling and starting docker service"
  systemctl enable docker
  systemctl start docker
}

add_vagrant_to_docker_group() {
  log "Adding vagrant user to docker group (no sudo needed for docker compose)"
  usermod -aG docker vagrant
}

verify_installation() {
  log "Verifying installation"
  docker --version
  docker compose version
}

main() {
  update_system
  install_prereqs
  install_docker
  enable_docker_service
  add_vagrant_to_docker_group
  verify_installation
  log "Bootstrap complete"
}

main "$@"
