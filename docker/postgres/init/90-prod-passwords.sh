#!/bin/sh
# First init only, after 01-init.sql. In prod (docker-compose.prod.yml sets the variables) replace the
# local-dev role passwords; locally it does nothing. Rotate later with ALTER ROLE ... PASSWORD.
# Written without `exit` because the entrypoint sources non-executable scripts.
if [ -n "${ODN_APP_DB_PASSWORD:-}" ] && [ -n "${ODN_AGENT_DB_PASSWORD:-}" ]; then
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -v app_pw="$ODN_APP_DB_PASSWORD" -v agent_pw="$ODN_AGENT_DB_PASSWORD" <<'SQL'
ALTER ROLE odn_app PASSWORD :'app_pw';
ALTER ROLE odn_agent PASSWORD :'agent_pw';
SQL
fi
