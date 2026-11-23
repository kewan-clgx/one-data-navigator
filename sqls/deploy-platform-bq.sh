#!/bin/bash
# One-time deployment script to make bigquery-public-data globally available.
# This script assumes:
# - Migration 0023_platform_connections.sql has been applied.
# - You have psql access to the production database.

set -e

DB_HOST="${1:-localhost}"
DB_PORT="${2:-5432}"
DB_NAME="${3:-odn}"
DB_USER="${4:-odn_app}"

echo "=================================================="
echo "Platform BigQuery Public Data Connection Setup"
echo "=================================================="
echo "Database: $DB_USER@$DB_HOST:$DB_PORT/$DB_NAME"
echo ""

# Check database connectivity
echo "[1/3] Testing database connectivity..."
psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "SELECT version();" > /dev/null
echo "✓ Connected"

# Apply the admin script
echo ""
echo "[2/3] Creating platform connection..."
psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" << 'EOF'
BEGIN;

-- Create platform-level bigquery-public-data connection
INSERT INTO connections (
  id,
  tenant_id,
  name,
  kind,
  config,
  secret_ref,
  allowed_objects,
  owner_user_id,
  created_at,
  updated_at
) VALUES (
  gen_random_uuid(),
  NULL,
  'bigquery-public-data',
  'bigquery',
  jsonb_build_object(
    'project', 'bigquery-public-data',
    'location', 'US'
  ),
  'connection:platform:shared:bq-public-data',
  ARRAY[
    'bigquery-public-data.samples.*',
    'bigquery-public-data.usa_names.*',
    'bigquery-public-data.austin_bikeshare.*',
    'bigquery-public-data.austin_311.*',
    'bigquery-public-data.austin_crime.*'
  ],
  NULL,
  now(),
  now()
)
ON CONFLICT (name) WHERE tenant_id IS NULL DO UPDATE
SET 
  config = EXCLUDED.config,
  secret_ref = EXCLUDED.secret_ref,
  allowed_objects = EXCLUDED.allowed_objects,
  updated_at = now();

COMMIT;
EOF

# Verify
echo ""
echo "[3/3] Verifying connection..."
psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "
SELECT '✓ Platform connection created:' AS status,
       id, name, kind, tenant_id, owner_user_id, created_at
FROM connections
WHERE tenant_id IS NULL
  AND kind = 'bigquery'
  AND config::text LIKE '%bigquery-public-data%';
"

echo ""
echo "=================================================="
echo "✓ Setup complete!"
echo "=================================================="
echo ""
echo "Next steps:"
echo "1. Configure credentials:"
echo "   - Upload key to Secret Manager, OR"
echo "   - Update secret_ciphertext column manually"
echo ""
echo "2. Test from QueryMuse frontend:"
echo "   - Log in as any user"
echo "   - Go to Settings → Connections"
echo "   - Click 'bigquery-public-data' (should appear in Shared)"
echo ""
echo "3. Run a query:"
echo "   SELECT * FROM bigquery-public-data.samples.shakespeare LIMIT 10"
echo ""
