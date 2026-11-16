from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ODN_")

    env: str = "local"
    database_url: str = ""
    migrations_database_url: str = ""
    gcs_bucket: str = ""
    gcs_prefix: str = "odn"
    # Service-account JSON for the workspace bucket only; empty = runtime identity (Cloud Run).
    gcs_credentials_file: str = ""
    oidc_issuer: str = ""
    oidc_jwks_url: str = ""
    oidc_audience: str = "odn"
    oidc_tenant_claim: str = "tenant"
    # Local-only credential files for connections with secret_ref "file:<name>".
    keys_dir: str = "/app/keys"
    confirm_bytes_threshold: int = 10 * 1024**3
    max_bytes_billed: int = 100 * 1024**3
    query_timeout_s: int = 300
    worker_url: str = "http://worker:8000"
    # Shared secret for API -> worker calls (Cloud Run: replace with IAM-authenticated invocation).
    worker_token: str = ""
    worker_memory_limit: str = "1536MB"
    agent_url: str = "http://agent:8000"
    api_url: str = "http://api:8000"
    # Shared secret proving LLM-usage reports come from the agent runtime, not a browser.
    agent_token: str = ""
    # SQLAlchemy URL for durable ADK chat sessions (agent role, "adk" schema); empty = in-memory sessions.
    agent_session_db_url: str = ""
    # Default per-tenant LLM token limit per UTC day (prompt + completion); tenant_llm_budgets overrides it.
    llm_daily_token_budget: int = 2_000_000


settings = Settings()
