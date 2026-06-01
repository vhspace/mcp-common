"""Account for LLM-as-judge (Anthropic / Together) token usage in eval runs (#169).

``summary.json`` (written by the eval runner, e.g. netbox-mcp's
``run_matrix.py``) records token/cost only for the **model under test** — it
reflects Inspect's ``model_usage``. The LLM judge runs through a **separate**
OpenAI-compatible client in :mod:`mcp_common.testing.eval.scorers`
(``_get_llm_client`` / ``_call_llm_judge``, used by ``tool_use_scorer`` /
``combined_scorer`` / ``parity_scorer``) and its token usage was never counted,
so ``summary.json`` undercounts true end-to-end cost. That matters now the judge
is a **paid Anthropic Sonnet** model (decoupled in #132 / #155), not a free/local
one, and it feeds the cost/trend charts in #125.

This module is the **report/summary side** of judge accounting. It does not
change how the judge is called (``scorers.py`` is owned elsewhere). Instead it
provides:

- :func:`tracked_judge_client` — a transparent proxy over the judge's
  OpenAI-compatible client that records ``response.usage`` from every
  ``chat.completions.create`` into a :class:`JudgeUsageAccumulator`, returning
  the response untouched. This is the **seam**: wrap the judge client once and
  every judge call is counted.
- :func:`install_judge_usage_tracking` — an opt-in, guarded runtime hook that
  wraps ``scorers._get_llm_client`` so a runner gets judge accounting with **no
  change to ``scorers.py``**. (The clean long-term integration is a one-liner in
  ``_get_llm_client``: ``return tracked_judge_client(client), model`` — see the
  PR notes.)
- :func:`judge_cost_block` — builds the per-run judge token/cost block to drop
  into ``summary.json`` as a **separate line item** from the model-under-test
  cost, so true end-to-end cost = model-under-test + judge.

Usage in a runner::

    from mcp_common.testing.eval import install_judge_usage_tracking, reset_judge_usage

    reset_judge_usage()
    install_judge_usage_tracking()      # wrap the judge client (idempotent)
    # ... run the matrix (judge calls are now counted) ...
    add_judge_usage_to_summary(summary) # inject summary.json["judge_cost"]
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

_log = logging.getLogger(__name__)

__all__ = [
    "JudgeModelUsage",
    "JudgePricing",
    "JudgeUsage",
    "JudgeUsageAccumulator",
    "estimate_judge_cost",
    "get_judge_usage",
    "install_judge_usage_tracking",
    "judge_cost_block",
    "record_judge_usage",
    "reset_judge_usage",
    "tracked_judge_client",
    "uninstall_judge_usage_tracking",
]

# Env vars carrying the judge's per-million-token price (USD). Pricing is volatile
# and provider-specific, so it is configured (not hard-coded): when unset, token
# counts are still recorded but ``cost_usd`` is reported as ``None``.
PRICE_INPUT_ENV_VAR = "EVAL_JUDGE_PRICE_INPUT_PER_MTOK"
PRICE_OUTPUT_ENV_VAR = "EVAL_JUDGE_PRICE_OUTPUT_PER_MTOK"

_TOKENS_PER_MILLION = 1_000_000.0


@dataclass(frozen=True)
class JudgeModelUsage:
    """Token usage attributed to a single judge model."""

    calls: int
    input_tokens: int
    output_tokens: int
    total_tokens: int

    def to_dict(self) -> dict[str, int]:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass(frozen=True)
class JudgeUsage:
    """Aggregated LLM-judge token usage for a run (a point-in-time snapshot)."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    by_model: Mapping[str, JudgeModelUsage] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "by_model": {name: usage.to_dict() for name, usage in self.by_model.items()},
        }


@dataclass(frozen=True)
class JudgePricing:
    """Per-million-token judge pricing (USD), used to estimate judge cost."""

    input_usd_per_mtok: float
    output_usd_per_mtok: float

    def to_dict(self) -> dict[str, float]:
        return {
            "input_usd_per_mtok": self.input_usd_per_mtok,
            "output_usd_per_mtok": self.output_usd_per_mtok,
        }

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> JudgePricing | None:
        """Build pricing from ``EVAL_JUDGE_PRICE_*`` env vars, or ``None`` if unset.

        Returns ``None`` when neither price env var is set (token counts are
        still recorded; ``cost_usd`` is reported as ``None``). A missing one of
        the pair defaults to ``0.0`` so a partial configuration still yields a
        (clearly partial) estimate rather than silently failing.
        """
        resolved = os.environ if env is None else env
        raw_in = resolved.get(PRICE_INPUT_ENV_VAR)
        raw_out = resolved.get(PRICE_OUTPUT_ENV_VAR)
        if raw_in is None and raw_out is None:
            return None
        try:
            return cls(
                input_usd_per_mtok=float(raw_in) if raw_in is not None else 0.0,
                output_usd_per_mtok=float(raw_out) if raw_out is not None else 0.0,
            )
        except ValueError:
            _log.warning(
                "Ignoring unparseable judge pricing env vars (%s=%r, %s=%r)",
                PRICE_INPUT_ENV_VAR,
                raw_in,
                PRICE_OUTPUT_ENV_VAR,
                raw_out,
            )
            return None


