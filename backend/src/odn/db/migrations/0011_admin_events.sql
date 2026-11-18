-- Append-only audit trail for tenant administration (users, invites, shared connections).
CREATE TABLE admin_events (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id     uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
  actor_user_id uuid REFERENCES users (id) ON DELETE SET NULL,
  action        text NOT NULL CHECK (action ~ '^[a-z_]+\.[a-z_]+$'),
  target        text,
  detail        jsonb NOT NULL DEFAULT '{}',
  occurred_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX admin_events_tenant_time ON admin_events (tenant_id, occurred_at DESC);

ALTER TABLE admin_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE admin_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON admin_events
  USING (tenant_id = odn_current_tenant()) WITH CHECK (tenant_id = odn_current_tenant());

REVOKE ALL ON admin_events FROM odn_app;
GRANT SELECT, INSERT ON admin_events TO odn_app;
