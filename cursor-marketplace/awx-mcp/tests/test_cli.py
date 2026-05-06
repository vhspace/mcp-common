"""Tests for awx-cli (typer CLI wrapper)."""

from __future__ import annotations

import json
import subprocess
import textwrap
from typing import ClassVar
from unittest.mock import MagicMock

import httpx
import pytest
from typer.testing import CliRunner

from awx_mcp.cli import _read_reference_hosts, app
from awx_mcp.log_parser import filter_stdout, parse_stdout_blocks

runner = CliRunner()

SAMPLE_ANSIBLE_OUTPUT = textwrap.dedent("""\
PLAY [Deploy application] ****************************************************

TASK [Gathering Facts] ********************************************************
ok: [web01]
ok: [web02]
ok: [db01]

TASK [Install packages] *******************************************************
changed: [web01]
changed: [web02]
ok: [db01]

TASK [Configure service] ******************************************************
ok: [web01]
fatal: [web02]: FAILED! => {"msg": "Service config failed"}
ok: [db01]

PLAY [Database setup] *********************************************************

TASK [Gathering Facts] ********************************************************
ok: [db01]

TASK [Run migrations] *********************************************************
changed: [db01]

TASK [Create backup] **********************************************************
ok: [db01]

PLAY RECAP ********************************************************************
db01                       : ok=5    changed=1    unreachable=0    failed=0    skipped=0    rescued=0    ignored=0
web01                      : ok=3    changed=1    unreachable=0    failed=0    skipped=0    rescued=0    ignored=0
web02                      : ok=2    changed=1    unreachable=0    failed=1    skipped=0    rescued=0    ignored=0
""")


def _mock_client(handler):
    """Create a patched _client that uses a mock transport."""
    from awx_mcp.awx_client import AwxRestClient

    transport = httpx.MockTransport(handler)
    return AwxRestClient(
        host="https://awx.example.com",
        token="test-token",
        http_transport=transport,
    )


# ---------------------------------------------------------------------------
# Issue #10 — hosts --inventory alias
# ---------------------------------------------------------------------------


class TestHostsInventoryAlias:
    """awx-cli hosts should accept both positional and --inventory / -i."""

    def _handler(self, request: httpx.Request) -> httpx.Response:
        if "/inventories/256/hosts" in str(request.url):
            return httpx.Response(
                200,
                json={"count": 1, "results": [{"id": 1, "name": "host1"}]},
            )
        return httpx.Response(404, text="not found")

    def test_positional_arg(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(self._handler))
        result = runner.invoke(app, ["hosts", "256", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["count"] == 1

    def test_inventory_option(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(self._handler))
        result = runner.invoke(app, ["hosts", "--inventory", "256", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["count"] == 1

    def test_inventory_short_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(self._handler))
        result = runner.invoke(app, ["hosts", "-i", "256", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["count"] == 1

    def test_missing_inventory_shows_helpful_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(self._handler))
        result = runner.invoke(app, ["hosts"])
        assert result.exit_code == 1
        assert "--inventory" in result.output or "INVENTORY_ID" in result.output


# ---------------------------------------------------------------------------
# Issue #33 — jobs --template filter uses correct endpoint
# ---------------------------------------------------------------------------


class TestJobsTemplateFilter:
    """awx-cli jobs should use the jobs endpoint when filtering by template."""

    JOBS_RESPONSE: ClassVar[dict] = {
        "count": 1,
        "results": [
            {
                "id": 500,
                "name": "deploy",
                "status": "successful",
                "created": "2025-01-01T00:00:00Z",
                "elapsed": 10.0,
            }
        ],
    }

    def test_no_filter_uses_unified_jobs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        hit_endpoint: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            hit_endpoint.append(str(request.url))
            return httpx.Response(200, json=self.JOBS_RESPONSE)

        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(handler))
        result = runner.invoke(app, ["jobs", "--json"])
        assert result.exit_code == 0
        assert any("unified_jobs" in u for u in hit_endpoint)

    def test_template_id_uses_jobs_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        hit_endpoint: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            hit_endpoint.append(str(request.url))
            return httpx.Response(200, json=self.JOBS_RESPONSE)

        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(handler))
        result = runner.invoke(app, ["jobs", "--template", "173", "--json"])
        assert result.exit_code == 0
        assert any("/jobs/" in u and "unified_jobs" not in u for u in hit_endpoint)
        assert any("job_template=173" in u for u in hit_endpoint)

    def test_template_name_uses_jobs_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        hit_endpoint: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            hit_endpoint.append(str(request.url))
            return httpx.Response(200, json=self.JOBS_RESPONSE)

        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(handler))
        result = runner.invoke(app, ["jobs", "--template-name", "k8s", "--json"])
        assert result.exit_code == 0
        assert any("/jobs/" in u and "unified_jobs" not in u for u in hit_endpoint)
        assert any("name__icontains=k8s" in u for u in hit_endpoint)

    def test_template_id_and_name_combined(self, monkeypatch: pytest.MonkeyPatch) -> None:
        hit_endpoint: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            hit_endpoint.append(str(request.url))
            return httpx.Response(200, json=self.JOBS_RESPONSE)

        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(handler))
        result = runner.invoke(
            app, ["jobs", "--template", "173", "--template-name", "k8s", "--json"]
        )
        assert result.exit_code == 0
        assert any("/jobs/" in u and "unified_jobs" not in u for u in hit_endpoint)
        assert any("job_template=173" in u for u in hit_endpoint)
        assert any("name__icontains=k8s" in u for u in hit_endpoint)

    def test_status_and_template_combined(self, monkeypatch: pytest.MonkeyPatch) -> None:
        hit_endpoint: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            hit_endpoint.append(str(request.url))
            return httpx.Response(200, json=self.JOBS_RESPONSE)

        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(handler))
        result = runner.invoke(app, ["jobs", "--status", "failed", "--template", "173", "--json"])
        assert result.exit_code == 0
        assert any("/jobs/" in u and "unified_jobs" not in u for u in hit_endpoint)
        assert any("status=failed" in u for u in hit_endpoint)
        assert any("job_template=173" in u for u in hit_endpoint)


