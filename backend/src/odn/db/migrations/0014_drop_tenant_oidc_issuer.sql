-- Tokens map to tenants through tenant_identity_providers (0009); the per-tenant issuer column is unused.
ALTER TABLE tenants DROP COLUMN oidc_issuer;
