"""DeepEval metric backend for MCP eval **output-quality** scoring.

The custom Inspect scorers in :mod:`mcp_common.testing.eval.scorers`
(``tool_use_scorer``, ``cli_tool_use_scorer``, ``parity_scorer``) validate
**structural** correctness — did the agent pick the right tool / run the right
CLI command, and is the final answer task-complete? This module adds a
**semantic** quality layer on top of those, wrapping
`DeepEval <https://docs.confident-ai.com/>`_'s battle-tested metrics:

* **faithfulness** — does the agent's response faithfully represent the
  underlying tool output (no unsupported claims)?
* **hallucination** — did the agent fabricate information not present in the
  tool results?
* **answer relevancy** — is the response relevant to the user's request?

The actual Inspect ``@scorer`` functions (``faithfulness_scorer``,
``hallucination_scorer``, ``relevancy_scorer``) live in ``scorers.py`` and call
into the pure, framework-light helpers here. Keeping the DeepEval glue in its
own module means ``scorers.py`` — and therefore the whole ``mcp_common.testing.eval``
package — imports cleanly **without** DeepEval installed: every DeepEval import
is deferred until a DeepEval scorer is actually run. Install the backend with::

    uv pip install "mcp-common[eval-scoring]"

DeepEval needs an LLM backend for its judges. To stay consistent with the
existing LLM-as-judge infrastructure, the scorers pass in the same
OpenAI-compatible client that :func:`mcp_common.testing.eval.scorers._get_llm_client`
builds (Together by default, honoring the ``EVAL_JUDGE_*`` overrides), which
:func:`build_judge_model` wraps in a :class:`~deepeval.models.DeepEvalBaseLLM`
adapter.

Coded against the installed ``deepeval==3.9.7`` API (``eval-scoring`` extra);
the metric / test-case / custom-model contract used here is stable across the
pending 4.x bump (togethercomputer/mcp-common#164).
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from functools import lru_cache
from importlib.util import find_spec
from typing import Any

_INSTALL_HINT = (
    'DeepEval scorer backend requires the "eval-scoring" extra. '
    'Install with: uv pip install "mcp-common[eval-scoring]"'
)

# DeepEval phones home (telemetry) and renders a progress UI by default. Opt
# out so running a scorer never makes an unexpected network call or pollutes
# eval output. ``setdefault`` so an operator who *wants* telemetry can still
# enable it via the environment.
_TELEMETRY_OPT_OUT_ENV: dict[str, str] = {
    "DEEPEVAL_TELEMETRY_OPT_OUT": "YES",
    "ERROR_REPORTING": "NO",
    "DEEPEVAL_DISABLE_PROGRESS_BAR": "YES",
}

# DeepEval-native default thresholds (lifted from the 3.x metric defaults) so
# callers get sensible pass/fail behaviour without having to know each metric's
# direction. Faithfulness / relevancy are "higher is better" (success when
# score >= threshold); hallucination is "lower is better" (success when score
# <= threshold). The directionality is handled by DeepEval itself — we surface
# ``metric.success`` verbatim — so callers only ever tune the magnitude.
_DEFAULT_THRESHOLD = 0.5


class DeepEvalUnavailableError(ImportError):
    """Raised when a DeepEval scorer runs but the ``eval-scoring`` extra is absent."""


@dataclass(frozen=True)
class DeepEvalResult:
    """Outcome of a single DeepEval metric measurement.

    Attributes:
        metric: Short metric label (``"faithfulness"`` / ``"hallucination"`` /
            ``"relevancy"``) used for Inspect ``Score`` metadata keys.
        score: Raw metric score in ``[0.0, 1.0]``. For faithfulness/relevancy
            higher is better; for hallucination **lower** is better.
        success: DeepEval's own pass/fail verdict for the metric (already
            accounts for the metric's direction relative to ``threshold``).
        reason: Natural-language justification from the judge (empty when
            ``include_reason`` produced nothing).
        threshold: The threshold the metric was evaluated against.
    """

    metric: str
    score: float
    success: bool
    reason: str
    threshold: float


def deepeval_available() -> bool:
    """Whether the optional ``deepeval`` dependency is importable.

    Uses :func:`importlib.util.find_spec` so it never imports DeepEval (heavy,
    and we want the package to stay import-clean without the extra).
    """
    return find_spec("deepeval") is not None


def _prepare_deepeval() -> None:
    """Guard the optional import and opt out of DeepEval telemetry.

    Raises :class:`DeepEvalUnavailableError` with an install hint when the
    ``eval-scoring`` extra is not installed. Sets the telemetry/progress opt-out
    environment variables (via ``setdefault``) **before** any ``import deepeval``
    runs so DeepEval reads them at import time.
    """
    if not deepeval_available():
        raise DeepEvalUnavailableError(_INSTALL_HINT)
    for key, value in _TELEMETRY_OPT_OUT_ENV.items():
        os.environ.setdefault(key, value)


@lru_cache(maxsize=1)
def _judge_model_cls() -> type:
    """Build (once) the ``DeepEvalBaseLLM`` adapter class used by the judge.

    The subclass is created lazily — so importing this module never requires
    DeepEval (see module docstring) — and **cached**: ``DeepEvalBaseLLM`` runs
    DeepEval's metric-observation instrumentation in ``__init_subclass__`` on
    *every* subclass creation, so re-defining the adapter on each
    :func:`build_judge_model` call (per sample, per metric) would repeat that
    work for no reason. Defined once here; :func:`build_judge_model` just
    instantiates it per call.
    """
    from deepeval.models.base_model import DeepEvalBaseLLM

    class _TogetherDeepEvalModel(DeepEvalBaseLLM):  # type: ignore[no-untyped-call]  # base is untyped (optional dep)
        """Adapter exposing an OpenAI-compatible client through DeepEval's LLM API."""

        def __init__(self, client: Any, model_name: str) -> None:
            self._client = client
            self._model_name = model_name
            # DeepEvalBaseLLM.__init__ calls self.load_model(); _client must
            # already be set, hence the ordering above.
            super().__init__(model_name)

        def load_model(self, *args: Any, **kwargs: Any) -> Any:
            return self._client

        def generate(self, prompt: str, *args: Any, **kwargs: Any) -> str:
            # Route through the shared LLM-as-judge call so DeepEval's many
            # sequential judge requests get the same retry/backoff (429
            # Retry-After aware) and provider-gated JSON-mode (response_format)
            # as the other scorers. DeepEval fans out one judge call per claim /
            # verdict / reason, so this is the most throttling-exposed judge
            # path — it must not bypass the shared retry logic. Imported lazily
            # to avoid a module-load import cycle with scorers.py.
            from mcp_common.testing.eval.scorers import _call_llm_judge

            return _call_llm_judge(self._client, self._model_name, prompt)

        async def a_generate(self, prompt: str, *args: Any, **kwargs: Any) -> str:
            return await asyncio.to_thread(self.generate, prompt)

        def get_model_name(self, *args: Any, **kwargs: Any) -> str:
            return self._model_name

    return _TogetherDeepEvalModel


