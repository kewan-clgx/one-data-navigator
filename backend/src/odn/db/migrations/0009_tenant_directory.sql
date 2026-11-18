-- Tenant directory and user profiles (docs/Tenant_Isolation_User_system.md §4.1).
--
-- * tenant_domains / tenant_identity_providers are directory tables like `tenants`: the app role has no
--   grants and reaches them only through the SECURITY DEFINER functions below.
-- * A verified token maps to a tenant by (issuer, external_id): Google `hd` domain, Identity Platform
--   `firebase.tenant`, or the mock IdP's tenant claim. external_id '*' is an issuer's fallback tenant
--   (personal Google accounts -> the platform tenant).
-- * users gain issuer/status/invites; admins must be on a verified tenant domain; the last active admin
--   cannot be demoted, disabled or deleted.

-- Tenant profile ---------------------------------------------------------------------------------------
ALTER TABLE tenants
  ADD COLUMN status        text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
  ADD COLUMN auth_method   text NOT NULL DEFAULT 'google' CHECK (auth_method IN ('google', 'password', 'oidc', 'saml')),
  ADD COLUMN join_policy   text NOT NULL DEFAULT 'invite' CHECK (join_policy IN ('domain', 'invite')),
  ADD COLUMN idp_tenant_id text UNIQUE,
  ADD COLUMN theme         text NOT NULL DEFAULT 'default' CHECK (theme ~ '^[a-z0-9-]{1,40}$'),
  ADD COLUMN updated_at    timestamptz NOT NULL DEFAULT now(),
  ALTER COLUMN oidc_issuer DROP NOT NULL;

-- Existing tenants keep today's behaviour: anyone their issuer maps to may sign in.
UPDATE tenants SET join_policy = 'domain',
                   auth_method = CASE WHEN oidc_issuer = 'https://accounts.google.com' THEN 'google' ELSE 'oidc' END;

