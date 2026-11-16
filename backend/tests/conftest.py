import os

# The agent module builds its LiteLLM model at import; tests never call the model.
os.environ.setdefault("LITELLM_MODEL", "openai/test-model")
os.environ.setdefault("LITELLM_API_BASE", "http://localhost:9")
os.environ.setdefault("LITELLM_API_KEY", "test")