def build_judge_model(client: Any, model_name: str) -> Any:
    """Wrap an OpenAI-compatible ``client`` as a DeepEval custom judge model.

    DeepEval metrics need an LLM backend. Rather than let DeepEval spin up its
    own (OpenAI-keyed) client, this adapts the *same* client the existing
    LLM-as-judge uses — built by
    :func:`mcp_common.testing.eval.scorers._get_llm_client` and pointed at
    Together (or whatever ``EVAL_JUDGE_BASE_URL`` selects) — so DeepEval judging
    rides on the configured judge endpoint/credentials and shares its
    retry/backoff + JSON-mode behaviour.

    The adapter class is built once and cached (see :func:`_judge_model_cls`);
    this call only instantiates it.

    Args:
        client: An OpenAI-compatible client exposing
            ``chat.completions.create(...)``.
        model_name: The judge model id to send on each request.

    Returns:
        A ``DeepEvalBaseLLM`` instance suitable for the ``model=`` argument of
        any DeepEval metric.
    """
    _prepare_deepeval()
    return _judge_model_cls()(client, model_name)


def _measure(metric: Any, test_case: Any, label: str) -> DeepEvalResult:
    """Run ``metric.measure(test_case)`` and snapshot the result.

    Reads ``metric.score`` / ``metric.success`` / ``metric.reason`` /
    ``metric.threshold`` — the attributes every DeepEval ``BaseMetric`` exposes
    after a successful ``measure`` — into an immutable :class:`DeepEvalResult`.

    ``_log_metric_to_confident=False`` keeps each measurement local: it
    suppresses DeepEval's per-measure POST to the Confident AI platform (which
    the telemetry opt-out env vars do not cover), so running a scorer never
    makes an unexpected network call.
    """
    metric.measure(test_case, _log_metric_to_confident=False)
    return DeepEvalResult(
        metric=label,
        score=float(metric.score),
        success=bool(metric.success),
        reason=str(getattr(metric, "reason", "") or ""),
        threshold=float(getattr(metric, "threshold", 0.0)),
    )


