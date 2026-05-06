"""Tests for awx_mcp.log_parser — Ansible log parsing and smart truncation."""

from awx_mcp.log_parser import (
    _strip_ansi,
    extract_failures,
    extract_recap,
    extract_warnings,
    parse_ansible_log,
    smart_truncate,
)

SAMPLE_LOG = """\
PLAY [Preflight checks] *******************************************************

TASK [Gathering Facts] *********************************************************
ok: [gpu101]
ok: [gpu102]
ok: [gpu103]

TASK [Check connectivity] ******************************************************
ok: [gpu101]
ok: [gpu102]
ok: [gpu103]

PLAY [Install packages] ********************************************************

TASK [Gathering Facts] *********************************************************
ok: [gpu101]
ok: [gpu102]
ok: [gpu103]

TASK [Install mlxconfig] *******************************************************
changed: [gpu101]
changed: [gpu102]
fatal: [gpu103]: FAILED! => {"changed": false, "msg": "mlxconfig: command not found", "rc": 127}

TASK [Configure network] *******************************************************
ok: [gpu101]
ok: [gpu102]

[WARNING]: Host 'gpu103' had errors during configuration
[WARNING]: Retrying in 30 seconds

PLAY RECAP *********************************************************************
gpu101                     : ok=5    changed=1    unreachable=0    failed=0    skipped=0    rescued=0    ignored=0
gpu102                     : ok=5    changed=1    unreachable=0    failed=0    skipped=0    rescued=0    ignored=0
gpu103                     : ok=3    changed=0    unreachable=0    failed=1    skipped=0    rescued=0    ignored=0
"""

SAMPLE_LOG_SUCCESS = """\
PLAY [Deploy app] **************************************************************

TASK [Gathering Facts] *********************************************************
ok: [host1]

TASK [Copy files] **************************************************************
changed: [host1]

PLAY RECAP *********************************************************************
host1                      : ok=2    changed=1    unreachable=0    failed=0    skipped=0    rescued=0    ignored=0
"""

SAMPLE_LOG_ITEM_FAILURE = """\
PLAY [Configure IB] ************************************************************

TASK [Set mlxconfig settings for devices] **************************************
changed: [gpu101] => (item=mlx5_0)
changed: [gpu101] => (item=mlx5_1)
failed: [gpu102] (item=mlx5_9) => {"ansible_loop_var": "item", "changed": true, "cmd": ["mlxconfig", "-y", "-d", "mlx5_9", "s"], "msg": "non-zero return code", "rc": 1}
changed: [gpu101] => (item=mlx5_9)

PLAY RECAP *********************************************************************
gpu101                     : ok=3    changed=3    unreachable=0    failed=0    skipped=0    rescued=0    ignored=0
gpu102                     : ok=1    changed=0    unreachable=0    failed=1    skipped=0    rescued=0    ignored=0
"""

SAMPLE_LOG_UNREACHABLE = """\
PLAY [Check hosts] *************************************************************

TASK [Gathering Facts] *********************************************************
fatal: [deadhost]: UNREACHABLE! => {"changed": false, "msg": "Failed to connect", "unreachable": true}

PLAY RECAP *********************************************************************
deadhost                   : ok=0    changed=0    unreachable=1    failed=0    skipped=0    rescued=0    ignored=0
"""


