"""Tests for the optional W&B eval-tracking layer (vhspace/mcp-common#60).

W&B is an OPTIONAL extra (``eval-tracking``) not installed in the default
dev/CI sync, so these tests never import it for real: the metric/record
extraction helpers are pure (tested against duck-typed ``EvalLog`` stand-ins),
and :func:`log_eval_to_wandb` is exercised by injecting a fake ``wandb`` module
into ``sys.modules`` and asserting the orchestration (init -> log metrics +
table -> upload artifact -> finish).
"""

from __future__ import annotations

import importlib.machinery
import sys
import types
from typing import Any

import pytest
from inspect_ai.scorer import CORRECT, INCORRECT, NOANSWER, PARTIAL, Score

from mcp_common.testing.eval import tracking as trk
from mcp_common.testing.eval.tracking import (
    WandbUnavailableError,
    _artifact_name,
    _score_to_float,
    _server_from_task,
    _stringify_value,
    build_sample_records,
    derive_run_config,
    log_eval_to_wandb,
    summarize_eval_log,
)

# ---------------------------------------------------------------------------
# Duck-typed EvalLog stand-ins
# ---------------------------------------------------------------------------


def _metric(value: Any) -> types.SimpleNamespace:
    return types.SimpleNamespace(value=value)


def _escore(name: str, metrics: dict[str, Any]) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        name=name, scorer=name, metrics={k: _metric(v) for k, v in metrics.items()}
    )


def _results(
    scores: list[Any], total: int | None = None, completed: int | None = None
) -> types.SimpleNamespace:
    return types.SimpleNamespace(scores=scores, total_samples=total, completed_samples=completed)


def _spec(
    task: str | None = None,
    model: str | None = None,
    model_base_url: str | None = None,
    dataset_name: str | None = None,
) -> types.SimpleNamespace:
    dataset = types.SimpleNamespace(name=dataset_name) if dataset_name else None
    return types.SimpleNamespace(
        task=task, model=model, model_base_url=model_base_url, dataset=dataset
    )


def _sample(sid: Any, epoch: int, scores: dict[str, Any]) -> types.SimpleNamespace:
    return types.SimpleNamespace(id=sid, epoch=epoch, scores=scores)


def _log(
    eval: Any = None,
    results: Any = None,
    samples: list[Any] | None = None,
    status: str | None = None,
    location: str | None = None,
) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        eval=eval, results=results, samples=samples or [], status=status, location=location
    )


# ---------------------------------------------------------------------------
# Pure value helpers
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestScoreToFloat:
    def test_categorical(self) -> None:
        assert _score_to_float(CORRECT) == 1.0
        assert _score_to_float(PARTIAL) == 0.5
        assert _score_to_float(INCORRECT) == 0.0
        assert _score_to_float(NOANSWER) is None

    def test_numeric_passthrough(self) -> None:
        assert _score_to_float(0.73) == 0.73
        assert _score_to_float(1) == 1.0

    def test_bool(self) -> None:
        assert _score_to_float(True) == 1.0
        assert _score_to_float(False) == 0.0

    def test_unknown(self) -> None:
        assert _score_to_float("weird") is None
        assert _score_to_float({"a": 1}) is None


@pytest.mark.eval
class TestStringifyValue:
    def test_categorical(self) -> None:
        assert _stringify_value("C") == "C"

    def test_dict_is_json(self) -> None:
        assert _stringify_value({"b": 2, "a": 1}) == '{"a": 1, "b": 2}'


@pytest.mark.eval
class TestServerFromTask:
    def test_strips_eval_suffix_and_hyphenates(self) -> None:
        assert _server_from_task("netbox_mcp_eval") == "netbox-mcp"

    def test_no_eval_suffix(self) -> None:
        assert _server_from_task("redfish_mcp") == "redfish-mcp"


