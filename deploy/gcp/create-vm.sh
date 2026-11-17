#!/usr/bin/env bash
# One-time GCP setup for the production VM (safe to re-run; existing resources are kept).
# Requires: gcloud, authenticated as a project owner/editor of $GCP_PROJECT.
set -euo pipefail
cd "$(dirname "$0")"
source ./config.sh

g() { gcloud --project "$GCP_PROJECT" "$@"; }
exists() { "$@" >/dev/null 2>&1; }

echo "== APIs"
g services enable compute.googleapis.com secretmanager.googleapis.com iap.googleapis.com

echo "== Service account $ODN_VM_SA_EMAIL"
exists g iam service-accounts describe "$ODN_VM_SA_EMAIL" ||
  g iam service-accounts create "$ODN_VM_SA" --display-name "One Data Navigator VM"
# Connection credentials (e.g. an enterprise client's BigQuery key) live in Secret Manager as odn-<tenant_id>-*.
g projects add-iam-policy-binding "$GCP_PROJECT" --condition=None --quiet \
  --member "serviceAccount:$ODN_VM_SA_EMAIL" --role roles/secretmanager.secretAccessor >/dev/null
# Result workspace (Parquet artifacts under gs://$ODN_BUCKET/odn/).
gcloud storage buckets add-iam-policy-binding "gs://$ODN_BUCKET" \
  --member "serviceAccount:$ODN_VM_SA_EMAIL" --role roles/storage.objectAdmin >/dev/null
g projects add-iam-policy-binding "$GCP_PROJECT" --condition=None --quiet \
  --member "serviceAccount:$ODN_VM_SA_EMAIL" --role roles/logging.logWriter >/dev/null

echo "== Static IP"
exists g compute addresses describe "$ODN_VM-ip" --region "$GCP_REGION" ||
  g compute addresses create "$ODN_VM-ip" --region "$GCP_REGION"
IP=$(g compute addresses describe "$ODN_VM-ip" --region "$GCP_REGION" --format 'value(address)')

echo "== Firewall (HTTPS public, SSH only via IAP)"
exists g compute firewall-rules describe odn-allow-web ||
  g compute firewall-rules create odn-allow-web --network default --direction INGRESS \
    --allow tcp:80,tcp:443,udp:443 --source-ranges 0.0.0.0/0 --target-tags odn-web
exists g compute firewall-rules describe odn-allow-iap-ssh ||
  g compute firewall-rules create odn-allow-iap-ssh --network default --direction INGRESS \
    --allow tcp:22 --source-ranges 35.235.240.0/20 --target-tags odn-web

echo "== Daily disk snapshots (Postgres + Caddy certs live on the boot disk)"
exists g compute resource-policies describe odn-daily-snapshot --region "$GCP_REGION" ||
  g compute resource-policies create snapshot-schedule odn-daily-snapshot --region "$GCP_REGION" \
    --daily-schedule --start-time 08:00 --max-retention-days 14 --on-source-disk-delete keep-auto-snapshots

echo "== VM $ODN_VM"
if ! exists g compute instances describe "$ODN_VM" --zone "$GCP_ZONE"; then
  g compute instances create "$ODN_VM" --zone "$GCP_ZONE" \
    --machine-type "$ODN_VM_MACHINE" \
    --image-family debian-12 --image-project debian-cloud \
    --boot-disk-size "${ODN_VM_DISK_GB}GB" --boot-disk-type pd-balanced \
    --address "$IP" --tags odn-web \
    --service-account "$ODN_VM_SA_EMAIL" --scopes cloud-platform \
    --shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring \
    --metadata enable-oslogin=TRUE \
    --metadata-from-file startup-script=./vm-startup.sh
  g compute disks add-resource-policies "$ODN_VM" --zone "$GCP_ZONE" --resource-policies odn-daily-snapshot
fi

cat <<MSG

Done. Static IP: $IP
Next:
  1. DNS: A record  ${ODN_HOSTNAME:-odn.datahop.ai}  ->  $IP
  2. Fill a copy of deploy/.env.prod.example, then:  deploy/deploy.sh env <file> && deploy/deploy.sh up
  See docs/deploy_gcp_vm.md for the Google OAuth client and tenant/connection provisioning.
MSG
