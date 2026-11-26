-- Prompt tokens served from the provider's cache (included in prompt_tokens). Cached input is billed far below
-- normal input (e.g. GitHub Copilot), so cost estimates need the split.
ALTER TABLE llm_usage ADD COLUMN cached_tokens bigint NOT NULL DEFAULT 0 CHECK (cached_tokens >= 0);
