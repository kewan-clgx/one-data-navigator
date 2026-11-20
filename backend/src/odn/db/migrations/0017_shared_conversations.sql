-- Sharing a conversation with teammates (docs/chat_session_share_plan.md): a frozen snapshot of the messages plus
-- copies of its result tables, charts and files, readable by chosen people (or everyone) in the same organization.
CREATE TABLE shared_conversations (
  id                        text PRIMARY KEY CHECK (id ~ '^[A-Za-z0-9_-]{22}$'),
  tenant_id                 uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
  owner_user_id             uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  source_thread_id          text NOT NULL CHECK (length(source_thread_id) BETWEEN 1 AND 200),
  source_session_id         text NOT NULL CHECK (source_session_id ~ '^[A-Za-z0-9._:-]{1,200}$'),
  title                     text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
  audience                  text NOT NULL DEFAULT 'people' CHECK (audience IN ('people', 'organization')),
  include_results           boolean NOT NULL,
  -- AG-UI messages as the chat renders them (user/assistant text, tool calls and results).
  messages                  jsonb NOT NULL,
  -- Copied result tables / charts / files: [{kind, name, ...metadata, object_key?}]; object keys under the share.
  artifacts                 jsonb NOT NULL DEFAULT '[]',
  used_personal_connections boolean NOT NULL DEFAULT false,
  used_collibra             boolean NOT NULL DEFAULT false,
  created_at                timestamptz NOT NULL DEFAULT now(),
  expires_at                timestamptz NOT NULL,
  revoked_at                timestamptz
);
CREATE INDEX shared_conversations_owner ON shared_conversations (tenant_id, owner_user_id, created_at DESC);

CREATE TABLE shared_conversation_recipients (
  share_id   text NOT NULL REFERENCES shared_conversations (id) ON DELETE CASCADE,
  tenant_id  uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
  user_id    uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  PRIMARY KEY (share_id, user_id)
);
CREATE INDEX shared_conversation_recipients_user ON shared_conversation_recipients (tenant_id, user_id);

CREATE TABLE shared_conversation_views (
  share_id            text NOT NULL REFERENCES shared_conversations (id) ON DELETE CASCADE,
  tenant_id           uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
  user_id             uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  first_viewed_at     timestamptz NOT NULL DEFAULT now(),
  continued_thread_id text,
  PRIMARY KEY (share_id, user_id)
);

-- Organization switch (admins); no row = sharing enabled.
CREATE TABLE tenant_sharing_settings (
  tenant_id  uuid PRIMARY KEY REFERENCES tenants (id) ON DELETE CASCADE,
  enabled    boolean NOT NULL DEFAULT true,
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE shared_conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE shared_conversations FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON shared_conversations
  USING (tenant_id = odn_current_tenant()) WITH CHECK (tenant_id = odn_current_tenant());
ALTER TABLE shared_conversation_recipients ENABLE ROW LEVEL SECURITY;
ALTER TABLE shared_conversation_recipients FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON shared_conversation_recipients
  USING (tenant_id = odn_current_tenant()) WITH CHECK (tenant_id = odn_current_tenant());
ALTER TABLE shared_conversation_views ENABLE ROW LEVEL SECURITY;
ALTER TABLE shared_conversation_views FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON shared_conversation_views
  USING (tenant_id = odn_current_tenant()) WITH CHECK (tenant_id = odn_current_tenant());
ALTER TABLE tenant_sharing_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_sharing_settings FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tenant_sharing_settings
  USING (tenant_id = odn_current_tenant()) WITH CHECK (tenant_id = odn_current_tenant());

-- Who may read a share (owner, chosen people, or everyone) is checked by the API on every request.
REVOKE ALL ON shared_conversations, shared_conversation_recipients, shared_conversation_views,
  tenant_sharing_settings FROM odn_app;
GRANT SELECT, INSERT, UPDATE ON shared_conversations TO odn_app;
GRANT SELECT, INSERT ON shared_conversation_recipients TO odn_app;
GRANT SELECT, INSERT, UPDATE ON shared_conversation_views TO odn_app;
GRANT SELECT, INSERT, UPDATE ON tenant_sharing_settings TO odn_app;