# ---------------------------------------------------------------------------
# Issue #13 — launch --scm-branch
# ---------------------------------------------------------------------------


class TestLaunchScmBranch:
    """awx-cli launch should pass --scm-branch through to the AWX API."""

    def test_scm_branch_sent_in_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured_body: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and "/job_templates/174/launch" in str(request.url):
                captured_body.update(json.loads(request.content))
                return httpx.Response(201, json={"id": 9000, "status": "pending"})
            return httpx.Response(404, text="not found")

        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(handler))
        result = runner.invoke(app, ["launch", "174", "--scm-branch", "feat/test", "--json"])
        assert result.exit_code == 0
        assert captured_body.get("scm_branch") == "feat/test"

    def test_scm_branch_absent_when_not_given(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured_body: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and "/job_templates/174/launch" in str(request.url):
                captured_body.update(json.loads(request.content))
                return httpx.Response(201, json={"id": 9001, "status": "pending"})
            return httpx.Response(404, text="not found")

        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(handler))
        result = runner.invoke(app, ["launch", "174", "--json"])
        assert result.exit_code == 0
        assert "scm_branch" not in captured_body

    def test_scm_branch_with_workflow(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured_body: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and "/workflow_job_templates/200/launch" in str(
                request.url
            ):
                captured_body.update(json.loads(request.content))
                return httpx.Response(201, json={"id": 9002, "status": "pending"})
            return httpx.Response(404, text="not found")

        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(handler))
        result = runner.invoke(
            app,
            ["launch", "200", "--workflow", "--scm-branch", "main", "--json"],
        )
        assert result.exit_code == 0
        assert captured_body.get("scm_branch") == "main"


# ---------------------------------------------------------------------------
# Issue #11 — launch --wait enriched status
# ---------------------------------------------------------------------------


class TestLaunchWaitEnrichedStatus:
    """awx-cli launch --wait should show enriched polling output."""

    def test_pending_shows_reason(self, monkeypatch: pytest.MonkeyPatch) -> None:
        poll_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal poll_count
            if request.method == "POST" and "/job_templates/174/launch" in str(request.url):
                return httpx.Response(201, json={"id": 7000, "status": "pending"})
            if request.method == "GET" and "/jobs/7000" in str(request.url):
                poll_count += 1
                if poll_count >= 2:
                    return httpx.Response(
                        200,
                        json={
                            "id": 7000,
                            "status": "successful",
                            "elapsed": 5.2,
                            "summary_fields": {},
                        },
                    )
                return httpx.Response(
                    200,
                    json={
                        "id": 7000,
                        "status": "pending",
                        "job_explanation": "waiting for execution node",
                    },
                )
            return httpx.Response(404, text="not found")

        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(handler))
        result = runner.invoke(
            app,
            ["launch", "174", "--wait", "--poll-interval", "0.1", "--timeout", "5", "--json"],
        )
        assert result.exit_code == 0
        assert "waiting for execution node" in result.output

    def test_running_shows_elapsed_and_node(self, monkeypatch: pytest.MonkeyPatch) -> None:
        poll_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal poll_count
            if request.method == "POST" and "/job_templates/174/launch" in str(request.url):
                return httpx.Response(201, json={"id": 7001, "status": "pending"})
            if request.method == "GET" and "/jobs/7001" in str(request.url):
                poll_count += 1
                if poll_count >= 2:
                    return httpx.Response(
                        200,
                        json={
                            "id": 7001,
                            "status": "successful",
                            "elapsed": 12.3,
                            "summary_fields": {},
                        },
                    )
                return httpx.Response(
                    200,
                    json={
                        "id": 7001,
                        "status": "running",
                        "execution_node": "awx-worker-1",
                    },
                )
            return httpx.Response(404, text="not found")

        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(handler))
        result = runner.invoke(
            app,
            ["launch", "174", "--wait", "--poll-interval", "0.1", "--timeout", "5", "--json"],
        )
        assert result.exit_code == 0
        assert "node=awx-worker-1" in result.output

    def test_completion_summary_shown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and "/job_templates/174/launch" in str(request.url):
                return httpx.Response(201, json={"id": 7002, "status": "pending"})
            if request.method == "GET" and "/jobs/7002" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "id": 7002,
                        "status": "successful",
                        "elapsed": 42.0,
                        "summary_fields": {
                            "job_host_summaries": {"changed": 3, "failures": 0},
                        },
                    },
                )
            return httpx.Response(404, text="not found")

        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(handler))
        result = runner.invoke(
            app,
            ["launch", "174", "--wait", "--poll-interval", "0.1", "--timeout", "5", "--json"],
        )
        assert result.exit_code == 0
        assert "FINISHED" in result.output
        assert "42.0s" in result.output

    def test_failed_job_exits_nonzero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and "/job_templates/174/launch" in str(request.url):
                return httpx.Response(201, json={"id": 7003, "status": "pending"})
            if request.method == "GET" and "/jobs/7003" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "id": 7003,
                        "status": "failed",
                        "elapsed": 10.0,
                        "summary_fields": {},
                    },
                )
            return httpx.Response(404, text="not found")

        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(handler))
        result = runner.invoke(
            app,
            ["launch", "174", "--wait", "--poll-interval", "0.1", "--timeout", "5", "--json"],
        )
        assert result.exit_code == 1
        assert "FINISHED: failed" in result.output


