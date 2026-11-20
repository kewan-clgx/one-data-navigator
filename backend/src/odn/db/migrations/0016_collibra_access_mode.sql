-- Collibra as an admin-managed data source (docs/collibra_int_phase5_plan.md): each environment has an access mode.
--   browser: every user reads Collibra with their own browser sign-in (QueryMuse Collibra Bridge extension).
--   token:   QueryMuse signs per-user tokens that Collibra trusts (needs the Collibra-side JWT setup + audience).
ALTER TABLE collibra_environments
  ADD COLUMN access_mode text NOT NULL DEFAULT 'browser' CHECK (access_mode IN ('browser', 'token'));
ALTER TABLE collibra_environments ALTER COLUMN audience DROP NOT NULL;
-- Rows created before this migration were token-mode (they required an audience).
UPDATE collibra_environments SET access_mode = 'token';
ALTER TABLE collibra_environments
  ADD CONSTRAINT collibra_token_needs_audience CHECK (access_mode <> 'token' OR audience IS NOT NULL);

-- Tenant admins manage their organization's environments (the API enforces the admin role; RLS the tenant).
GRANT INSERT, UPDATE, DELETE ON collibra_environments TO odn_app;
