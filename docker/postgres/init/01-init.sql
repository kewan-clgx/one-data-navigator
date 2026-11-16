-- Runs once on first init of the platform metadata database.
CREATE EXTENSION IF NOT EXISTS vector;

-- Runtime role used by the services. Not superuser and NOBYPASSRLS so RLS policies apply.
-- Tables are owned by the "odn" migration role; enable FORCE ROW LEVEL SECURITY on them as well.
CREATE ROLE odn_app LOGIN PASSWORD 'odn_app_local_dev' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;

GRANT CONNECT ON DATABASE odn TO odn_app;
GRANT USAGE ON SCHEMA public TO odn_app;
-- Table privileges are granted explicitly per table by the migrations (backend/src/odn/db/migrations).
