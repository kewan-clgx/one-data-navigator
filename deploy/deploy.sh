#!/usr/bin/env bash
# Deploy the committed HEAD to the production VM and manage it over IAP SSH (no CI needed).
#
#   deploy/deploy.sh env deploy/.env.prod   upload production settings (once, and when they change)
#   deploy/deploy.sh up                     ship HEAD, rebuild images, restart changed services
#   deploy/deploy.sh ps | logs [service...] | ssh
#   deploy/deploy.sh provision <args>       run python -m odn.db.provision <args> (see docs/deploy_gcp_vm.md)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/deploy/gcp/config.sh"

COMPOSE="sudo docker compose -f docker-compose.yml -f docker-compose.prod.yml"
CURRENT="$ODN_REMOTE_DIR/current"

remote() {
  gcloud compute ssh "$ODN_VM" --project "$GCP_PROJECT" --zone "$GCP_ZONE" --tunnel-through-iap \
    --command "$1" "${@:2}"
}

cmd_env() {
  local file="${1:?usage: deploy.sh env <file>}"
  [[ -f "$file" ]] || { echo "no such file: $file" >&2; exit 1; }
  remote "sudo install -d -m 700 $ODN_REMOTE_DIR/shared && sudo tee $ODN_REMOTE_DIR/shared/.env >/dev/null && sudo chmod 600 $ODN_REMOTE_DIR/shared/.env" <"$file"
  echo "uploaded $file -> $ODN_VM:$ODN_REMOTE_DIR/shared/.env (run 'deploy.sh up' to apply)"
}

cmd_up() {
  cd "$ROOT"
  if ! git diff --quiet HEAD -- || [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
    echo "warning: uncommitted changes are NOT deployed (only HEAD is shipped)" >&2
  fi
  local sha release
  sha=$(git rev-parse --short HEAD)
  release="$ODN_REMOTE_DIR/releases/$sha"
  echo "== shipping $sha to $ODN_VM"
  git archive --format=tar HEAD | remote "set -e
    sudo test -f $ODN_REMOTE_DIR/shared/.env || { echo 'missing .env: run deploy.sh env <file> first' >&2; exit 1; }
    sudo rm -rf $release && sudo mkdir -p $release && sudo tar -x -C $release
    sudo ln -sfn $ODN_REMOTE_DIR/shared/.env $release/.env
    sudo ln -sfn $release $CURRENT
    cd $CURRENT && $COMPOSE up -d --build --remove-orphans
    sudo docker image prune -f >/dev/null
    cd $ODN_REMOTE_DIR/releases && ls -1t | tail -n +6 | xargs -r sudo rm -rf
    cd $CURRENT && $COMPOSE ps"
}

case "${1:-}" in
  env) shift; cmd_env "$@" ;;
  up) cmd_up ;;
  ps) remote "cd $CURRENT && $COMPOSE ps" ;;
  logs) shift; remote "cd $CURRENT && $COMPOSE logs -f --tail 200 $*" -- -t ;;
  ssh) remote "cd $CURRENT 2>/dev/null; exec bash -l" -- -t ;;
  provision)
    shift
    remote "cd $CURRENT && $COMPOSE run --rm --no-deps api python -m odn.db.provision $(printf '%q ' "$@")"
    ;;
  *) sed -n '2,8p' "$0"; exit 1 ;;
esac
