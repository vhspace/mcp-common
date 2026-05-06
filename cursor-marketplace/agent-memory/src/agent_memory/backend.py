"""Shared backend service wrapping Graphiti Core with decay and promotion."""

import asyncio
import logging
import math
from datetime import UTC, datetime

from graphiti_core import Graphiti
from graphiti_core.nodes import EpisodeType

from .config import Settings

logger = logging.getLogger(__name__)


class MemoryBackend:
    """Shared backend for MCP server and CLI.

    Wraps Graphiti Core to provide episode ingestion, hybrid search,
    and retention scoring. Both the MCP server and the CLI call into
    this class rather than touching Graphiti directly.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._graphiti: Graphiti | None = None

    def _build_llm_client(self, provider: str | None = None, model: str | None = None):
        """Build an LLM client for the given provider/model (defaults to primary)."""
        from graphiti_core.llm_client.config import LLMConfig

        provider = (provider or self.settings.llm_provider).lower()
        model = model or self.settings.model_name

        if provider == "anthropic":
            from graphiti_core.llm_client.anthropic_client import AnthropicClient

            return AnthropicClient(
                LLMConfig(api_key=self.settings.anthropic_api_key, model=model)
            )

        base_url = "https://api.together.ai/v1" if provider == "together" else None
        api_key = (
            self.settings.together_api_key if provider == "together"
            else self.settings.openai_api_key
        )
        from graphiti_core.llm_client import OpenAIClient

        return OpenAIClient(
            LLMConfig(api_key=api_key, model=model, base_url=base_url)
        )

    def _build_embedder(self):
        """Build the embedding client based on configured provider."""
        provider = self.settings.embedding_provider.lower()

        if provider == "voyage":
            from graphiti_core.embedder.voyage import VoyageAIEmbedder, VoyageAIEmbedderConfig

            api_key = self.settings.voyage_api_key or self.settings.embedding_api_key
            return VoyageAIEmbedder(
                VoyageAIEmbedderConfig(
                    api_key=api_key,
                    embedding_model=self.settings.embedding_model,
                )
            )

        if provider == "together":
            from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig

            api_key = self.settings.embedding_api_key or self.settings.together_api_key
            return OpenAIEmbedder(
                OpenAIEmbedderConfig(
                    api_key=api_key,
                    embedding_model=self.settings.embedding_model,
                    base_url="https://api.together.ai/v1",
                )
            )

        # Default: OpenAI
        from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig

        api_key = self.settings.embedding_api_key or self.settings.openai_api_key
        return OpenAIEmbedder(
            OpenAIEmbedderConfig(
                api_key=api_key,
                embedding_model=self.settings.embedding_model,
            )
        )

    @staticmethod
    def _is_transient_error(exc: Exception) -> bool:
        """Return True if *exc* looks like a transient HTTP/connection error."""
        text = str(exc)
        if any(code in text for code in ("429", "500", "502", "503", "504")):
            return True
        text_lower = text.lower()
        return any(kw in text_lower for kw in (
            "connection", "timeout", "timed out", "reset by peer",
            "broken pipe", "temporary failure", "service unavailable",
            "rate limit", "overloaded",
        ))

    def _build_cross_encoder(self, llm_client):
        """Build a cross-encoder reranker using an OpenAI-compatible API."""
        from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
        from graphiti_core.llm_client.config import LLMConfig

        api_key = self.settings.together_api_key or self.settings.openai_api_key
        config = LLMConfig(
            api_key=api_key,
            model=self.settings.model_name,
            base_url="https://api.together.ai/v1" if self.settings.together_api_key else None,
        )
        return OpenAIRerankerClient(config=config)

    async def initialize(self) -> None:
        """Initialize Graphiti connection and build indices."""
        llm_client = self._build_llm_client()
        embedder = self._build_embedder()
        cross_encoder = self._build_cross_encoder(llm_client)

        self._graphiti = Graphiti(
            uri=self.settings.neo4j_uri,
            user=self.settings.neo4j_user,
            password=self.settings.neo4j_password,
            llm_client=llm_client,
            embedder=embedder,
            cross_encoder=cross_encoder,
        )
        await self._graphiti.build_indices_and_constraints()
        logger.info(
            "Memory backend initialized (Neo4j %s, LLM: %s/%s)",
            self.settings.neo4j_uri,
            self.settings.llm_provider,
            self.settings.model_name,
        )

    @property
    def graphiti(self) -> Graphiti:
        if self._graphiti is None:
            raise RuntimeError("Backend not initialized. Call initialize() first.")
        return self._graphiti

    async def close(self) -> None:
        if self._graphiti:
            await self._graphiti.close()

    # ------------------------------------------------------------------
    # Episode ingestion
    # ------------------------------------------------------------------

    @staticmethod
    def _episode_result(name: str, gid: str, result) -> dict:
        return {
            "status": "ok",
            "name": name,
            "group_id": gid,
            "episode_id": str(result.uuid) if hasattr(result, "uuid") else name,
        }

    async def add_episode(
        self,
        name: str,
        body: str,
        source: str = "text",
        source_description: str = "",
        group_id: str | None = None,
        reference_time: datetime | None = None,
    ) -> dict:
        """Add an episode to the knowledge graph.

        Retries transient LLM errors with exponential backoff, then falls
        back to the configured fallback provider before giving up.
        """
        episode_type = getattr(EpisodeType, source, EpisodeType.text)
        ref_time = reference_time or datetime.now(UTC)
        gid = group_id or self.settings.group_id

        call_kwargs = dict(
            name=name,
            episode_body=body,
            source=episode_type,
            source_description=source_description,
            reference_time=ref_time,
            group_id=gid,
        )

        max_retries = self.settings.llm_max_retries
        base_delay = self.settings.llm_retry_base_delay
        last_exc: Exception | None = None

        for attempt in range(1, max_retries + 1):
            try:
                result = await self.graphiti.add_episode(**call_kwargs)
                return self._episode_result(name, gid, result)
            except Exception as exc:
                last_exc = exc
                if not self._is_transient_error(exc):
                    raise
                delay = base_delay * (2 ** (attempt - 1))
                logger.warning(
                    "add_episode attempt %d/%d failed (%s: %s), retrying in %.1fs",
                    attempt, max_retries, type(exc).__name__, exc, delay,
                )
                await asyncio.sleep(delay)

        fb_provider = self.settings.fallback_llm_provider
        fb_model = self.settings.fallback_model_name
        logger.warning(
            "Primary LLM exhausted %d retries; falling back to %s/%s",
            max_retries, fb_provider, fb_model,
        )

        fallback_graphiti = Graphiti(
            uri=self.settings.neo4j_uri,
            user=self.settings.neo4j_user,
            password=self.settings.neo4j_password,
            llm_client=self._build_llm_client(fb_provider, fb_model),
            embedder=self.graphiti.embedder,
            cross_encoder=self.graphiti.cross_encoder,
        )
        try:
            result = await fallback_graphiti.add_episode(**call_kwargs)
            logger.info("Fallback to %s/%s succeeded for episode %r", fb_provider, fb_model, name)
            return self._episode_result(name, gid, result)
        except Exception as fallback_exc:
            logger.error("Fallback LLM (%s/%s) also failed: %s", fb_provider, fb_model, fallback_exc)
            raise RuntimeError(
                f"Primary LLM failed after {max_retries} retries and fallback also failed"
            ) from last_exc

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def _all_group_ids(self) -> list[str]:
        """Return every group_id present in the graph."""
        records, _, _ = await self.graphiti.driver.execute_query(
            "MATCH (e:Episodic) RETURN DISTINCT e.group_id AS gid"
        )
        return [r["gid"] for r in records if r["gid"]]

    async def get_groups(self) -> list[dict]:
        """Return all groups with episode count and last activity timestamp."""
        records, _, _ = await self.graphiti.driver.execute_query(
            "MATCH (e:Episodic) "
            "WHERE e.group_id IS NOT NULL "
            "RETURN e.group_id AS group_id, "
            "       count(e) AS episode_count, "
            "       max(e.created_at) AS last_active "
            "ORDER BY last_active DESC"
        )
        return [
            {
                "group_id": r["group_id"],
                "episode_count": r["episode_count"],
                "last_active": (r["last_active"].isoformat() if r["last_active"] else None),
            }
            for r in records
            if r["group_id"]
        ]

    async def search_facts(
        self,
        query: str,
        group_ids: list[str] | None = None,
        max_facts: int = 10,
        center_node_uuid: str | None = None,
    ) -> list[dict]:
        """Search for facts (edges) in the knowledge graph.

        When no group_ids are specified, searches across ALL groups.
        """
        gids = group_ids or await self._all_group_ids()
        try:
            results = await self.graphiti.search(
                query=query,
                group_ids=gids,
                num_results=max_facts,
                center_node_uuid=center_node_uuid,
            )
        except Exception as e:
            logger.error("Search failed: %s", e)
            raise RuntimeError(f"Search failed: {e}") from e
        return [
            {
                "fact": r.fact,
                "uuid": str(r.uuid),
                "source_node": getattr(r, "source_node_name", ""),
                "target_node": getattr(r, "target_node_name", ""),
                "valid_at": (r.valid_at.isoformat() if r.valid_at else None),
                "invalid_at": (r.invalid_at.isoformat() if r.invalid_at else None),
                "created_at": (
                    r.created_at.isoformat() if hasattr(r, "created_at") and r.created_at else None
                ),
            }
            for r in results
        ]

    async def search_nodes(
        self,
        query: str,
        group_ids: list[str] | None = None,
        max_nodes: int = 10,
    ) -> list[dict]:
        """Search for entity nodes in the knowledge graph."""
        gids = group_ids or await self._all_group_ids()
        results = await self.graphiti.search(
            query=query,
            group_ids=gids,
            num_results=max_nodes,
        )
        return [
            {
                "name": getattr(r, "name", getattr(r, "fact", str(r))),
                "uuid": str(r.uuid) if hasattr(r, "uuid") else "",
                "summary": getattr(r, "summary", getattr(r, "fact", "")),
                "entity_type": getattr(r, "entity_type", ""),
            }
            for r in results
        ]

    # ------------------------------------------------------------------
    # Episodes
    # ------------------------------------------------------------------

    async def get_episodes(
        self,
        group_id: str | None = None,
        last_n: int = 10,
    ) -> list[dict]:
        """Get recent episodes."""
        gids = [group_id or self.settings.group_id]
        results = await self.graphiti.retrieve_episodes(
            reference_time=datetime.now(UTC),
            last_n=last_n,
            group_ids=gids,
        )
        return [
            {
                "name": ep.name,
                "uuid": str(ep.uuid),
                "source": getattr(ep, "source", ""),
                "created_at": (
                    ep.created_at.isoformat()
                    if hasattr(ep, "created_at") and ep.created_at
                    else None
                ),
            }
            for ep in results
        ]

    # ------------------------------------------------------------------
    # Retention scoring (hybrid Ebbinghaus + usage-based decay)
    # ------------------------------------------------------------------

    @staticmethod
    def retention_score(
        created_at: datetime,
        last_accessed: datetime,
        access_count: int,
        now: datetime | None = None,
        base_strength: float = 30.0,
        temporal_weight: float = 0.4,
        usage_weight: float = 0.6,
    ) -> float:
        """Hybrid decay score combining Ebbinghaus curve with usage signals.

        Returns a value in [0, 1] where 1 means fully retained and
        values below the configured prune threshold are candidates for
        garbage collection.
        """
        now = now or datetime.now(UTC)
        age_secs = (now - created_at).total_seconds()
        days_since_creation = max(age_secs / 86400, 0.01)
        access_secs = (now - last_accessed).total_seconds()
        days_since_access = max(access_secs / 86400, 0.01)

        # Reinforced temporal decay — each access increases memory strength
        reinforced_s = base_strength * (1 + 0.1 * math.log(1 + access_count))
        temporal_score = math.exp(-days_since_creation / reinforced_s)

        # Usage score — recency weighted by frequency
        recency = 1.0 / (1.0 + days_since_access / 7.0)
        frequency = min(1.0, math.log(1 + access_count) / math.log(1 + 100))
        usage_score = frequency * recency

        return temporal_weight * temporal_score + usage_weight * usage_score

    # ------------------------------------------------------------------
    # Forget (immediate removal)
    # ------------------------------------------------------------------

    async def search_entity_facts(
        self,
        entity_name: str,
        group_ids: list[str] | None = None,
    ) -> list[dict]:
        """Find all facts (edges) connected to an entity by name."""
        gids = group_ids or await self._all_group_ids()
        records, _, _ = await self.graphiti.driver.execute_query(
            "MATCH (a)-[r]->(b) "
            "WHERE r.group_id IN $gids "
            "AND r.fact IS NOT NULL "
            "AND ("
            "  (a.name IS NOT NULL AND toLower(a.name) CONTAINS toLower($name))"
            "  OR (b.name IS NOT NULL AND toLower(b.name) CONTAINS toLower($name))"
            ") "
            "RETURN r.uuid AS uuid, r.fact AS fact, "
            "       a.name AS source_node, b.name AS target_node, "
            "       r.created_at AS created_at",
            name=entity_name,
            gids=gids,
        )
        return [
            {
                "uuid": str(r["uuid"]),
                "fact": r["fact"] or "",
                "source_node": r["source_node"] or "",
                "target_node": r["target_node"] or "",
                "created_at": (r["created_at"].isoformat() if r["created_at"] else None),
            }
            for r in records
        ]

    async def delete_facts_by_uuid(self, uuids: list[str]) -> int:
        """Delete fact edges by UUID. Returns the count of deleted edges."""
        if not uuids:
            return 0
        records, _, _ = await self.graphiti.driver.execute_query(
            "MATCH ()-[r]->() WHERE r.uuid IN $uuids DELETE r RETURN count(r) AS deleted",
            uuids=uuids,
        )
        return records[0]["deleted"] if records else 0

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def get_status(self) -> dict:
        """Get system status including connectivity check."""
        try:
            episodes = await self.graphiti.retrieve_episodes(
                reference_time=datetime.now(UTC),
                last_n=1,
                group_ids=[self.settings.group_id],
            )
            return {
                "status": "ok",
                "backend": "neo4j",
                "uri": self.settings.neo4j_uri,
                "group_id": self.settings.group_id,
                "llm_provider": self.settings.llm_provider,
                "model": self.settings.model_name,
                "recent_episodes": len(episodes),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}


# Module-level singleton
_backend: MemoryBackend | None = None


async def get_backend() -> MemoryBackend:
    """Return (and lazily initialize) the module-level backend singleton."""
    global _backend
    if _backend is None:
        settings = Settings()
        _backend = MemoryBackend(settings)
        await _backend.initialize()
    return _backend
