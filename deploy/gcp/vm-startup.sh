#!/bin/sh
# GCE startup script (runs on every boot): install Docker Engine + Compose plugin once.
set -eu
if ! command -v docker >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y ca-certificates curl
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
fi
mkdir -p /opt/odn/releases /opt/odn/shared
chmod 700 /opt/odn/shared