# ---------------------------------------------------------------------------
# summarize_eval_log
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestSummarizeEvalLog:
    def test_flattens_scorer_metrics(self) -> None:
        log = _log(
            results=_results(
                [
                    _escore("tool_use_scorer", {"accuracy": 0.85}),
                    _escore("parity_scorer", {"accuracy": 0.5, "mean": 0.7}),
                ],
                total=10,
                completed=9,
            )
        )
        out = summarize_eval_log(log)
        assert out["tool_use_scorer/accuracy"] == 0.85
        assert out["parity_scorer/accuracy"] == 0.5
        assert out["parity_scorer/mean"] == 0.7
        assert out["total_samples"] == 10.0
        assert out["completed_samples"] == 9.0

    def test_no_results_returns_empty(self) -> None:
        assert summarize_eval_log(_log(results=None)) == {}

    def test_skips_non_numeric_and_bool_metrics(self) -> None:
        log = _log(results=_results([_escore("s", {"accuracy": 0.9, "label": "C", "flag": True})]))
        out = summarize_eval_log(log)
        assert out == {"s/accuracy": 0.9}


# ---------------------------------------------------------------------------
# build_sample_records
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestBuildSampleRecords:
    def test_rows_per_sample_scorer(self) -> None:
        log = _log(
            samples=[
                _sample(1, 1, {"tool_use_scorer": Score(value=CORRECT, explanation="good")}),
                _sample(2, 1, {"tool_use_scorer": Score(value=INCORRECT, explanation="bad")}),
            ]
        )
        columns, rows = build_sample_records(log)
        assert columns == ["sample_id", "epoch", "scorer", "value", "score", "explanation"]
        assert rows == [
            ["1", 1, "tool_use_scorer", "C", 1.0, "good"],
            ["2", 1, "tool_use_scorer", "I", 0.0, "bad"],
        ]

    def test_no_samples(self) -> None:
        columns, rows = build_sample_records(_log(samples=[]))
        assert rows == []
        assert "sample_id" in columns

    def test_truncates_long_explanation(self) -> None:
        long_reason = "x" * 1000
        log = _log(samples=[_sample(1, 1, {"s": Score(value=CORRECT, explanation=long_reason)})])
        _columns, rows = build_sample_records(log)
        assert len(rows[0][5]) == 500

    def test_numeric_value_passthrough(self) -> None:
        log = _log(samples=[_sample(7, 2, {"s": Score(value=0.4, explanation="")})])
        _columns, rows = build_sample_records(log)
        assert rows[0][3] == "0.4"
        assert rows[0][4] == 0.4