# ---------------------------------------------------------------------------
# project-update command
# ---------------------------------------------------------------------------


class TestProjectUpdate:
    """awx-cli project-update should PATCH branch and/or trigger sync."""

    def test_set_branch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "PATCH" and "/projects/8/" in str(request.url):
                captured.update(json.loads(request.content))
                return httpx.Response(
                    200,
                    json={"id": 8, "name": "infra", "scm_branch": "feat/test"},
                )
            return httpx.Response(404, text="not found")

        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(handler))
        result = runner.invoke(app, ["project-update", "8", "--branch", "feat/test", "--json"])
        assert result.exit_code == 0
        assert captured.get("scm_branch") == "feat/test"

    def test_sync_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and "/projects/8/update/" in str(request.url):
                return httpx.Response(202, json={"id": 500, "status": "pending"})
            return httpx.Response(404, text="not found")

        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(handler))
        result = runner.invoke(app, ["project-update", "8", "--sync", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["id"] == 500

    def test_branch_and_sync_with_wait(self, monkeypatch: pytest.MonkeyPatch) -> None:
        poll_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal poll_count
            if request.method == "PATCH" and "/projects/8/" in str(request.url):
                return httpx.Response(
                    200,
                    json={"id": 8, "name": "infra", "scm_branch": "feat/test"},
                )
            if request.method == "POST" and "/projects/8/update/" in str(request.url):
                return httpx.Response(202, json={"id": 501, "status": "pending"})
            if request.method == "GET" and "/project_updates/501/" in str(request.url):
                poll_count += 1
                if poll_count >= 2:
                    return httpx.Response(
                        200, json={"id": 501, "status": "successful", "scm_branch": "feat/test"}
                    )
                return httpx.Response(200, json={"id": 501, "status": "running"})
            return httpx.Response(404, text="not found")

        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(handler))
        result = runner.invoke(
            app,
            [
                "project-update",
                "8",
                "--branch",
                "feat/test",
                "--sync",
                "--wait",
                "--poll-interval",
                "0.1",
                "--timeout",
                "5",
                "--json",
            ],
        )
        assert result.exit_code == 0
        assert "FINISHED: successful" in result.output

    def test_no_args_shows_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="not found")

        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(handler))
        result = runner.invoke(app, ["project-update", "8"])
        assert result.exit_code == 1
        assert "--branch" in result.output or "--sync" in result.output

    def test_failed_sync_exits_nonzero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and "/projects/8/update/" in str(request.url):
                return httpx.Response(202, json={"id": 502, "status": "pending"})
            if request.method == "GET" and "/project_updates/502/" in str(request.url):
                return httpx.Response(200, json={"id": 502, "status": "failed"})
            return httpx.Response(404, text="not found")

        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(handler))
        result = runner.invoke(
            app,
            [
                "project-update",
                "8",
                "--sync",
                "--wait",
                "--poll-interval",
                "0.1",
                "--timeout",
                "5",
                "--json",
            ],
        )
        assert result.exit_code == 1
        assert "FINISHED: failed" in result.output


# ---------------------------------------------------------------------------
# Issue #26 — stdout filtering (log_parser unit tests)
# ---------------------------------------------------------------------------


class TestParseStdoutBlocks:
    """parse_stdout_blocks should split Ansible output into typed blocks."""

    def test_parses_plays_and_tasks(self) -> None:
        blocks = parse_stdout_blocks(SAMPLE_ANSIBLE_OUTPUT)
        play_blocks = [b for b in blocks if b.kind == "play"]
        task_blocks = [b for b in blocks if b.kind == "task"]
        recap_blocks = [b for b in blocks if b.kind == "recap"]
        assert len(play_blocks) == 2
        assert play_blocks[0].play_name == "Deploy application"
        assert play_blocks[1].play_name == "Database setup"
        assert len(task_blocks) == 6
        assert len(recap_blocks) == 1

    def test_task_hosts_captured(self) -> None:
        blocks = parse_stdout_blocks(SAMPLE_ANSIBLE_OUTPUT)
        task_blocks = [b for b in blocks if b.kind == "task"]
        install = task_blocks[1]
        assert install.task_name == "Install packages"
        assert install.hosts == {"web01": "changed", "web02": "changed", "db01": "ok"}

    def test_fatal_captured(self) -> None:
        blocks = parse_stdout_blocks(SAMPLE_ANSIBLE_OUTPUT)
        task_blocks = [b for b in blocks if b.kind == "task"]
        configure = task_blocks[2]
        assert configure.task_name == "Configure service"
        assert configure.hosts["web02"] == "fatal"

    def test_crlf_line_endings(self) -> None:
        crlf_content = SAMPLE_ANSIBLE_OUTPUT.replace("\n", "\r\n")
        blocks = parse_stdout_blocks(crlf_content)
        play_blocks = [b for b in blocks if b.kind == "play"]
        task_blocks = [b for b in blocks if b.kind == "task"]
        assert len(play_blocks) == 2
        assert len(task_blocks) == 6
        assert play_blocks[0].play_name == "Deploy application"

    def test_no_play_lines(self) -> None:
        blocks = parse_stdout_blocks("just some output\nno plays here\n")
        assert len(blocks) == 1
        assert blocks[0].kind == "preamble"


