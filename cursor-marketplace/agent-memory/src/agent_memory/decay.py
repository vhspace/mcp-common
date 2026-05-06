"""Memory decay scoring and rule promotion for Cursor.

Implements two key lifecycle operations:

1. **Promotion** — exports high-confidence procedural memories as
   .cursor/rules/*.mdc files so the agent uses them automatically.
2. **Decay** — evaluates retention scores for episodes and flags
   low-value memories for eventual pruning.
"""

import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from .backend import MemoryBackend
from .config import Settings  # noqa: F401 (used by callers for type reference)

logger = logging.getLogger(__name__)


async def promote_memories_to_rules(
    backend: MemoryBackend,
    output_dir: str | None = None,
) -> dict:
    """Export high-confidence procedural memories as Cursor rule files.

    Searches for Procedure/Preference entities that have been accessed
    frequently and promotes them to ``.cursor/rules/*.mdc`` files so
    the agent picks them up automatically on future sessions.
    """
    settings = backend.settings
    out = output_dir or settings.rules_output_dir
    os.makedirs(out, exist_ok=True)

    procedures = await backend.search_facts(
        query="procedures preferences best practices lessons learned",
        group_ids=[settings.group_id],
        max_facts=50,
    )

    if not procedures:
        logger.info("No procedural memories found to promote")
        return {"status": "ok", "promoted": 0}

    valid = [p for p in procedures if p.get("invalid_at") is None]

    if not valid:
        logger.info("No valid (non-superseded) procedures to promote")
        return {"status": "ok", "promoted": 0}

    rule_path = Path(out) / "memory-promoted-procedures.mdc"
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "---",
        f"description: Auto-promoted from agent memory. Updated: {timestamp}",
        "alwaysApply: true",
        "---",
        "",
        "# Learned Procedures (Auto-Generated from Memory)",
        "",
        "These rules were automatically promoted from the agent memory system.",
        "They represent patterns learned from past incidents and operations.",
        "",
    ]

    for i, proc in enumerate(valid, 1):
        src = proc.get("source_node", "Unknown")
        tgt = proc.get("target_node", "Unknown")
        lines.append(f"## {i}. {src} → {tgt}")
        lines.append(f"- {proc['fact']}")
        if proc.get("valid_at"):
            lines.append(f"- Learned: {proc['valid_at']}")
        lines.append("")

    rule_path.write_text("\n".join(lines))
    logger.info("Promoted %d procedures to %s", len(valid), rule_path)

    return {"status": "ok", "promoted": len(valid), "path": str(rule_path)}


async def run_decay_cycle(backend: MemoryBackend) -> dict:
    """Run a decay evaluation cycle.

    Graphiti handles factual supersession automatically (marking
    contradicted facts with ``invalid_at``). This function layers
    usage-based relevance decay on top by computing a retention score
    for each episode. Currently reports scores and flags candidates;
    actual pruning is deferred until thresholds are tuned.
    """
    settings = backend.settings
    episodes = await backend.get_episodes(
        group_id=settings.group_id,
        last_n=100,
    )

    now = datetime.now(UTC)
    decay_report: list[dict] = []

    for ep in episodes:
        created = datetime.fromisoformat(ep["created_at"]) if ep.get("created_at") else now
        score = MemoryBackend.retention_score(
            created_at=created,
            last_accessed=created,  # TODO: track actual access times
            access_count=1,  # TODO: track access counts
            now=now,
            base_strength=settings.decay_base_strength_days,
            temporal_weight=settings.decay_temporal_weight,
            usage_weight=settings.decay_usage_weight,
        )
        decay_report.append(
            {
                "name": ep["name"],
                "uuid": ep["uuid"],
                "retention_score": round(score, 4),
                "would_prune": score < settings.decay_prune_threshold,
            }
        )

    prunable = [d for d in decay_report if d["would_prune"]]
    logger.info(
        "Decay cycle: %d episodes evaluated, %d below prune threshold",
        len(decay_report),
        len(prunable),
    )

    return {
        "status": "ok",
        "evaluated": len(decay_report),
        "below_threshold": len(prunable),
        "threshold": settings.decay_prune_threshold,
        "details": decay_report[:20],
    }
