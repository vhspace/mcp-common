"""Tests for agent_memory.config."""


class TestSettings:
    """Test Settings class loads defaults correctly."""

    def test_defaults(self):
        from agent_memory.config import Settings

        s = Settings()
        assert s.llm_provider == "anthropic"
        assert s.model_name == "claude-sonnet-4-6"
        assert s.group_id == "together-ops"
        assert s.neo4j_uri == "bolt://localhost:7687"

    def test_env_override(self, monkeypatch):
        from agent_memory.config import Settings

        monkeypatch.setenv("LLM_PROVIDER", "together")
        monkeypatch.setenv("MODEL_NAME", "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8")
        monkeypatch.setenv("GROUP_ID", "test-group")

        s = Settings()
        assert s.llm_provider == "together"
        assert s.model_name == "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8"
        assert s.group_id == "test-group"

    def test_decay_defaults(self):
        from agent_memory.config import Settings

        s = Settings()
        assert s.decay_base_strength_days == 30.0
        assert s.decay_temporal_weight == 0.4
        assert s.decay_usage_weight == 0.6
        assert s.decay_prune_threshold == 0.1

    def test_promotion_defaults(self):
        from agent_memory.config import Settings

        s = Settings()
        assert s.promote_min_confidence == 0.8
        assert s.promote_min_access_count == 3