class TestFilterStdout:
    """filter_stdout should apply various filter criteria correctly."""

    def test_no_filter_returns_everything(self) -> None:
        result = filter_stdout(SAMPLE_ANSIBLE_OUTPUT)
        assert "Deploy application" in result
        assert "Database setup" in result
        assert "PLAY RECAP" in result

    def test_filter_errors_only(self) -> None:
        result = filter_stdout(SAMPLE_ANSIBLE_OUTPUT, filter_mode="errors")
        assert "Configure service" in result
        assert "fatal: [web02]" in result
        assert "Install packages" not in result
        assert "PLAY RECAP" in result

    def test_filter_changed_only(self) -> None:
        result = filter_stdout(SAMPLE_ANSIBLE_OUTPUT, filter_mode="changed")
        assert "Install packages" in result
        assert "Run migrations" in result
        assert "Gathering Facts" not in result
        assert "Configure service" not in result

    def test_filter_by_play_name(self) -> None:
        result = filter_stdout(SAMPLE_ANSIBLE_OUTPUT, play="Database")
        assert "Database setup" in result
        assert "Run migrations" in result
        assert "Install packages" not in result

    def test_filter_by_play_index(self) -> None:
        result = filter_stdout(SAMPLE_ANSIBLE_OUTPUT, play="2")
        assert "Database setup" in result
        assert "Run migrations" in result
        assert "Install packages" not in result

    def test_filter_by_host(self) -> None:
        result = filter_stdout(SAMPLE_ANSIBLE_OUTPUT, host="db*")
        assert "ok: [db01]" in result
        assert "ok: [web01]" not in result

    def test_filter_by_task_pattern(self) -> None:
        result = filter_stdout(SAMPLE_ANSIBLE_OUTPUT, task="*package*")
        assert "Install packages" in result
        assert "Configure service" not in result
        assert "Run migrations" not in result

    def test_combined_host_and_errors(self) -> None:
        result = filter_stdout(SAMPLE_ANSIBLE_OUTPUT, filter_mode="errors", host="web*")
        assert "fatal: [web02]" in result
        assert "ok: [web01]" not in result

    def test_empty_content(self) -> None:
        result = filter_stdout("")
        assert result == ""

    def test_no_matching_blocks(self) -> None:
        result = filter_stdout(SAMPLE_ANSIBLE_OUTPUT, task="nonexistent*task*")
        assert "PLAY RECAP" in result
        assert "Install packages" not in result

    def test_crlf_line_endings(self) -> None:
        crlf_content = SAMPLE_ANSIBLE_OUTPUT.replace("\n", "\r\n")
        result = filter_stdout(crlf_content, filter_mode="errors")
        assert "fatal: [web02]" in result
        assert "Install packages" not in result

    def test_no_play_lines(self) -> None:
        content = "Some preamble output\nAnother line\n"
        result = filter_stdout(content, filter_mode="errors")
        assert "Some preamble output" in result

    def test_host_filter_all_mode(self) -> None:
        result = filter_stdout(SAMPLE_ANSIBLE_OUTPUT, host="web01")
        assert "ok: [web01]" in result
        assert "ok: [db01]" not in result
        assert "changed: [web02]" not in result


