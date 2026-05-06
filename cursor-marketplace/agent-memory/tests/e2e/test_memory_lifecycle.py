"""E2E test: full memory lifecycle (requires Neo4j + API keys)."""

import os

import pytest

pytestmark = [pytest.mark.e2e]

SKIP_REASON = "E2E tests require NEO4J_URI, ANTHROPIC_API_KEY, and TOGETHER_API_KEY"


@pytest.fixture
def has_credentials() -> bool:
    return all(os.environ.get(k) for k in ["NEO4J_URI", "ANTHROPIC_API_KEY", "TOGETHER_API_KEY"])


@pytest.mark.skipif(
    not all(os.environ.get(k) for k in ["NEO4J_URI", "ANTHROPIC_API_KEY", "TOGETHER_API_KEY"]),
    reason=SKIP_REASON,
)
class TestMemoryLifecycle:
    """Full add → search → verify lifecycle."""

    @pytest.fixture(autouse=True)
    async def setup_backend(self):
        from agent_memory.backend import MemoryBackend
        from agent_memory.config import Settings

        self.settings = Settings()
        self.backend = MemoryBackend(self.settings)
        await self.backend.initialize()
        yield
        await self.backend.close()

    async def test_add_and_search(self, sample_episode_body, sample_episode_name):
        result = await self.backend.add_episode(
            name=sample_episode_name,
            body=sample_episode_body,
            source="text",
            group_id="test-e2e",
        )
        assert result["status"] == "ok"

        facts = await self.backend.search_facts(
            query="NVLink firmware",
            group_ids=["test-e2e"],
        )
        assert len(facts) > 0
        assert any("firmware" in f["fact"].lower() for f in facts)

    async def test_status(self):
        status = await self.backend.get_status()
        assert status["status"] == "ok"
        assert status["backend"] == "neo4j"
