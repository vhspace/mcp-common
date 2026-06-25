"""Tests for the shared eval matrix runner (matrix_runner.py, #88)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from mcp_common.testing.eval.matrix_runner import (
    JUDGE_DECOUPLED_DEFAULT_CONNECTIONS,
    MatrixPreflight,
    MatrixPreflightError,
    MatrixRunConfig,
    classify_model,
    judge_api_string,
    provider_of,
    resolve_max_connections,
    resolve_modes,
    routes_to_together,
    run_matrix,
    select_models,
    summarize_log,
    together_api_model,
)


@dataclass(frozen=True)
class _StubModel:
    name: str
    tier: str
    open_weights: bool = True
    requires_env: str | None = None
    enabled: bool = True
    note: str = ""
    model_args: dict | None = None
    catalog_slug: str | None = None


MODEL_A = _StubModel("together/Qwen/Qwen3.5-9B", "fast")
MODEL_B = _StubModel("anthropic/claude-opus-4-8", "high", False, requires_env="ANTHROPIC_API_KEY")
MODEL_DISABLED = _StubModel("cursor/composer-2.5", "high", False, enabled=False, note="deferred")
REGISTRY = [MODEL_A, MODEL_B, MODEL_DISABLED]


@pytest.mark.eval
class TestModelStringHelpers:
    def test_provider_of(self) -> None:
        assert provider_of("together/Qwen/Qwen3.5-9B") == "together"
        assert provider_of("anthropic/claude-opus-4-8") == "anthropic"

    def test_together_api_model(self) -> None:
        assert together_api_model("together/Qwen/Qwen3.5-9B") == "Qwen/Qwen3.5-9B"
        assert together_api_model("anthropic/claude-opus-4-8") is None

    def test_routes_to_together(self) -> None:
        assert routes_to_together("together/Qwen/Qwen3.5-9B")
        assert routes_to_together("openai-api/together/Qwen/Qwen3.7-Max")
        assert not routes_to_together("anthropic/claude-opus-4-8")

    def test_judge_api_string_strips_together_prefix(self) -> None:
        assert judge_api_string("together/Qwen/Qwen3-235B-A22B-Instruct-2507-tput") == (
            "Qwen/Qwen3-235B-A22B-Instruct-2507-tput"
        )
        assert judge_api_string("claude-sonnet-4-6") == "claude-sonnet-4-6"


@pytest.mark.eval
class TestResolveModes:
    def test_all_expands(self) -> None:
        assert resolve_modes("all", ("mcp", "cli")) == ["mcp", "cli"]

    def test_single_mode(self) -> None:
        assert resolve_modes("mcp", ("mcp", "cli")) == ["mcp"]


@pytest.mark.eval
class TestResolveMaxConnections:
    def test_explicit_wins(self) -> None:
        n, reason = resolve_max_connections(8, judge_api_key="key")
        assert n == 8
        assert reason == "explicit --max-connections"

    def test_decoupled_judge_bumps_default(self) -> None:
        n, reason = resolve_max_connections(None, judge_api_key="judge-key")
        assert n == JUDGE_DECOUPLED_DEFAULT_CONNECTIONS
        assert "EVAL_JUDGE_API_KEY" in reason

    def test_shared_judge_stays_serial(self) -> None:
        n, reason = resolve_max_connections(None, judge_api_key=None)
        assert n == 1
        assert "shared judge key" in reason


@pytest.mark.eval
class TestSelectModels:
    def test_tier_filter(self) -> None:
        fast = select_models(REGISTRY, "fast", [])
        assert [m.name for m in fast] == [MODEL_A.name]

    def test_name_substring_filter(self) -> None:
        matched = select_models(REGISTRY, "all", ["claude"])
        assert [m.name for m in matched] == [MODEL_B.name]


@pytest.mark.eval
class TestClassifyModel:
    def test_disabled_model(self) -> None:
        status, reason = classify_model(MODEL_DISABLED, catalog=None)
        assert status == "disabled"
        assert reason == "deferred"

    def test_missing_env_skips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        status, reason = classify_model(MODEL_B, catalog=None)
        assert status == "skip"
        assert reason == "ANTHROPIC_API_KEY not set"

    def test_together_without_key_skips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TOGETHER_API_KEY", raising=False)
        status, _ = classify_model(MODEL_A, catalog=None)
        assert status == "skip"

    def test_catalog_warning_proceeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TOGETHER_API_KEY", "test-key")
        status, reason = classify_model(MODEL_A, catalog={"other-model"})
        assert status == "run"
        assert reason.startswith("WARNING")


@pytest.mark.eval
class TestSummarizeLog:
    def test_success_log_shape(self) -> None:
        metric = MagicMock()
        metric.value = 0.75
        score = MagicMock()
        score.metrics = {"accuracy": metric}
        results = MagicMock()
        results.scores = [score]
        log = MagicMock(status="success", results=results, samples=None, location="/tmp/log.eval")
        summary = summarize_log(log)
        assert summary["status"] == "success"
        assert summary["accuracy"] == 0.75
        assert summary["log"] == "/tmp/log.eval"


def _minimal_config(
    tmp_path: Path,
    *,
    dry_run: bool = True,
    preflights: list[MatrixPreflight] | None = None,
    history_path: Path | None = None,
    trend_dir: Path | None = None,
) -> MatrixRunConfig:
    return MatrixRunConfig(
        title="test matrix",
        tier="all",
        modes=["mcp"],
        mode_tasks={"mcp": ("mcp_eval", "netbox_mcp_eval")},
        models=[MODEL_A],
        judge_model="together/Qwen/Qwen3-235B-A22B-Instruct-2507-tput",
        judge_api="Qwen/Qwen3-235B-A22B-Instruct-2507-tput",
        judge_decoupled=False,
        judge_endpoint="TOGETHER_API_KEY (shared)",
        max_connections=1,
        max_conn_reason="test",
        limit=None,
        catalog_state="skipped (--no-verify)",
        log_dir=tmp_path / "logs",
        timestamp="20260101-000000",
        dry_run=dry_run,
        preflights=preflights or [],
        history_path=history_path,
        trend_dir=trend_dir,
    )


@pytest.mark.eval
class TestRunMatrixDryRun:
    def test_dry_run_prints_plan_and_exits_zero(self, tmp_path: Path) -> None:
        code = run_matrix(_minimal_config(tmp_path))
        assert code == 0

    def test_preflight_abort_writes_summary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TOGETHER_API_KEY", "test-key")

        def fail() -> dict[str, Any]:
            raise MatrixPreflightError("boom")

        preflights = [
            MatrixPreflight(
                summary_key="preflight",
                skip_flag=False,
                skip_arg="preflight",
                plan_label="preflight",
                plan_value="run check",
                execute_intro="PREFLIGHT: checking ...",
                run=fail,
                abort_message="ABORT: boom",
            ),
        ]
        config = _minimal_config(tmp_path, dry_run=False, preflights=preflights)
        code = run_matrix(config)
        assert code == 2
        summary_path = config.log_dir / "summary.json"
        assert summary_path.exists()
        payload = summary_path.read_text(encoding="utf-8")
        assert "preflight" in payload
        assert '"status": "error"' in payload

    @patch("inspect_ai.eval")
    def test_run_matrix_includes_judge_cost_in_summary(
        self, mock_eval: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TOGETHER_API_KEY", "test-key")
        log = MagicMock(status="success", results=None, samples=None, location=None)
        mock_eval.return_value = [log]

        with patch(
            "mcp_common.testing.eval.matrix_runner.load_task",
            return_value=MagicMock(),
        ):
            code = run_matrix(_minimal_config(tmp_path, dry_run=False))

        assert code == 0
        summary_path = tmp_path / "logs" / "summary.json"
        assert summary_path.exists()
        payload = summary_path.read_text(encoding="utf-8")
        assert "judge_cost" in payload


@pytest.mark.eval
class TestRunMatrixHistory:
    """Trend history wiring (#88 Phase 3b)."""

    @patch("inspect_ai.eval")
    def test_history_appended_and_trend_rendered_on_success(
        self, mock_eval: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import json as _json

        monkeypatch.setenv("TOGETHER_API_KEY", "test-key")
        log = MagicMock(status="success", results=None, samples=None, location=None)
        mock_eval.return_value = [log]
        history_path = tmp_path / "history.jsonl"
        trend_dir = tmp_path / "trend"

        with patch(
            "mcp_common.testing.eval.matrix_runner.load_task",
            return_value=MagicMock(),
        ):
            code = run_matrix(
                _minimal_config(
                    tmp_path,
                    dry_run=False,
                    history_path=history_path,
                    trend_dir=trend_dir,
                )
            )

        assert code == 0
        assert history_path.exists()
        records = [
            _json.loads(line)
            for line in history_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(records) == 1
        assert records[0]["timestamp"] == "20260101-000000"
        # Trend artifacts rendered (Markdown + sections.json; viz-mcp not
        # installed in CI so no html/png — that's the documented happy path).
        assert (trend_dir / "trend.md").exists()
        assert (trend_dir / "sections.json").exists()

    def test_history_appended_on_preflight_abort(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TOGETHER_API_KEY", "test-key")
        history_path = tmp_path / "history.jsonl"

        def fail() -> dict[str, Any]:
            raise MatrixPreflightError("boom")

        preflights = [
            MatrixPreflight(
                summary_key="preflight",
                skip_flag=False,
                skip_arg="preflight",
                plan_label="preflight",
                plan_value="run check",
                execute_intro="PREFLIGHT: checking ...",
                run=fail,
                abort_message="ABORT: boom",
            ),
        ]
        config = _minimal_config(
            tmp_path, dry_run=False, preflights=preflights, history_path=history_path
        )
        code = run_matrix(config)
        assert code == 2
        # Even an aborted run records history (so infra failures are visible
        # in the trend, not just successes).
        assert history_path.exists()
        assert len(history_path.read_text(encoding="utf-8").splitlines()) == 1

    @patch("inspect_ai.eval")
    def test_no_history_when_unset(
        self, mock_eval: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TOGETHER_API_KEY", "test-key")
        log = MagicMock(status="success", results=None, samples=None, location=None)
        mock_eval.return_value = [log]

        with patch(
            "mcp_common.testing.eval.matrix_runner.load_task",
            return_value=MagicMock(),
        ):
            code = run_matrix(_minimal_config(tmp_path, dry_run=False))

        assert code == 0
        # Default: no history.jsonl anywhere under tmp_path.
        assert not list(tmp_path.rglob("history.jsonl"))