class TestStdoutCLIFilters:
    """awx-cli stdout should accept and apply filter flags."""

    def _handler(self, request: httpx.Request) -> httpx.Response:
        if "/jobs/100/stdout" in str(request.url):
            return httpx.Response(200, text=SAMPLE_ANSIBLE_OUTPUT)
        return httpx.Response(404, text="not found")

    def test_filter_errors_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(self._handler))
        result = runner.invoke(app, ["stdout", "100", "--filter", "errors"])
        assert result.exit_code == 0
        assert "fatal: [web02]" in result.output
        assert "Install packages" not in result.output

    def test_filter_host_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(self._handler))
        result = runner.invoke(app, ["stdout", "100", "--host", "db*"])
        assert result.exit_code == 0
        assert "ok: [db01]" in result.output

    def test_filter_task_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(self._handler))
        result = runner.invoke(app, ["stdout", "100", "--task", "*migration*"])
        assert result.exit_code == 0
        assert "Run migrations" in result.output
        assert "Install packages" not in result.output

    def test_filter_play_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(self._handler))
        result = runner.invoke(app, ["stdout", "100", "--play", "Database"])
        assert result.exit_code == 0
        assert "Database setup" in result.output
        assert "Deploy application" not in result.output

    def test_filter_json_output_includes_filtered_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(self._handler))
        result = runner.invoke(app, ["stdout", "100", "--filter", "errors", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["filtered"] is True
        assert "fatal" in data["content"]

    def test_no_filter_json_output_filtered_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(self._handler))
        result = runner.invoke(app, ["stdout", "100", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data.get("filtered") is not True


# ---------------------------------------------------------------------------
# Issue #38 — check-access SSH preflight
# ---------------------------------------------------------------------------


def _fake_ssh_run(returncode: int = 0, stderr: str = ""):
    """Return a mock subprocess.run result with the given returncode."""
    completed = MagicMock(spec=subprocess.CompletedProcess)
    completed.returncode = returncode
    completed.stdout = "uid=1000(ansible) gid=1000(ansible) groups=1000(ansible)"
    completed.stderr = stderr
    return completed


class TestCheckAccess:
    """awx-cli check-access should SSH-probe and report reachability."""

    def test_reachable_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli.subprocess.run", lambda *a, **kw: _fake_ssh_run(0))
        result = runner.invoke(app, ["check-access", "gpu-001.cloud.together.ai"])
        assert result.exit_code == 0
        assert "OK: ansible user reachable on gpu-001.cloud.together.ai" in result.output

    def test_unreachable_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli.subprocess.run", lambda *a, **kw: _fake_ssh_run(255))
        result = runner.invoke(app, ["check-access", "gpu-001.cloud.together.ai"])
        assert result.exit_code == 1
        assert "FAIL: ansible user unreachable on gpu-001.cloud.together.ai" in result.output
        assert "Remediation:" in result.output
        assert 'ansible-playbook ansible/prep-awx-access.yaml --limit "gpu-001*"' in result.output

    def test_reachable_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli.subprocess.run", lambda *a, **kw: _fake_ssh_run(0))
        result = runner.invoke(app, ["check-access", "gpu-001.cloud.together.ai", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["host"] == "gpu-001.cloud.together.ai"
        assert data["user"] == "ansible"
        assert data["reachable"] is True
        assert "remediation" not in data

    def test_unreachable_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "awx_mcp.cli.subprocess.run",
            lambda *a, **kw: _fake_ssh_run(255, stderr="Connection refused"),
        )
        result = runner.invoke(app, ["check-access", "gpu-001.cloud.together.ai", "--json"])
        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert data["reachable"] is False
        assert "prep-awx-access" in data["remediation"]
        assert '"gpu-001*"' in data["remediation"]
        assert data["error"] == "Connection refused"

    def test_custom_user(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured_args: list = []

        def capture_run(*args, **kwargs):
            captured_args.extend(args)
            return _fake_ssh_run(0)

        monkeypatch.setattr("awx_mcp.cli.subprocess.run", capture_run)
        result = runner.invoke(
            app, ["check-access", "myhost.example.com", "--user", "root"]
        )
        assert result.exit_code == 0
        assert "OK: root user reachable on myhost.example.com" in result.output
        ssh_cmd = captured_args[0]
        assert "root@myhost.example.com" in ssh_cmd

    def test_custom_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured_args: list = []

        def capture_run(*args, **kwargs):
            captured_args.extend(args)
            return _fake_ssh_run(0)

        monkeypatch.setattr("awx_mcp.cli.subprocess.run", capture_run)
        result = runner.invoke(
            app, ["check-access", "myhost.example.com", "--timeout", "15"]
        )
        assert result.exit_code == 0
        ssh_cmd = captured_args[0]
        assert "ConnectTimeout=15" in ssh_cmd

    def test_host_pattern_strips_domain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli.subprocess.run", lambda *a, **kw: _fake_ssh_run(255))
        result = runner.invoke(
            app, ["check-access", "research-common-h100-055.cloud.together.ai"]
        )
        assert result.exit_code == 1
        assert '"research-common-h100-055*"' in result.output

    def test_bare_hostname_pattern(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli.subprocess.run", lambda *a, **kw: _fake_ssh_run(255))
        result = runner.invoke(app, ["check-access", "gpu-node-001"])
        assert result.exit_code == 1
        assert '"gpu-node-001*"' in result.output

    def test_ssh_args_are_correct(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured_args: list = []
        captured_kwargs: dict = {}

        def capture_run(*args, **kwargs):
            captured_args.extend(args)
            captured_kwargs.update(kwargs)
            return _fake_ssh_run(0)

        monkeypatch.setattr("awx_mcp.cli.subprocess.run", capture_run)
        runner.invoke(app, ["check-access", "host.example.com"])
        ssh_cmd = captured_args[0]
        assert ssh_cmd[0] == "ssh"
        assert "ConnectTimeout=5" in ssh_cmd
        assert "StrictHostKeyChecking=accept-new" in ssh_cmd
        assert "BatchMode=yes" in ssh_cmd
        assert "ansible@host.example.com" in ssh_cmd
        assert "id" in ssh_cmd
        assert captured_kwargs["capture_output"] is True
        assert captured_kwargs["text"] is True
        assert captured_kwargs["timeout"] == 10  # ConnectTimeout(5) + 5

    def test_subprocess_timeout_treated_as_unreachable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def timeout_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="ssh", timeout=10)

        monkeypatch.setattr("awx_mcp.cli.subprocess.run", timeout_run)
        result = runner.invoke(app, ["check-access", "gpu-001.cloud.together.ai"])
        assert result.exit_code == 1
        assert "FAIL" in result.output

    def test_subprocess_timeout_json_has_error_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def timeout_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="ssh", timeout=10)

        monkeypatch.setattr("awx_mcp.cli.subprocess.run", timeout_run)
        result = runner.invoke(
            app, ["check-access", "gpu-001.cloud.together.ai", "--json"]
        )
        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert data["reachable"] is False
        assert data["error"] == "timeout"
        assert "remediation" in data

    def test_unreachable_json_error_permission_denied(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "awx_mcp.cli.subprocess.run",
            lambda *a, **kw: _fake_ssh_run(255, stderr="Permission denied (publickey)"),
        )
        result = runner.invoke(
            app, ["check-access", "gpu-001.cloud.together.ai", "--json"]
        )
        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert data["error"] == "Permission denied (publickey)"

    def test_ip_address_uses_full_ip_as_pattern(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("awx_mcp.cli.subprocess.run", lambda *a, **kw: _fake_ssh_run(255))
        result = runner.invoke(app, ["check-access", "10.0.0.1"])
        assert result.exit_code == 1
        assert '"10.0.0.1"' in result.output
        assert '"10*"' not in result.output

    def test_ip_address_json_pattern(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "awx_mcp.cli.subprocess.run",
            lambda *a, **kw: _fake_ssh_run(255, stderr="Connection refused"),
        )
        result = runner.invoke(app, ["check-access", "192.168.1.100", "--json"])
        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert '"192.168.1.100"' in data["remediation"]
        assert '"192*"' not in data["remediation"]


# ---------------------------------------------------------------------------
# Issue #61 — events command formatter
# ---------------------------------------------------------------------------


class TestEventsFormatter:
    """awx-cli events should display event_display, host_name, task, status."""

    EVENTS_RESPONSE: ClassVar[dict] = {
        "count": 3,
        "results": [
            {
                "id": 1,
                "event": "runner_on_ok",
                "event_display": "Host OK",
                "host_name": "web01",
                "event_data": {"task": "Gathering Facts"},
                "failed": False,
                "changed": False,
            },
            {
                "id": 2,
                "event": "runner_on_failed",
                "event_display": "Host Failed",
                "host_name": "web02",
                "event_data": {"task": "Install packages"},
                "failed": True,
                "changed": False,
            },
            {
                "id": 3,
                "event": "runner_on_ok",
                "event_display": "Host OK",
                "host_name": "db01",
                "event_data": {"task": "Run migrations"},
                "failed": False,
                "changed": True,
            },
        ],
    }

    def _handler(self, request: httpx.Request) -> httpx.Response:
        if "/jobs/100/job_events" in str(request.url):
            return httpx.Response(200, json=self.EVENTS_RESPONSE)
        return httpx.Response(404, text="not found")

    def test_events_shows_event_display(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(self._handler))
        result = runner.invoke(app, ["events", "100"])
        assert result.exit_code == 0
        assert "Host OK" in result.output
        assert "Host Failed" in result.output

    def test_events_shows_host_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(self._handler))
        result = runner.invoke(app, ["events", "100"])
        assert result.exit_code == 0
        assert "host=web01" in result.output
        assert "host=web02" in result.output
        assert "host=db01" in result.output

    def test_events_shows_task(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(self._handler))
        result = runner.invoke(app, ["events", "100"])
        assert result.exit_code == 0
        assert "task=Gathering Facts" in result.output
        assert "task=Install packages" in result.output

    def test_events_shows_failed_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(self._handler))
        result = runner.invoke(app, ["events", "100"])
        assert result.exit_code == 0
        assert "FAILED" in result.output

    def test_events_shows_changed_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(self._handler))
        result = runner.invoke(app, ["events", "100"])
        assert result.exit_code == 0
        assert "changed" in result.output

    def test_events_no_question_marks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The old bug: all events showed '?' because _format_resource_line was used."""
        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(self._handler))
        result = runner.invoke(app, ["events", "100"])
        assert result.exit_code == 0
        lines = [l for l in result.output.splitlines() if l.startswith("[")]
        for line in lines:
            assert line != "[?]  ?"
            assert "  ?  " not in line

    def test_events_json_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(self._handler))
        result = runner.invoke(app, ["events", "100", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["count"] == 3
        assert data["results"][0]["event_display"] == "Host OK"

    def test_events_fallback_to_event_field(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When event_display is missing, fall back to event field."""
        resp = {
            "count": 1,
            "results": [
                {
                    "id": 10,
                    "event": "playbook_on_start",
                    "event_data": {},
                    "failed": False,
                    "changed": False,
                }
            ],
        }

        def handler(request: httpx.Request) -> httpx.Response:
            if "/jobs/200/job_events" in str(request.url):
                return httpx.Response(200, json=resp)
            return httpx.Response(404, text="not found")

        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(handler))
        result = runner.invoke(app, ["events", "200"])
        assert result.exit_code == 0
        assert "playbook_on_start" in result.output


# ---------------------------------------------------------------------------
# Issue #50 — relaunch --on-failed
# ---------------------------------------------------------------------------


class TestRelaunchOnFailed:
    """awx-cli relaunch should support --on-failed for bulk relaunch on failed hosts."""

    captured_payload: ClassVar[dict | None] = None

    def _handler(self, request: httpx.Request) -> httpx.Response:
        if "/jobs/42/relaunch" in str(request.url) and request.method == "POST":
            TestRelaunchOnFailed.captured_payload = json.loads(request.content)
            return httpx.Response(200, json={"id": 99, "status": "pending"})
        return httpx.Response(404, text="not found")

    def test_on_failed_sends_hosts_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(self._handler))
        result = runner.invoke(app, ["relaunch", "42", "--on-failed", "--json"])
        assert result.exit_code == 0
        assert TestRelaunchOnFailed.captured_payload == {"hosts": "failed"}

    def test_on_failed_takes_precedence_over_hosts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(self._handler))
        result = runner.invoke(
            app, ["relaunch", "42", "--hosts", "web01", "--on-failed", "--json"]
        )
        assert result.exit_code == 0
        assert TestRelaunchOnFailed.captured_payload == {"hosts": "failed"}

    def test_no_flags_sends_empty_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(self._handler))
        result = runner.invoke(app, ["relaunch", "42", "--json"])
        assert result.exit_code == 0
        assert TestRelaunchOnFailed.captured_payload == {}

    def test_hosts_flag_sends_hosts_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(self._handler))
        result = runner.invoke(app, ["relaunch", "42", "--hosts", "web01,web02", "--json"])
        assert result.exit_code == 0
        assert TestRelaunchOnFailed.captured_payload == {"hosts": "web01,web02"}


