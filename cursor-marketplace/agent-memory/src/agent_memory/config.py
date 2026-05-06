"""Configuration for the agent memory system using pydantic-settings."""

from pydantic import model_validator
from pydantic_settings import BaseSettings


def _env_file_values() -> dict[str, str]:
    """Load values from .env files (last file wins) for fallback."""
    try:
        from dotenv import dotenv_values
    except ImportError:
        return {}
    merged: dict[str, str] = {}
    for path in (".env", "/workspaces/together/.env", "/workspaces/together/agent-memory/.env"):
        merged.update(dotenv_values(path))
    return merged


_API_KEY_FIELDS = frozenset({
    "anthropic_api_key", "openai_api_key", "together_api_key",
    "embedding_api_key", "voyage_api_key",
})


class Settings(BaseSettings):
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "changeme123"

    # LLM provider: "anthropic", "openai", or "together"
    llm_provider: str = "anthropic"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    together_api_key: str = ""
    model_name: str = "claude-sonnet-4-6"

    # Fallback LLM (used when primary provider exhausts retries)
    fallback_llm_provider: str = "together"
    fallback_model_name: str = "Qwen/Qwen3.5-397B-A17B"
    llm_max_retries: int = 3
    llm_retry_base_delay: float = 2.0

    # Embedding provider: "together", "openai", or "voyage"
    embedding_provider: str = "together"
    embedding_model: str = "intfloat/multilingual-e5-large-instruct"
    embedding_api_key: str = ""
    voyage_api_key: str = ""

    group_id: str = "together-ops"
    workspace_path: str = "/workspace"
    rules_output_dir: str = "/workspace/.cursor/rules"

    # Decay settings (hybrid Ebbinghaus + usage-based)
    decay_base_strength_days: float = 30.0
    decay_temporal_weight: float = 0.4
    decay_usage_weight: float = 0.6
    decay_prune_threshold: float = 0.1

    # Promotion settings
    promote_min_confidence: float = 0.8
    promote_min_access_count: int = 3
    promote_interval_hours: int = 6

    model_config = {
        "env_file": (".env", "/workspaces/together/.env", "/workspaces/together/agent-memory/.env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @model_validator(mode="after")
    def _fill_empty_keys_from_dotenv(self) -> "Settings":
        """Env vars set to empty string beat .env in pydantic-settings.

        Fall back to .env file values for API key fields so that
        ``TOGETHER_API_KEY=`` in the shell doesn't shadow the real key.
        """
        dotenv_vals = _env_file_values()
        for field in _API_KEY_FIELDS:
            if not getattr(self, field) and dotenv_vals.get(field.upper()):
                object.__setattr__(self, field, dotenv_vals[field.upper()])
        return self