class TestParseAnsibleLog:
    def test_extracts_play_names(self):
        result = parse_ansible_log(SAMPLE_LOG)
        assert result.plays == ["Preflight checks", "Install packages"]

    def test_extracts_task_names(self):
        result = parse_ansible_log(SAMPLE_LOG)
        assert "Gathering Facts" in result.tasks
        assert "Check connectivity" in result.tasks
        assert "Install mlxconfig" in result.tasks
        assert "Configure network" in result.tasks

    def test_extracts_failed_tasks(self):
        result = parse_ansible_log(SAMPLE_LOG)
        assert len(result.failed_tasks) == 1
        ft = result.failed_tasks[0]
        assert ft.host == "gpu103"
        assert ft.task == "Install mlxconfig"
        assert ft.module == "FAILED"
        assert "mlxconfig: command not found" in ft.message

    def test_extracts_warnings(self):
        result = parse_ansible_log(SAMPLE_LOG)
        assert len(result.warnings) == 2
        assert any("gpu103" in w for w in result.warnings)
        assert any("Retrying" in w for w in result.warnings)

    def test_extracts_host_stats(self):
        result = parse_ansible_log(SAMPLE_LOG)
        assert len(result.host_stats) == 3
        stats_by_host = {h.host: h for h in result.host_stats}
        assert stats_by_host["gpu101"].ok == 5
        assert stats_by_host["gpu101"].changed == 1
        assert stats_by_host["gpu101"].failed == 0
        assert stats_by_host["gpu103"].failed == 1
        assert stats_by_host["gpu103"].ok == 3

    def test_has_failures_true_for_failed_job(self):
        result = parse_ansible_log(SAMPLE_LOG)
        assert result.has_failures is True

    def test_has_failures_false_for_successful_job(self):
        result = parse_ansible_log(SAMPLE_LOG_SUCCESS)
        assert result.has_failures is False

    def test_overall_result_failed(self):
        result = parse_ansible_log(SAMPLE_LOG)
        assert result.overall_result == "failed"

    def test_overall_result_successful(self):
        result = parse_ansible_log(SAMPLE_LOG_SUCCESS)
        assert result.overall_result == "successful"

    def test_overall_result_unreachable(self):
        result = parse_ansible_log(SAMPLE_LOG_UNREACHABLE)
        assert result.overall_result == "unreachable"

    def test_recap_text_present(self):
        result = parse_ansible_log(SAMPLE_LOG)
        assert "PLAY RECAP" in result.recap_text
        assert "gpu101" in result.recap_text

    def test_total_lines(self):
        result = parse_ansible_log(SAMPLE_LOG)
        assert result.total_lines > 30

    def test_to_dict_roundtrip(self):
        result = parse_ansible_log(SAMPLE_LOG)
        d = result.to_dict()
        assert isinstance(d, dict)
        assert d["has_failures"] is True
        assert d["overall_result"] == "failed"
        assert len(d["failed_tasks"]) == 1
        assert d["failed_tasks"][0]["host"] == "gpu103"

    def test_empty_log(self):
        result = parse_ansible_log("")
        assert result.plays == []
        assert result.tasks == []
        assert result.failed_tasks == []
        assert result.warnings == []
        assert result.host_stats == []
        assert result.overall_result == "unknown"

    def test_log_without_recap(self):
        partial = "PLAY [test] ***\n\nTASK [setup] ***\nok: [host1]\n"
        result = parse_ansible_log(partial)
        assert result.plays == ["test"]
        assert result.recap_text == ""
        assert result.overall_result == "unknown"

    def test_item_failure_detected(self):
        result = parse_ansible_log(SAMPLE_LOG_ITEM_FAILURE)
        assert result.has_failures is True
        assert result.overall_result == "failed"
        assert len(result.failed_tasks) == 1
        ft = result.failed_tasks[0]
        assert ft.host == "gpu102"
        assert "item=mlx5_9" in ft.task
        assert "non-zero return code" in ft.message

    def test_item_failure_host_stats(self):
        result = parse_ansible_log(SAMPLE_LOG_ITEM_FAILURE)
        stats_by_host = {h.host: h for h in result.host_stats}
        assert stats_by_host["gpu102"].failed == 1
        assert stats_by_host["gpu101"].failed == 0


class TestExtractRecap:
    def test_extracts_recap(self):
        result = extract_recap(SAMPLE_LOG)
        assert result["found"] is True
        assert "PLAY RECAP" in result["recap_text"]
        assert len(result["host_stats"]) == 3

    def test_no_recap(self):
        result = extract_recap("TASK [setup] ***\nok: [host1]\n")
        assert result["found"] is False
        assert result["host_stats"] == []


class TestExtractFailures:
    def test_extracts_with_context(self):
        failures = extract_failures(SAMPLE_LOG)
        assert len(failures) == 1
        f = failures[0]
        assert f["host"] == "gpu103"
        assert f["task"] == "Install mlxconfig"
        assert "context" in f
        assert f["line_number"] > 0

    def test_no_failures(self):
        failures = extract_failures(SAMPLE_LOG_SUCCESS)
        assert failures == []


class TestExtractWarnings:
    def test_extracts_warnings(self):
        warnings = extract_warnings(SAMPLE_LOG)
        assert len(warnings) == 2

    def test_no_warnings(self):
        warnings = extract_warnings(SAMPLE_LOG_SUCCESS)
        assert warnings == []


class TestSmartTruncate:
    def test_no_truncation_when_within_limit(self):
        result = smart_truncate("short content", 1000, strategy="tail")
        assert result["truncated"] is False
        assert result["content"] == "short content"

    def test_head_strategy(self):
        content = "A" * 100
        result = smart_truncate(content, 50, strategy="head")
        assert result["truncated"] is True
        assert len(result["content"]) == 50
        assert result["strategy"] == "head"
        assert result["content"] == "A" * 50

    def test_tail_strategy(self):
        content = "line1\nline2\nline3\nline4\nline5\nline6\n"
        result = smart_truncate(content, 20, strategy="tail")
        assert result["truncated"] is True
        assert result["strategy"] == "tail"
        assert "line6" in result["content"]

    def test_head_tail_strategy(self):
        content = "HEAD" * 100 + "MIDDLE" * 1000 + "TAIL" * 100
        result = smart_truncate(content, 500, strategy="head_tail")
        assert result["truncated"] is True
        assert result["strategy"] == "head_tail"
        assert "HEAD" in result["content"]
        assert "TAIL" in result["content"]
        assert "omitted" in result["content"]

    def test_recap_context_strategy_with_recap(self):
        result = smart_truncate(SAMPLE_LOG, 600, strategy="recap_context")
        assert result["truncated"] is True
        assert "PLAY RECAP" in result["content"]

    def test_recap_context_falls_back_to_tail(self):
        content_no_recap = "line\n" * 1000
        result = smart_truncate(content_no_recap, 100, strategy="recap_context")
        assert result["truncated"] is True
        assert result["strategy"] == "tail"

    def test_original_length_always_present(self):
        result = smart_truncate("test", 1000, strategy="head")
        assert result["original_length"] == 4

    def test_unknown_strategy_falls_back_to_tail(self):
        content = "x" * 100
        result = smart_truncate(content, 50, strategy="bogus")
        assert result["strategy"] == "tail"