# ---------------------------------------------------------------------------
# Issue #52 — inventory-audit command
# ---------------------------------------------------------------------------


class TestInventoryAudit:
    """awx-cli inventory-audit should diff AWX hosts against a reference list."""

    AWX_HOSTS: ClassVar[list[dict]] = [
        {"id": 1, "name": "host-a"},
        {"id": 2, "name": "host-b"},
        {"id": 3, "name": "host-c"},
    ]

    def _handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/inventories/" in url and "/hosts/" in url:
            return httpx.Response(
                200,
                json={"count": 3, "results": self.AWX_HOSTS, "next": None},
            )
        if request.method == "GET" and "/inventories/" in url and "name__iexact" in url:
            return httpx.Response(
                200,
                json={"count": 1, "results": [{"id": 256, "name": "Production"}]},
            )
        return httpx.Response(404, text="not found")

    def test_comma_separated_reference(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(self._handler))
        result = runner.invoke(
            app, ["inventory-audit", "256", "--reference", "host-a,host-b,host-d", "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["awx_host_count"] == 3
        assert data["reference_host_count"] == 3
        assert data["matched_count"] == 2
        assert data["stale_count"] == 1
        assert data["missing_count"] == 1
        assert "host-c" in data["stale"]
        assert "host-d" in data["missing"]
        assert "host-a" in data["matched"]

    def test_file_reference(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        host_file = tmp_path / "hosts.txt"
        host_file.write_text("host-a\nhost-b\nhost-d\n")
        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(self._handler))
        result = runner.invoke(
            app, ["inventory-audit", "256", "--reference", str(host_file), "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["stale"] == ["host-c"]
        assert data["missing"] == ["host-d"]

    def test_stdin_reference(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(self._handler))
        result = runner.invoke(
            app, ["inventory-audit", "256", "--stdin", "--json"], input="host-a\nhost-b\nhost-d\n"
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["stale"] == ["host-c"]
        assert data["missing"] == ["host-d"]

    def test_exact_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(self._handler))
        result = runner.invoke(
            app, ["inventory-audit", "256", "--reference", "host-a,host-b,host-c", "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["stale_count"] == 0
        assert data["missing_count"] == 0
        assert data["matched_count"] == 3

    def test_text_output_stale_and_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(self._handler))
        result = runner.invoke(
            app, ["inventory-audit", "256", "--reference", "host-a,host-d"]
        )
        assert result.exit_code == 0
        assert "STALE" in result.output
        assert "host-b" in result.output
        assert "host-c" in result.output
        assert "MISSING" in result.output
        assert "host-d" in result.output

    def test_text_output_exact_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(self._handler))
        result = runner.invoke(
            app, ["inventory-audit", "256", "--reference", "host-a,host-b,host-c"]
        )
        assert result.exit_code == 0
        assert "OK: AWX inventory matches reference exactly." in result.output

    def test_name_resolution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(self._handler))
        result = runner.invoke(
            app, ["inventory-audit", "Production", "--reference", "host-a,host-b,host-c", "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["inventory_id"] == 256

    def test_missing_reference_shows_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(self._handler))
        result = runner.invoke(app, ["inventory-audit", "256"])
        assert result.exit_code == 1
        assert "--reference" in result.output or "--stdin" in result.output

    def test_case_insensitive_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(self._handler))
        result = runner.invoke(
            app, ["inventory-audit", "256", "--reference", "Host-A,HOST-B,Host-C", "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["matched_count"] == 3
        assert data["stale_count"] == 0
        assert data["missing_count"] == 0


class TestReadReferenceHosts:
    """Unit tests for _read_reference_hosts helper."""

    def test_comma_separated(self) -> None:
        hosts = _read_reference_hosts("host1,host2,host3", reference_stdin=False)
        assert hosts == {"host1", "host2", "host3"}

    def test_file_input(self, tmp_path) -> None:
        f = tmp_path / "h.txt"
        f.write_text("alpha\nbeta\ngamma\n")
        hosts = _read_reference_hosts(str(f), reference_stdin=False)
        assert hosts == {"alpha", "beta", "gamma"}

    def test_mixed_commas_and_newlines(self) -> None:
        hosts = _read_reference_hosts("a,b\nc,d", reference_stdin=False)
        assert hosts == {"a", "b", "c", "d"}

    def test_strips_whitespace(self) -> None:
        hosts = _read_reference_hosts("  host1 , host2 \n host3 ", reference_stdin=False)
        assert hosts == {"host1", "host2", "host3"}

    def test_ignores_blank_lines(self) -> None:
        hosts = _read_reference_hosts("host1\n\n\nhost2\n", reference_stdin=False)
        assert hosts == {"host1", "host2"}

    def test_normalizes_case(self) -> None:
        hosts = _read_reference_hosts("Host-A,HOST-B,host-c", reference_stdin=False)
        assert hosts == {"host-a", "host-b", "host-c"}
# Issue #51 — inventory-sources command
# ---------------------------------------------------------------------------


class TestInventorySources:
    """awx-cli inventory-sources should list inventory sources for an inventory."""

    SOURCES_RESPONSE: ClassVar[dict] = {
        "count": 2,
        "results": [
            {"id": 10, "name": "NetBox Dynamic", "status": "successful"},
            {"id": 11, "name": "AWS EC2", "status": "failed"},
        ],
    }

    def _handler(self, request: httpx.Request) -> httpx.Response:
        if "/inventories/42/inventory_sources" in str(request.url):
            return httpx.Response(200, json=self.SOURCES_RESPONSE)
        return httpx.Response(404, text="not found")

    def test_list_sources(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(self._handler))
        result = runner.invoke(app, ["inventory-sources", "42"])
        assert result.exit_code == 0
        assert "NetBox Dynamic" in result.output
        assert "AWS EC2" in result.output

    def test_list_sources_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(self._handler))
        result = runner.invoke(app, ["inventory-sources", "42", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["count"] == 2
        assert data["results"][0]["name"] == "NetBox Dynamic"

    def test_list_sources_with_search(self, monkeypatch: pytest.MonkeyPatch) -> None:
        hit_urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            hit_urls.append(str(request.url))
            if "/inventories/42/inventory_sources" in str(request.url):
                return httpx.Response(
                    200,
                    json={"count": 1, "results": [{"id": 10, "name": "NetBox Dynamic"}]},
                )
            return httpx.Response(404, text="not found")

        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(handler))
        result = runner.invoke(app, ["inventory-sources", "42", "--search", "NetBox"])
        assert result.exit_code == 0
        assert any("name__icontains=NetBox" in u for u in hit_urls)


# ---------------------------------------------------------------------------
# Issue #51 — inventory-sync command
# ---------------------------------------------------------------------------


def _fake_clock(times: list[float]):
    """Return a callable that yields successive values from *times*, then repeats the last."""
    it = iter(times)
    last = [times[-1]]

    def clock():
        try:
            return next(it)
        except StopIteration:
            return last[0]

    return clock


class TestInventorySync:
    """awx-cli inventory-sync should trigger inventory source syncs."""

    def test_sync_by_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and "/inventory_sources/42/update/" in str(request.url):
                return httpx.Response(202, json={"id": 800, "status": "pending"})
            return httpx.Response(404, text="not found")

        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(handler))
        result = runner.invoke(app, ["inventory-sync", "42", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["id"] == 800

    def test_sync_by_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and "/inventory_sources/" in str(request.url):
                if "name__iexact=NetBox+Dynamic" in str(request.url) or "name__iexact=NetBox%20Dynamic" in str(request.url):
                    return httpx.Response(
                        200,
                        json={"count": 1, "results": [{"id": 42, "name": "NetBox Dynamic"}]},
                    )
            if request.method == "POST" and "/inventory_sources/42/update/" in str(request.url):
                return httpx.Response(202, json={"id": 801, "status": "pending"})
            return httpx.Response(404, text="not found")

        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(handler))
        result = runner.invoke(app, ["inventory-sync", "NetBox Dynamic", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["id"] == 801

    def test_sync_wait_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        poll_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal poll_count
            if request.method == "POST" and "/inventory_sources/42/update/" in str(request.url):
                return httpx.Response(202, json={"id": 802, "status": "pending"})
            if request.method == "GET" and "/inventory_updates/802/" in str(request.url):
                poll_count += 1
                if poll_count >= 2:
                    return httpx.Response(
                        200, json={"id": 802, "status": "successful"}
                    )
                return httpx.Response(200, json={"id": 802, "status": "running"})
            return httpx.Response(404, text="not found")

        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(handler))
        result = runner.invoke(
            app,
            ["inventory-sync", "42", "--wait", "--poll-interval", "0.1", "--timeout", "5", "--json"],
        )
        assert result.exit_code == 0
        assert "FINISHED: successful" in result.output

    def test_sync_wait_failure_exits_nonzero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and "/inventory_sources/42/update/" in str(request.url):
                return httpx.Response(202, json={"id": 803, "status": "pending"})
            if request.method == "GET" and "/inventory_updates/803/" in str(request.url):
                return httpx.Response(200, json={"id": 803, "status": "failed"})
            return httpx.Response(404, text="not found")

        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(handler))
        result = runner.invoke(
            app,
            ["inventory-sync", "42", "--wait", "--poll-interval", "0.1", "--timeout", "5", "--json"],
        )
        assert result.exit_code == 1
        assert "FINISHED: failed" in result.output

    def test_sync_wait_timeout_exits_2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("time.monotonic", _fake_clock([0, 0, 0, 5, 10]))

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and "/inventory_sources/42/update/" in str(request.url):
                return httpx.Response(202, json={"id": 804, "status": "pending"})
            if request.method == "GET" and "/inventory_updates/804/" in str(request.url):
                return httpx.Response(200, json={"id": 804, "status": "running"})
            return httpx.Response(404, text="not found")

        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(handler))
        result = runner.invoke(
            app,
            ["inventory-sync", "42", "--wait", "--poll-interval", "0.1", "--timeout", "1", "--json"],
        )
        assert result.exit_code == 2
        assert "Timed out" in result.output

    def test_sync_name_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and "/inventory_sources/" in str(request.url):
                return httpx.Response(200, json={"count": 0, "results": []})
            return httpx.Response(404, text="not found")

        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(handler))
        result = runner.invoke(app, ["inventory-sync", "nonexistent"])
        assert result.exit_code == 1
        assert "no inventory_sources found" in result.output

    def test_sync_no_wait(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and "/inventory_sources/42/update/" in str(request.url):
                return httpx.Response(202, json={"id": 805, "status": "pending"})
            return httpx.Response(404, text="not found")

        monkeypatch.setattr("awx_mcp.cli._client", lambda: _mock_client(handler))
        result = runner.invoke(app, ["inventory-sync", "42", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["id"] == 805
        assert data["status"] == "pending"