def score_faithfulness(
    client: Any,
    model_name: str,
    *,
    input: str,
    actual_output: str,
    retrieval_context: list[str],
    threshold: float = _DEFAULT_THRESHOLD,
) -> DeepEvalResult:
    """Measure how faithfully ``actual_output`` represents ``retrieval_context``.

    ``retrieval_context`` is the underlying data the response should be grounded
    in — for MCP evals, the tool outputs the agent saw. Higher score is better.
    """
    _prepare_deepeval()
    from deepeval.metrics import FaithfulnessMetric
    from deepeval.test_case import LLMTestCase

    model = build_judge_model(client, model_name)
    test_case = LLMTestCase(
        input=input,
        actual_output=actual_output,
        retrieval_context=list(retrieval_context),
    )
    metric = FaithfulnessMetric(
        threshold=threshold, model=model, include_reason=True, async_mode=False
    )
    return _measure(metric, test_case, "faithfulness")


def score_hallucination(
    client: Any,
    model_name: str,
    *,
    input: str,
    actual_output: str,
    context: list[str],
    threshold: float = _DEFAULT_THRESHOLD,
) -> DeepEvalResult:
    """Measure whether ``actual_output`` fabricates info absent from ``context``.

    ``context`` is the ground-truth the output is checked against (the agent's
    tool outputs). DeepEval's hallucination score is "lower is better", and
    ``success`` already encodes that direction relative to ``threshold``.
    """
    _prepare_deepeval()
    from deepeval.metrics import HallucinationMetric
    from deepeval.test_case import LLMTestCase

    model = build_judge_model(client, model_name)
    test_case = LLMTestCase(
        input=input,
        actual_output=actual_output,
        context=list(context),
    )
    metric = HallucinationMetric(
        threshold=threshold, model=model, include_reason=True, async_mode=False
    )
    return _measure(metric, test_case, "hallucination")


def score_answer_relevancy(
    client: Any,
    model_name: str,
    *,
    input: str,
    actual_output: str,
    threshold: float = _DEFAULT_THRESHOLD,
) -> DeepEvalResult:
    """Measure whether ``actual_output`` is relevant to the user's ``input``.

    Needs no retrieval context — just the request and the response. Higher score
    is better.
    """
    _prepare_deepeval()
    from deepeval.metrics import AnswerRelevancyMetric
    from deepeval.test_case import LLMTestCase

    model = build_judge_model(client, model_name)
    test_case = LLMTestCase(input=input, actual_output=actual_output)
    metric = AnswerRelevancyMetric(
        threshold=threshold, model=model, include_reason=True, async_mode=False
    )
    return _measure(metric, test_case, "relevancy")