def estimate_judge_cost(usage: JudgeUsage, pricing: JudgePricing) -> float:
    """Estimate judge cost (USD) from ``usage`` and ``pricing`` (per-million tokens)."""
    return (
        usage.input_tokens / _TOKENS_PER_MILLION * pricing.input_usd_per_mtok
        + usage.output_tokens / _TOKENS_PER_MILLION * pricing.output_usd_per_mtok
    )


class JudgeUsageAccumulator:
    """Thread-safe accumulator of LLM-judge token usage across a run.

    Judge calls run in worker threads (``asyncio.to_thread`` in the scorers), so
    every mutation is guarded by a lock. Record usage with :meth:`record` (or
    :meth:`record_response` from a raw OpenAI response), read a consistent
    :class:`JudgeUsage` with :meth:`snapshot`, and clear between runs with
    :meth:`reset`.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls = 0
        self._input = 0
        self._output = 0
        self._total = 0
        # model -> [calls, input, output, total]
        self._by_model: dict[str, list[int]] = {}

    def record(
        self,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int | None = None,
    ) -> None:
        """Record one judge call's token usage (``total`` defaults to in+out)."""
        total = total_tokens if total_tokens is not None else input_tokens + output_tokens
        with self._lock:
            self._calls += 1
            self._input += input_tokens
            self._output += output_tokens
            self._total += total
            entry = self._by_model.setdefault(model, [0, 0, 0, 0])
            entry[0] += 1
            entry[1] += input_tokens
            entry[2] += output_tokens
            entry[3] += total

    def record_response(self, response: Any, *, request_model: str | None = None) -> bool:
        """Record usage from a raw OpenAI-style chat-completion ``response``.

        Reads ``response.usage`` (``prompt_tokens`` / ``completion_tokens`` /
        ``total_tokens``) and attributes it to ``response.model`` (falling back
        to ``request_model``). Returns ``True`` when usage was recorded; ``False``
        (and never raises) when the response carries no usage — so wrapping a
        client can never break a judge call.
        """
        usage = getattr(response, "usage", None)
        if usage is None:
            return False
        input_tokens = _coerce_int(getattr(usage, "prompt_tokens", None))
        output_tokens = _coerce_int(getattr(usage, "completion_tokens", None))
        raw_total = getattr(usage, "total_tokens", None)
        total_tokens = _coerce_int(raw_total) if raw_total is not None else None
        model = getattr(response, "model", None) or request_model or "unknown"
        self.record(
            model=str(model),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )
        return True

    def snapshot(self) -> JudgeUsage:
        """Return a consistent :class:`JudgeUsage` snapshot of the accumulated totals."""
        with self._lock:
            by_model = {
                name: JudgeModelUsage(
                    calls=vals[0],
                    input_tokens=vals[1],
                    output_tokens=vals[2],
                    total_tokens=vals[3],
                )
                for name, vals in self._by_model.items()
            }
            return JudgeUsage(
                calls=self._calls,
                input_tokens=self._input,
                output_tokens=self._output,
                total_tokens=self._total,
                by_model=by_model,
            )

    def reset(self) -> None:
        """Zero all accumulated totals (call once at the start of a run)."""
        with self._lock:
            self._calls = 0
            self._input = 0
            self._output = 0
            self._total = 0
            self._by_model.clear()


def _coerce_int(value: Any) -> int:
    """Best-effort coerce a token count to a non-negative int (``0`` on failure)."""
    if value is None:
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Process-global default accumulator
# ---------------------------------------------------------------------------
#
# A single judge-usage accumulator is shared per process so the scorers' judge
# client (created deep inside Inspect, with no handle back to the runner) and
# the runner's summary writer can meet on common ground without threading an
# accumulator through every call.

_DEFAULT_ACCUMULATOR = JudgeUsageAccumulator()


def get_judge_usage() -> JudgeUsage:
    """Snapshot the process-global judge-usage accumulator."""
    return _DEFAULT_ACCUMULATOR.snapshot()


def reset_judge_usage() -> None:
    """Reset the process-global judge-usage accumulator (call at run start)."""
    _DEFAULT_ACCUMULATOR.reset()