# ---------------------------------------------------------------------------
# Issue #57 — log-summary ANSI stripping and clean recap parsing
# ---------------------------------------------------------------------------

SAMPLE_LOG_CLEAN_RECAP = """\
PLAY [Configure nodes] ********************************************************

TASK [Gathering Facts] *********************************************************
ok: [host1]
ok: [host2]

TASK [Apply settings] **********************************************************
ok: [host1]
ok: [host2]

PLAY RECAP *********************************************************************
host1                      : ok=15   changed=0    unreachable=0    failed=0    skipped=3    rescued=0    ignored=0
host2                      : ok=15   changed=0    unreachable=0    failed=0    skipped=3    rescued=0    ignored=0
"""

SAMPLE_LOG_ANSI = (
    "\x1b[0;36mPLAY [Configure nodes]\x1b[0m "
    "********************************************************\n"
    "\n"
    "\x1b[0;36mTASK [Gathering Facts]\x1b[0m "
    "*********************************************************\n"
    "\x1b[0;32mok: [host1]\x1b[0m\n"
    "\x1b[0;32mok: [host2]\x1b[0m\n"
    "\n"
    "\x1b[0;36mTASK [Apply settings]\x1b[0m "
    "**********************************************************\n"
    "\x1b[0;32mok: [host1]\x1b[0m\n"
    "\x1b[0;32mok: [host2]\x1b[0m\n"
    "\n"
    "\x1b[0;36mPLAY RECAP\x1b[0m "
    "*********************************************************************\n"
    "\x1b[0;32mhost1\x1b[0m"
    "                      : \x1b[0;32mok=15\x1b[0m   changed=0    "
    "unreachable=0    failed=0    skipped=3    rescued=0    ignored=0\n"
    "\x1b[0;32mhost2\x1b[0m"
    "                      : \x1b[0;32mok=15\x1b[0m   changed=0    "
    "unreachable=0    failed=0    skipped=3    rescued=0    ignored=0\n"
)


class TestStripAnsi:
    def test_removes_color_codes(self):
        colored = "\x1b[0;32mok: [host1]\x1b[0m"
        assert _strip_ansi(colored) == "ok: [host1]"

    def test_noop_on_plain_text(self):
        plain = "ok: [host1]"
        assert _strip_ansi(plain) == plain


class TestCleanRecapParsing:
    """Issue #57: successful jobs with a clean recap must return overall_result='successful'."""

    def test_clean_recap_overall_result(self):
        result = parse_ansible_log(SAMPLE_LOG_CLEAN_RECAP)
        assert result.overall_result == "successful"
        assert result.has_failures is False

    def test_clean_recap_host_stats(self):
        result = parse_ansible_log(SAMPLE_LOG_CLEAN_RECAP)
        assert len(result.host_stats) == 2
        stats = {h.host: h for h in result.host_stats}
        assert stats["host1"].ok == 15
        assert stats["host1"].failed == 0
        assert stats["host2"].skipped == 3

    def test_ansi_colored_recap_parses_correctly(self):
        result = parse_ansible_log(SAMPLE_LOG_ANSI)
        assert result.overall_result == "successful"
        assert len(result.host_stats) == 2
        assert result.plays == ["Configure nodes"]

    def test_ansi_colored_host_stats_values(self):
        result = parse_ansible_log(SAMPLE_LOG_ANSI)
        stats = {h.host: h for h in result.host_stats}
        assert stats["host1"].ok == 15
        assert stats["host1"].changed == 0
        assert stats["host2"].failed == 0

    def test_ansi_extract_recap(self):
        result = extract_recap(SAMPLE_LOG_ANSI)
        assert result["found"] is True
        assert len(result["host_stats"]) == 2

    def test_ansi_extract_warnings(self):
        content = "\x1b[0;33m[WARNING]: Some warning\x1b[0m\n"
        warnings = extract_warnings(content)
        assert len(warnings) == 1
        assert "Some warning" in warnings[0]

    def test_many_hosts_recap(self):
        lines = [
            "PLAY [Deploy] ************************************************************",
            "",
            "TASK [Setup] *************************************************************",
        ]
        recap_lines = ["PLAY RECAP *********************************************************************"]
        for i in range(50):
            lines.append(f"ok: [node{i:03d}]")
            recap_lines.append(
                f"node{i:03d}                    : ok=10   changed=2    "
                f"unreachable=0    failed=0    skipped=1    rescued=0    ignored=0"
            )
        content = "\n".join(lines + [""] + recap_lines) + "\n"
        result = parse_ansible_log(content)
        assert result.overall_result == "successful"
        assert len(result.host_stats) == 50