CREATE TABLE tenant_domains (
  domain      text PRIMARY KEY CHECK (domain ~ '^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$'),
  tenant_id   uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
  verified_at timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX tenant_domains_tenant ON tenant_domains (tenant_id);

CREATE TABLE tenant_identity_providers (
  issuer      text NOT NULL,
  external_id text NOT NULL CHECK (external_id <> ''),
  tenant_id   uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (issuer, external_id)
);
CREATE INDEX tenant_identity_providers_tenant ON tenant_identity_providers (tenant_id);

-- Non-Google tenants (local mock IdP) used the tenant claim as the slug.
INSERT INTO tenant_identity_providers (issuer, external_id, tenant_id)
SELECT oidc_issuer, slug, id FROM tenants
WHERE oidc_issuer IS NOT NULL AND oidc_issuer <> '' AND oidc_issuer <> 'https://accounts.google.com'
ON CONFLICT DO NOTHING;

-- User profile -----------------------------------------------------------------------------------------
-- The owner bypasses RLS only without FORCE; lift it for the backfill inside this transaction.
ALTER TABLE users NO FORCE ROW LEVEL SECURITY;

ALTER TABLE users
  ADD COLUMN issuer     text,
  ADD COLUMN status     text NOT NULL DEFAULT 'active' CHECK (status IN ('invited', 'active', 'disabled')),
  ADD COLUMN invited_by uuid REFERENCES users (id) ON DELETE SET NULL,
  ADD COLUMN invited_at timestamptz,
  ALTER COLUMN subject DROP NOT NULL;

UPDATE users u SET issuer = t.oidc_issuer FROM tenants t WHERE t.id = u.tenant_id AND u.issuer IS NULL;

ALTER TABLE users DROP CONSTRAINT users_tenant_id_subject_key;
CREATE UNIQUE INDEX users_identity ON users (tenant_id, issuer, subject) NULLS NOT DISTINCT WHERE subject IS NOT NULL;
CREATE UNIQUE INDEX users_tenant_email ON users (tenant_id, lower(email)) WHERE email IS NOT NULL;
-- An invited user has an email but no identity yet; everyone else has an identity.
ALTER TABLE users ADD CONSTRAINT users_identity_or_invite
  CHECK (subject IS NOT NULL OR (status = 'invited' AND email IS NOT NULL));

ALTER TABLE users FORCE ROW LEVEL SECURITY;

-- Admin rules ------------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION odn_email_domain(p_email text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN p_email LIKE '%_@_%' THEN lower(split_part(p_email, '@', 2)) END
$$;

-- Admins must use an email on one of the tenant's verified domains (tenants without domains are exempt).
CREATE OR REPLACE FUNCTION odn_check_admin_domain() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF NEW.role = 'admin'
     AND EXISTS (SELECT 1 FROM tenant_domains WHERE tenant_id = NEW.tenant_id)
     AND NOT EXISTS (SELECT 1 FROM tenant_domains
                     WHERE tenant_id = NEW.tenant_id AND verified_at IS NOT NULL
                       AND domain = odn_email_domain(NEW.email)) THEN
    RAISE EXCEPTION 'admin email must be on a verified domain of the tenant' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER users_admin_domain BEFORE INSERT OR UPDATE OF role, email ON users
  FOR EACH ROW EXECUTE FUNCTION odn_check_admin_domain();

-- Never leave a tenant that has an active admin without one (deleting the whole tenant is fine).
CREATE OR REPLACE FUNCTION odn_keep_last_admin() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM tenants WHERE id = OLD.tenant_id) THEN
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
  END IF;
  IF OLD.role = 'admin' AND OLD.status = 'active'
     AND (TG_OP = 'DELETE' OR NEW.role <> 'admin' OR NEW.status <> 'active')
     AND NOT EXISTS (SELECT 1 FROM users
                     WHERE tenant_id = OLD.tenant_id AND id <> OLD.id AND role = 'admin' AND status = 'active') THEN
    RAISE EXCEPTION 'cannot remove the last active admin of a tenant' USING ERRCODE = 'check_violation';
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;
CREATE TRIGGER users_keep_last_admin BEFORE UPDATE OF role, status OR DELETE ON users
  FOR EACH ROW EXECUTE FUNCTION odn_keep_last_admin();

-- Directory lookups for the app role -------------------------------------------------------------------
-- Token -> tenant. Exact (issuer, external_id) wins over the issuer's '*' fallback. domain_ok is true when
-- the tenant has no domains or the email's domain is one of its verified domains.
CREATE OR REPLACE FUNCTION odn_resolve_identity(p_issuer text, p_external_id text, p_email_domain text)
RETURNS TABLE (tenant_id uuid, slug text, status text, join_policy text, fallback boolean, has_domains boolean,
               domain_ok boolean)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT t.id, t.slug, t.status, t.join_policy, p.external_id = '*',
         EXISTS (SELECT 1 FROM tenant_domains d WHERE d.tenant_id = t.id),
         NOT EXISTS (SELECT 1 FROM tenant_domains d WHERE d.tenant_id = t.id)
         OR EXISTS (SELECT 1 FROM tenant_domains d
                    WHERE d.tenant_id = t.id AND d.verified_at IS NOT NULL AND d.domain = lower(p_email_domain))
  FROM tenant_identity_providers p JOIN tenants t ON t.id = p.tenant_id
  WHERE p.issuer = p_issuer AND p.external_id IN (lower(p_external_id), p_external_id, '*')
  ORDER BY p.external_id = '*'
  LIMIT 1
$$;

-- Sign-in routing: the tenant owning the email's verified domain, if the typed organization names it.
CREATE OR REPLACE FUNCTION odn_discover_tenant(p_org text, p_email_domain text)
RETURNS TABLE (slug text, name text, auth_method text, idp_tenant_id text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT t.slug, t.name, t.auth_method, t.idp_tenant_id
  FROM tenant_domains d JOIN tenants t ON t.id = d.tenant_id
  WHERE d.domain = lower(p_email_domain) AND d.verified_at IS NOT NULL AND t.status = 'active'
    AND lower(btrim(p_org)) IN (t.slug, lower(t.name))
$$;

-- The caller's own tenant profile (RLS context must be set to that tenant).
CREATE OR REPLACE FUNCTION odn_tenant_profile()
RETURNS TABLE (slug text, name text, theme text, auth_method text, join_policy text, idp_tenant_id text,
               domains text[])
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT t.slug, t.name, t.theme, t.auth_method, t.join_policy, t.idp_tenant_id,
         coalesce((SELECT array_agg(d.domain ORDER BY d.domain) FROM tenant_domains d
                   WHERE d.tenant_id = t.id AND d.verified_at IS NOT NULL), '{}')
  FROM tenants t WHERE t.id = odn_current_tenant()
$$;

DROP FUNCTION odn_resolve_tenant(text, text);

-- Directory and migration bookkeeping are owner-only (a default-privileges grant may exist on some databases).
REVOKE ALL ON tenant_domains, tenant_identity_providers, schema_migrations FROM odn_app;
REVOKE ALL ON FUNCTION odn_resolve_identity(text, text, text), odn_discover_tenant(text, text),
  odn_tenant_profile(), odn_check_admin_domain(), odn_keep_last_admin() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION odn_resolve_identity(text, text, text), odn_discover_tenant(text, text),
  odn_tenant_profile(), odn_email_domain(text) TO odn_app;
GRANT DELETE ON users TO odn_app;
