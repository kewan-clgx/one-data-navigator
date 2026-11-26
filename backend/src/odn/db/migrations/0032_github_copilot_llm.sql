-- GitHub Copilot as a user's AI model: the stored "key" is the user's GitHub OAuth token from the device flow.
ALTER TABLE user_llm_keys DROP CONSTRAINT user_llm_keys_provider_check;
ALTER TABLE user_llm_keys ADD CONSTRAINT user_llm_keys_provider_check
  CHECK (provider IN ('gemini', 'openai', 'anthropic', 'openai_compatible', 'github_copilot'));
