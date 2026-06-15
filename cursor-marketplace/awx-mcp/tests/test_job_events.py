"""Unit tests for the pure job_events transformations (issue #54 / #44)."""

from __future__ import annotations

from typing import Any

from awx_mcp.job_events import (
    build_results,
    event_status,
    extract_diffs,
    job_events_query,
    render_events_text,
    summarize_events,
)


def _ev(
    event: str,
    *,
    host: str = "",
    task: str = "",
    module: str = "",
    play: str = "",
    failed: bool = False,
    changed: bool = False,
    res: dict[str, Any] | None = None,
    stdout: str | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {"task": task, "task_action": module, "play": play}
    if res is not None:
        data["res"] = res
    ev: dict[str, Any] = {
        "event": event,
        "host_name": host,
        "failed": failed,
        "changed": changed,
        "event_data": data,
    }
    if stdout is not None:
        ev["stdout"] = stdout
    return ev


# Representative multi-host, multi-status event stream.
SAMPLE_EVENTS: list[dict[str, Any]] = [
    _ev("playbook_on_play_start", play="Deploy"),
    _ev("playbook_on_task_start", task="Gathering Facts"),
    _ev("runner_on_ok", host="web01", task="Gathering Facts", module="setup"),
    _ev("runner_on_ok", host="web02", task="Gathering Facts", module="setup"),
    _ev("playbook_on_task_start", task="Install packages"),
    _ev("runner_on_ok", host="web01", task="Install packages", module="yum", changed=True),
    _ev("runner_on_ok", host="web02", task="Install packages", module="yum", changed=True),
    _ev("playbook_on_task_start", task="Configure service"),
    _ev(
        "runner_on_failed",
        host="web02",
        task="Configure service",
        module="template",
        failed=True,
        res={"msg": "Service config failed"},
    ),
    _ev("runner_on_unreachable", host="db01", task="Configure service"),
    _ev("runner_on_skipped", host="web01", task="Configure service"),
]


class TestEventStatus:
    def test_ok(self) -> None:
        assert event_status(_ev("runner_on_ok", host="h")) == "ok"

    def test_changed(self) -> None:
        assert event_status(_ev("runner_on_ok", host="h", changed=True)) == "changed"

    def test_failed(self) -> None:
        assert event_status(_ev("runner_on_failed", host="h", failed=True)) == "failed"

    def test_unreachable(self) -> None:
        assert event_status(_ev("runner_on_unreachable", host="h")) == "unreachable"

    def test_skipped(self) -> None:
        assert event_status(_ev("runner_on_skipped", host="h")) == "skipped"

    def test_item_failed(self) -> None:
        assert event_status(_ev("runner_item_on_failed", host="h", failed=True)) == "failed"

    def test_non_runner_event_is_none(self) -> None:
        assert event_status(_ev("playbook_on_task_start", task="t")) is None
        assert event_status(_ev("playbook_on_play_start", play="p")) is None
        assert event_status({"event": "verbose"}) is None


class TestBuildResults:
    def test_no_filter_returns_all_host_outcomes(self) -> None:
        out = build_results(42, SAMPLE_EVENTS)
        assert out["job_id"] == 42
        # 7 runner_on_* host outcomes (framing events excluded).
        assert out["count"] == 7
        statuses = sorted(r["status"] for r in out["results"])
        assert statuses == ["changed", "changed", "failed", "ok", "ok", "skipped", "unreachable"]

    def test_status_filter_failed(self) -> None:
        out = build_results(42, SAMPLE_EVENTS, status="failed")
        assert out["count"] == 1
        assert out["results"][0]["host"] == "web02"
        assert out["results"][0]["task"] == "Configure service"
        assert out["results"][0]["message"] == "Service config failed"

    def test_status_filter_changed(self) -> None:
        out = build_results(42, SAMPLE_EVENTS, status="changed")
        assert out["count"] == 2
        assert {r["host"] for r in out["results"]} == {"web01", "web02"}
        assert all(r["status"] == "changed" for r in out["results"])

    def test_status_filter_unreachable(self) -> None:
        out = build_results(42, SAMPLE_EVENTS, status="unreachable")
        assert out["count"] == 1
        assert out["results"][0]["host"] == "db01"

    def test_host_pattern_filter(self) -> None:
        out = build_results(42, SAMPLE_EVENTS, host="web*")
        assert {r["host"] for r in out["results"]} == {"web01", "web02"}
        assert "db01" not in {r["host"] for r in out["results"]}

    def test_task_pattern_filter(self) -> None:
        out = build_results(42, SAMPLE_EVENTS, task="install*")
        assert out["count"] == 2
        assert all(r["task"] == "Install packages" for r in out["results"])

    def test_combined_host_and_status(self) -> None:
        out = build_results(42, SAMPLE_EVENTS, host="web*", status="failed")
        assert out["count"] == 1
        assert out["results"][0]["host"] == "web02"

    def test_host_summary_counts(self) -> None:
        out = build_results(42, SAMPLE_EVENTS)
        assert out["host_summary"]["web01"]["ok"] == 1
        assert out["host_summary"]["web01"]["changed"] == 1
        assert out["host_summary"]["web01"]["skipped"] == 1
        assert out["host_summary"]["web02"]["failed"] == 1
        assert out["host_summary"]["db01"]["unreachable"] == 1

    def test_max_results_truncates(self) -> None:
        out = build_results(42, SAMPLE_EVENTS, max_results=3)
        assert len(out["results"]) == 3
        assert out["truncated"] is True
        # host_summary still counts every matching outcome.
        assert sum(sum(c.values()) for c in out["host_summary"].values()) == 7

    def test_filters_echoed(self) -> None:
        out = build_results(42, SAMPLE_EVENTS, task="t", host="h", status="ok", include_diff=True)
        assert out["filters"] == {"task": "t", "host": "h", "status": "ok", "diff": True}


class TestDiffs:
    DIFF_EVENT = _ev(
        "runner_on_ok",
        host="web01",
        task="Write config",
        module="copy",
        changed=True,
        res={
            "changed": True,
            "diff": {
                "before": "old\n",
                "after": "new\n",
                "before_header": "/etc/app.conf",
                "after_header": "/etc/app.conf",
            },
        },
    )

    def test_extract_single_diff(self) -> None:
        diffs = extract_diffs(self.DIFF_EVENT)
        assert len(diffs) == 1
        assert diffs[0]["before"] == "old\n"
        assert diffs[0]["after"] == "new\n"

    def test_extract_list_of_diffs(self) -> None:
        ev = _ev(
            "runner_on_ok",
            host="h",
            changed=True,
            res={"diff": [{"before": "a"}, {"after": "b"}, {}]},
        )
        diffs = extract_diffs(ev)
        assert len(diffs) == 2  # empty dict dropped

    def test_no_diff(self) -> None:
        assert extract_diffs(_ev("runner_on_ok", host="h")) == []

    def test_include_diff_in_results(self) -> None:
        out = build_results(7, [self.DIFF_EVENT], include_diff=True)
        assert out["results"][0]["diff"][0]["after"] == "new\n"

    def test_diff_omitted_when_not_requested(self) -> None:
        out = build_results(7, [self.DIFF_EVENT], include_diff=False)
        assert "diff" not in out["results"][0]


class TestSummarizeEvents:
    def test_summary_shape_and_recap(self) -> None:
        summary = summarize_events(42, SAMPLE_EVENTS)
        assert summary["source"] == "job_events"
        assert summary["plays"] == ["Deploy"]
        assert summary["total_tasks"] == 3
        assert summary["has_failures"] is True
        assert summary["overall_result"] == "unreachable"
        hosts = {h["host"]: h for h in summary["host_stats"]}
        assert hosts["web01"]["ok"] == 2  # facts + (changed counts as ok too)
        assert hosts["web01"]["changed"] == 1
        assert hosts["web02"]["failed"] == 1
        assert hosts["db01"]["unreachable"] == 1
        assert "PLAY RECAP" in summary["recap_text"]

    def test_failed_tasks_captured(self) -> None:
        summary = summarize_events(42, SAMPLE_EVENTS)
        failed = summary["failed_tasks"]
        assert any(f["host"] == "web02" and "Configure" in f["task"] for f in failed)
        assert any(f["host"] == "db01" for f in failed)

    def test_all_successful(self) -> None:
        events = [
            _ev("playbook_on_play_start", play="P"),
            _ev("playbook_on_task_start", task="T"),
            _ev("runner_on_ok", host="h1", task="T"),
            _ev("runner_on_ok", host="h2", task="T", changed=True),
        ]
        summary = summarize_events(1, events)
        assert summary["overall_result"] == "successful"
        assert summary["has_failures"] is False


class TestRenderEventsText:
    def test_render_all(self) -> None:
        events = [
            _ev("runner_on_failed", host="web02", task="Configure", stdout="fatal: [web02]: msg"),
            _ev("runner_on_ok", host="web01", task="Install", stdout="ok: [web01]"),
        ]
        text = render_events_text(events)
        assert "fatal: [web02]" in text
        assert "ok: [web01]" in text

    def test_render_errors_only(self) -> None:
        events = [
            _ev("runner_on_failed", host="web02", task="Configure", failed=True, stdout="FATAL"),
            _ev("runner_on_ok", host="web01", task="Install", stdout="OKLINE"),
        ]
        text = render_events_text(events, filter_mode="errors")
        assert "FATAL" in text
        assert "OKLINE" not in text

    def test_render_host_filter(self) -> None:
        events = [
            _ev("runner_on_ok", host="db01", task="T", stdout="DBLINE"),
            _ev("runner_on_ok", host="web01", task="T", stdout="WEBLINE"),
        ]
        text = render_events_text(events, host="db*")
        assert "DBLINE" in text
        assert "WEBLINE" not in text

    def test_render_synthesizes_line_without_stdout(self) -> None:
        events = [
            _ev(
                "runner_on_failed",
                host="h1",
                task="T",
                failed=True,
                res={"msg": "boom"},
            )
        ]
        text = render_events_text(events)
        assert "failed: [h1] T => boom" in text


class TestJobEventsQuery:
    def test_default(self) -> None:
        q = job_events_query()
        assert q["page_size"] == 200
        assert q["order_by"] == "counter"
        assert "failed" not in q

    def test_failed_status(self) -> None:
        assert job_events_query("failed")["failed"] == "true"

    def test_changed_status(self) -> None:
        assert job_events_query("changed")["changed"] == "true"

    def test_unreachable_status(self) -> None:
        assert job_events_query("unreachable")["event"] == "runner_on_unreachable"

    def test_ok_status(self) -> None:
        assert job_events_query("ok")["event"] == "runner_on_ok"

    def test_literal_host_pushed_server_side(self) -> None:
        assert job_events_query(host="web01")["host_name"] == "web01"

    def test_wildcard_host_not_pushed(self) -> None:
        assert "host_name" not in job_events_query(host="web*")
