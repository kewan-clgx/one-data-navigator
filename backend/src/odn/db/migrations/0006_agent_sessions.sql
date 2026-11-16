-- Durable ADK chat sessions live in their own schema, writable only by the agent runtime role.
-- The role is provisioned outside migrations (it needs a password); grants apply once it exists.
CREATE SCHEMA IF NOT EXISTS adk;
REVOKE ALL ON SCHEMA adk FROM PUBLIC;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'odn_agent') THEN
    GRANT USAGE, CREATE ON SCHEMA adk TO odn_agent;
  END IF;
END
$$;