def record_judge_usage(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int | None = None,
) -> None:
    """Record a judge call against the process-global accumulator."""
    _DEFAULT_ACCUMULATOR.record(
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


# ---------------------------------------------------------------------------
# Transparent client proxy (the seam)
# ---------------------------------------------------------------------------


class _TrackedCompletions:
    """Wraps ``client.chat.completions`` to record usage from ``create``."""

    def __init__(self, inner: Any, accumulator: JudgeUsageAccumulator) -> None:
        self._inner = inner
        self._acc = accumulator

    def create(self, *args: Any, **kwargs: Any) -> Any:
        response = self._inner.create(*args, **kwargs)
        try:
            self._acc.record_response(response, request_model=kwargs.get("model"))
        except Exception:  # pragma: no cover - accounting must never break a call
            _log.debug("judge usage accounting failed for a response", exc_info=True)
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _TrackedChat:
    """Wraps ``client.chat`` so ``.completions`` is the tracking proxy."""

    def __init__(self, inner: Any, accumulator: JudgeUsageAccumulator) -> None:
        self._inner = inner
        self._acc = accumulator

    @property
    def completions(self) -> _TrackedCompletions:
        return _TrackedCompletions(self._inner.completions, self._acc)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class TrackedJudgeClient:
    """Transparent proxy over an OpenAI-compatible client that counts judge tokens.

    Delegates every attribute to the wrapped client, except ``chat`` (and thus
    ``chat.completions.create``), whose responses are recorded into the supplied
    accumulator. A drop-in replacement for the judge client: the judge still
    reads ``resp.choices[0].message.content`` exactly as before.
    """

    def __init__(self, inner: Any, accumulator: JudgeUsageAccumulator) -> None:
        self._inner = inner
        self._acc = accumulator

    @property
    def chat(self) -> _TrackedChat:
        return _TrackedChat(self._inner.chat, self._acc)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def tracked_judge_client(
    client: Any, accumulator: JudgeUsageAccumulator | None = None
) -> TrackedJudgeClient:
    """Wrap an OpenAI-compatible judge ``client`` so its token usage is counted.

    Records every ``chat.completions.create`` response's usage into
    ``accumulator`` (the process-global one when omitted) and returns the
    response untouched. Wrap the judge client once — in ``scorers._get_llm_client``
    or via :func:`install_judge_usage_tracking` — and the whole run is counted.
    """
    return TrackedJudgeClient(client, accumulator or _DEFAULT_ACCUMULATOR)


# ---------------------------------------------------------------------------
# Opt-in runtime hook into the scorers' judge client (no scorers.py edit)
# ---------------------------------------------------------------------------

_installed_original: Callable[[], Any] | None = None


def install_judge_usage_tracking(accumulator: JudgeUsageAccumulator | None = None) -> bool:
    """Wrap ``scorers._get_llm_client`` so judge calls are counted (idempotent).

    A guarded, reversible runtime hook that lets a runner enable judge token
    accounting **without editing ``scorers.py``**: it replaces the private
    ``_get_llm_client`` factory with one that returns a
    :func:`tracked_judge_client` around the real client. Safe by construction —
    it no-ops (returning ``False``) if the seam is missing or has changed, never
    raising — so a refactor on the scorers side degrades to "judge cost not
    counted" rather than a broken run.

    The clean long-term integration is a one-liner inside ``_get_llm_client``
    itself (``return tracked_judge_client(client), model``); this hook exists so
    accounting works today across the lane boundary. Returns whether tracking is
    installed.
    """
    global _installed_original
    if _installed_original is not None:
        return True
    try:
        from mcp_common.testing.eval import scorers
    except Exception:  # pragma: no cover - eval extra always present here
        return False
    original = getattr(scorers, "_get_llm_client", None)
    if not callable(original):
        return False
    acc = accumulator if accumulator is not None else _DEFAULT_ACCUMULATOR
    _installed_original = original

    def wrapped() -> Any:
        result = original()
        if result is None:
            return None
        client, model = result
        return tracked_judge_client(client, acc), model

    scorers._get_llm_client = wrapped
    return True


def uninstall_judge_usage_tracking() -> bool:
    """Restore the original ``scorers._get_llm_client`` (undo the runtime hook).

    Returns ``True`` if a hook was removed, ``False`` if none was installed.
    """
    global _installed_original
    if _installed_original is None:
        return False
    try:
        from mcp_common.testing.eval import scorers

        scorers._get_llm_client = _installed_original
    except Exception:  # pragma: no cover
        pass
    _installed_original = None
    return True


def judge_cost_block(
    usage: JudgeUsage | None = None,
    pricing: JudgePricing | None = None,
) -> dict[str, Any]:
    """Build the judge token/cost block for ``summary.json`` (a separate line item).

    Kept distinct from the model-under-test cost so true end-to-end cost =
    model-under-test + judge. Token counts (overall and per judge model) are
    always present; ``cost_usd`` is populated only when ``pricing`` is supplied
    or resolvable from ``EVAL_JUDGE_PRICE_*`` env vars, otherwise ``None``.

    Args:
        usage: Judge usage to report (defaults to the process-global snapshot).
        pricing: Judge pricing (defaults to :meth:`JudgePricing.from_env`).

    Returns:
        A JSON-serializable dict with ``calls`` / ``input_tokens`` /
        ``output_tokens`` / ``total_tokens`` / ``by_model``, plus ``cost_usd``
        and the ``pricing`` used (``None`` when unpriced).
    """
    resolved_usage = usage if usage is not None else get_judge_usage()
    resolved_pricing = pricing if pricing is not None else JudgePricing.from_env()

    block = resolved_usage.to_dict()
    if resolved_pricing is not None:
        block["cost_usd"] = round(estimate_judge_cost(resolved_usage, resolved_pricing), 6)
        block["pricing"] = resolved_pricing.to_dict()
    else:
        block["cost_usd"] = None
        block["pricing"] = None
    return block
