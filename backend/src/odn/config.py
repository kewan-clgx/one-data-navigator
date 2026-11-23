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
    # Which token maps to which tenant lives in the tenant directory (`python -m odn.db.provision`).
    # Comma-separated emails or "@domain" entries allowed into the issuer's fallback ('*') tenant; empty = anyone.
    oidc_default_tenant_emails: str = ""
    # Google Identity Platform (password sign-in tenants); empty project = disabled.
    idp_project: str = ""
    idp_api_key: str = ""
    # Slug of the tenant personal accounts join (email/password sign-up through its Identity Platform tenant;
    # personal Google accounts reach it through the issuer's '*' mapping). Empty = no personal email sign-up.
    personal_tenant: str = ""
    # Branded invite/reset emails via Resend (odn.platform.mailer); no key = Identity Platform's own emails.
    public_url: str = ""  # app origin used in email links, e.g. https://easyquery.ai
    resend_api_key: str = ""
    resend_api_key_secret: str = ""  # Secret Manager version name, e.g. projects/<p>/secrets/<s>/versions/latest
    mail_from: str = "EasyQuery <noreply@easyquery.ai>"
    mail_reply_to: str = "support@easyquery.ai"
    # Collibra (docs/collibra_int_phase5_plan.md): EasyQuery signs short-lived per-user tokens that Collibra trusts
    # through our JWKS (/.well-known/jwks.json). RSA private key PEM, or a Secret Manager version name; local dev
    # without either uses an ephemeral key.
    collibra_signing_key: str = ""
    collibra_signing_key_secret: str = ""
    collibra_token_ttl_s: int = 300
    collibra_timeout_s: float = 20.0
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
    # Per-user daily trial on the platform key for users without their own LLM key; 0 = no trial.
    llm_trial_user_daily_tokens: int = 200_000
    # 32 random bytes, base64url: encrypts user-supplied credentials (connection keys, LLM keys) at rest.
    # Losing or changing it makes stored user credentials unreadable.
    secrets_key: str = ""


settings = Settings()