# ---------------------------------------------------------------------------
# derive_run_config
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestDeriveRunConfig:
    def test_full_metadata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("EVAL_JUDGE_MODEL", raising=False)
        log = _log(
            eval=_spec(
                task="netbox_mcp_eval",
                model="anthropic/claude-3-5-haiku-latest",
                model_base_url="https://api.anthropic.com/v1",
                dataset_name="scenarios-v3",
            ),
            results=_results([_escore("tool_use_scorer", {"accuracy": 0.8})]),
            status="success",
        )
        config = derive_run_config(log, extra={"dataset_version": "abc123"})
        assert config["task"] == "netbox_mcp_eval"
        assert config["server"] == "netbox-mcp"
        assert config["model_under_test"] == "anthropic/claude-3-5-haiku-latest"
        assert config["model_base_url"] == "https://api.anthropic.com/v1"
        assert config["dataset"] == "scenarios-v3"
        assert config["scorers"] == ["tool_use_scorer"]
        assert config["status"] == "success"
        assert config["dataset_version"] == "abc123"

    def test_judge_model_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EVAL_JUDGE_MODEL", "Qwen/Judge")
        config = derive_run_config(_log(eval=_spec(task="t_eval")))
        assert config["judge_model"] == "Qwen/Judge"

    def test_extra_overrides_derived(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("EVAL_JUDGE_MODEL", raising=False)
        log = _log(eval=_spec(task="netbox_mcp_eval"))
        config = derive_run_config(log, extra={"server": "override-server"})
        assert config["server"] == "override-server"

    def test_minimal_log(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("EVAL_JUDGE_MODEL", raising=False)
        assert derive_run_config(_log()) == {}


# ---------------------------------------------------------------------------
# _artifact_name
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestArtifactName:
    def test_from_server(self) -> None:
        assert _artifact_name({"server": "netbox-mcp"}) == "netbox-mcp-eval-log"

    def test_falls_back_to_task(self) -> None:
        assert _artifact_name({"task": "foo_eval"}) == "foo_eval-eval-log"

    def test_sanitizes_unsafe_chars(self) -> None:
        assert _artifact_name({"server": "weird/name space"}) == "weird-name-space-eval-log"

    def test_empty_config(self) -> None:
        assert _artifact_name({}) == "eval-eval-log"


# ---------------------------------------------------------------------------
# Fake wandb for log_eval_to_wandb orchestration
# ---------------------------------------------------------------------------


class _FakeSummary:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    def update(self, d: dict[str, Any]) -> None:
        self.data.update(d)


class _FakeTable:
    def __init__(self, columns: list[str], data: list[list[Any]]) -> None:
        self.columns = columns
        self.data = data


class _FakeArtifact:
    def __init__(self, name: str, type: str) -> None:
        self.name = name
        self.type = type
        self.files: list[str] = []

    def add_file(self, path: str) -> None:
        self.files.append(path)


class _FakeRun:
    def __init__(self) -> None:
        self.init_kwargs: dict[str, Any] | None = None
        self.logged: list[dict[str, Any]] = []
        self.summary = _FakeSummary()
        self.artifacts: list[_FakeArtifact] = []
        self.url = "https://wandb.ai/acme/evals/runs/abc"
        self.finished = False

    def log(self, data: dict[str, Any]) -> None:
        self.logged.append(data)

    def log_artifact(self, artifact: _FakeArtifact) -> None:
        self.artifacts.append(artifact)

    def finish(self) -> None:
        self.finished = True


@pytest.fixture
def fake_wandb(monkeypatch: pytest.MonkeyPatch) -> tuple[types.ModuleType, _FakeRun]:
    run = _FakeRun()

    def fake_init(**kwargs: Any) -> _FakeRun:
        run.init_kwargs = kwargs
        return run

    wandb_mod = types.ModuleType("wandb")
    wandb_mod.__spec__ = importlib.machinery.ModuleSpec("wandb", loader=None)
    wandb_mod.init = fake_init  # type: ignore[attr-defined]
    wandb_mod.Table = _FakeTable  # type: ignore[attr-defined]
    wandb_mod.Artifact = _FakeArtifact  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "wandb", wandb_mod)
    monkeypatch.setattr(trk, "wandb_available", lambda: True)
    return wandb_mod, run


def _full_log() -> types.SimpleNamespace:
    return _log(
        eval=_spec(task="netbox_mcp_eval", model="anthropic/claude-3-5-haiku-latest"),
        results=_results([_escore("tool_use_scorer", {"accuracy": 0.8})], total=2, completed=2),
        samples=[_sample(1, 1, {"tool_use_scorer": Score(value=CORRECT, explanation="ok")})],
        status="success",
        location="/tmp/run.eval",
    )


@pytest.mark.eval
class TestLogEvalToWandb:
    def test_full_orchestration(self, fake_wandb: tuple[types.ModuleType, _FakeRun]) -> None:
        _mod, run = fake_wandb
        url = log_eval_to_wandb(
            _full_log(),
            project="evals",
            entity="acme",
            tags=["nightly"],
            extra_config={"dataset_version": "v1"},
            eval_log_path="/tmp/run.eval",
        )

        assert url == "https://wandb.ai/acme/evals/runs/abc"
        assert run.finished is True

        assert run.init_kwargs is not None
        assert run.init_kwargs["project"] == "evals"
        assert run.init_kwargs["entity"] == "acme"
        assert run.init_kwargs["tags"] == ["nightly"]
        assert run.init_kwargs["name"] == "netbox_mcp_eval"
        assert run.init_kwargs["config"]["server"] == "netbox-mcp"
        assert run.init_kwargs["config"]["dataset_version"] == "v1"

        # aggregate metrics logged to history + summary
        metric_logs = [d for d in run.logged if "tool_use_scorer/accuracy" in d]
        assert metric_logs and metric_logs[0]["tool_use_scorer/accuracy"] == 0.8
        assert run.summary.data["tool_use_scorer/accuracy"] == 0.8

        # per-sample table logged
        table_logs = [d for d in run.logged if "samples" in d]
        assert table_logs and isinstance(table_logs[0]["samples"], _FakeTable)
        assert table_logs[0]["samples"].data == [["1", 1, "tool_use_scorer", "C", 1.0, "ok"]]

        # .eval artifact uploaded
        assert len(run.artifacts) == 1
        assert run.artifacts[0].name == "netbox-mcp-eval-log"
        assert run.artifacts[0].type == "eval-log"
        assert run.artifacts[0].files == ["/tmp/run.eval"]

    def test_upload_artifact_disabled(self, fake_wandb: tuple[types.ModuleType, _FakeRun]) -> None:
        _mod, run = fake_wandb
        log_eval_to_wandb(_full_log(), project="evals", upload_artifact=False)
        assert run.artifacts == []
        assert run.finished is True

    def test_no_path_skips_artifact(self, fake_wandb: tuple[types.ModuleType, _FakeRun]) -> None:
        _mod, run = fake_wandb
        log = _full_log()
        log.location = None
        log_eval_to_wandb(log, project="evals")  # no eval_log_path, no location
        assert run.artifacts == []

    def test_defaults_artifact_path_to_log_location(
        self, fake_wandb: tuple[types.ModuleType, _FakeRun]
    ) -> None:
        _mod, run = fake_wandb
        log_eval_to_wandb(_full_log(), project="evals")  # no explicit eval_log_path
        assert run.artifacts[0].files == ["/tmp/run.eval"]

    def test_finishes_run_even_on_error(
        self, fake_wandb: tuple[types.ModuleType, _FakeRun]
    ) -> None:
        _mod, run = fake_wandb

        def boom(_data: dict[str, Any]) -> None:
            raise RuntimeError("log failed")

        run.log = boom  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="log failed"):
            log_eval_to_wandb(_full_log(), project="evals")
        assert run.finished is True


# ---------------------------------------------------------------------------
# Guard + file convenience
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestWandbGuard:
    def test_raises_when_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(trk, "wandb_available", lambda: False)
        with pytest.raises(WandbUnavailableError, match="eval-tracking"):
            log_eval_to_wandb(_full_log(), project="evals")


@pytest.mark.eval
class TestLogEvalFile:
    def test_reads_and_forwards(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sentinel_log = object()
        monkeypatch.setattr("inspect_ai.log.read_eval_log", lambda p: sentinel_log)

        captured: dict[str, Any] = {}

        def fake_log_to_wandb(eval_log: Any, **kwargs: Any) -> str:
            captured["eval_log"] = eval_log
            captured["kwargs"] = kwargs
            return "run-url"

        monkeypatch.setattr(trk, "log_eval_to_wandb", fake_log_to_wandb)

        out = trk.log_eval_file("/tmp/run.eval", project="evals", entity="acme")
        assert out == "run-url"
        assert captured["eval_log"] is sentinel_log
        assert captured["kwargs"]["eval_log_path"] == "/tmp/run.eval"
        assert captured["kwargs"]["project"] == "evals"
        assert captured["kwargs"]["entity"] == "acme"
