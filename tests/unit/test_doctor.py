"""Unit tests for credential chain doctor."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from mcpanvil.doctor import (
    CheckResult,
    DoctorReport,
    check_env_credentials,
    check_keyctl,
    check_op_auth,
    check_op_cli,
    check_op_forward_relay,
    check_os,
    render_report,
    run,
)


@pytest.fixture()
def report() -> DoctorReport:
    return DoctorReport()


@pytest.fixture()
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove all credential-shaped env vars to ensure deterministic tests."""
    for key in list(__import__("os").environ.keys()):
        if any(key.endswith(s) for s in ("_TOKEN", "_PASSWORD", "_API_KEY", "_SECRET")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("OP_SERVICE_ACCOUNT_TOKEN", raising=False)


class TestCheckOS:
    def test_records_os_pass(self, report: DoctorReport) -> None:
        check_os(report)
        assert len(report.checks) == 1
        assert report.checks[0].name == "OS"
        assert report.checks[0].status == "pass"

    def test_devcontainer_detected_via_env(
        self, report: DoctorReport, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DEVCONTAINER", "true")
        with patch("mcpanvil.doctor.os.path.exists", return_value=False):
            check_os(report)
        assert "devcontainer" in report.checks[0].detail


class TestCheckKeyctl:
    def test_skip_on_macos(self, report: DoctorReport) -> None:
        with patch("mcpanvil.doctor.platform.system", return_value="Darwin"):
            check_keyctl(report)
        assert report.checks[0].status == "skip"
        assert "not Linux" in report.checks[0].detail

    def test_fail_when_missing(self, report: DoctorReport) -> None:
        with (
            patch("mcpanvil.doctor.platform.system", return_value="Linux"),
            patch("mcpanvil.doctor.shutil.which", return_value=None),
        ):
            check_keyctl(report)
        assert report.checks[0].status == "fail"
        assert "not installed" in report.checks[0].detail
        assert "apt-get" in report.checks[0].fix

    def test_pass_when_keyring_writable(self, report: DoctorReport) -> None:
        add_ok = MagicMock(returncode=0, stdout="123\n", stderr="")
        revoke_ok = MagicMock(returncode=0)
        with (
            patch("mcpanvil.doctor.platform.system", return_value="Linux"),
            patch("mcpanvil.doctor.shutil.which", return_value="/usr/bin/keyctl"),
            patch("mcpanvil.doctor.subprocess.run", side_effect=[add_ok, revoke_ok]) as mock_run,
        ):
            check_keyctl(report)
        assert report.checks[0].status == "pass"
        assert "/usr/bin/keyctl" in report.checks[0].detail
        first_call = mock_run.call_args_list[0]
        assert first_call.args[0][:3] == ["keyctl", "add", "user"]

    def test_fail_when_keyring_not_writable(self, report: DoctorReport) -> None:
        add_fail = MagicMock(returncode=1, stdout="", stderr="permission denied")
        with (
            patch("mcpanvil.doctor.platform.system", return_value="Linux"),
            patch("mcpanvil.doctor.shutil.which", return_value="/usr/bin/keyctl"),
            patch("mcpanvil.doctor.subprocess.run", return_value=add_fail),
        ):
            check_keyctl(report)
        assert report.checks[0].status == "fail"
        assert "session keyring not writable" in report.checks[0].detail

    def test_handles_subprocess_timeout(self, report: DoctorReport) -> None:
        with (
            patch("mcpanvil.doctor.platform.system", return_value="Linux"),
            patch("mcpanvil.doctor.shutil.which", return_value="/usr/bin/keyctl"),
            patch(
                "mcpanvil.doctor.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="keyctl", timeout=5),
            ),
        ):
            check_keyctl(report)
        assert report.checks[0].status == "fail"
        assert "keyctl error" in report.checks[0].detail


class TestCheckOpCli:
    def test_warn_when_missing(self, report: DoctorReport) -> None:
        with patch("mcpanvil.doctor.shutil.which", return_value=None):
            check_op_cli(report)
        assert report.checks[0].status == "warn"
        assert "not installed" in report.checks[0].detail

    def test_pass_when_present(self, report: DoctorReport) -> None:
        version_ok = MagicMock(returncode=0, stdout="2.33.0\n", stderr="")
        with (
            patch("mcpanvil.doctor.shutil.which", return_value="/usr/bin/op"),
            patch("mcpanvil.doctor.subprocess.run", return_value=version_ok),
        ):
            check_op_cli(report)
        assert report.checks[0].status == "pass"
        assert "2.33.0" in report.checks[0].detail
        assert "/usr/bin/op" in report.checks[0].detail

    def test_pass_with_unknown_version(self, report: DoctorReport) -> None:
        with (
            patch("mcpanvil.doctor.shutil.which", return_value="/usr/bin/op"),
            patch(
                "mcpanvil.doctor.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="op", timeout=5),
            ),
        ):
            check_op_cli(report)
        assert report.checks[0].status == "pass"
        assert "unknown" in report.checks[0].detail


class TestCheckOpAuth:
    def test_skip_when_op_missing(self, report: DoctorReport, clean_env: None) -> None:
        with patch("mcpanvil.doctor.shutil.which", return_value=None):
            check_op_auth(report)
        assert report.checks[0].status == "skip"

    def test_pass_with_service_account(
        self, report: DoctorReport, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OP_SERVICE_ACCOUNT_TOKEN", "ops_test")
        vault_list_ok = MagicMock(returncode=0, stdout="vault1", stderr="")
        with (
            patch("mcpanvil.doctor.shutil.which", return_value="/usr/bin/op"),
            patch("mcpanvil.doctor.subprocess.run", return_value=vault_list_ok),
        ):
            check_op_auth(report)
        assert report.checks[0].status == "pass"
        assert "service account" in report.checks[0].detail

    def test_fail_when_service_account_rejected(
        self, report: DoctorReport, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OP_SERVICE_ACCOUNT_TOKEN", "ops_bad")
        vault_list_fail = MagicMock(returncode=1, stdout="", stderr="auth failed")
        with (
            patch("mcpanvil.doctor.shutil.which", return_value="/usr/bin/op"),
            patch("mcpanvil.doctor.subprocess.run", return_value=vault_list_fail),
        ):
            check_op_auth(report)
        assert report.checks[0].status == "fail"
        assert "rejected" in report.checks[0].detail
        assert "auth failed" in report.checks[0].fix

    def test_pass_with_session(self, report: DoctorReport, clean_env: None) -> None:
        account_list_ok = MagicMock(
            returncode=0,
            stdout="URL                EMAIL              USER ID\nteam.1password.com a@b   ABC123\n",
            stderr="",
        )
        with (
            patch("mcpanvil.doctor.shutil.which", return_value="/usr/bin/op"),
            patch("mcpanvil.doctor.subprocess.run", return_value=account_list_ok),
        ):
            check_op_auth(report)
        assert report.checks[0].status == "pass"
        assert "1 account" in report.checks[0].detail

    def test_fail_when_no_session(self, report: DoctorReport, clean_env: None) -> None:
        account_list_empty = MagicMock(returncode=0, stdout="", stderr="")
        with (
            patch("mcpanvil.doctor.shutil.which", return_value="/usr/bin/op"),
            patch("mcpanvil.doctor.subprocess.run", return_value=account_list_empty),
        ):
            check_op_auth(report)
        assert report.checks[0].status == "fail"
        assert "no active op session" in report.checks[0].detail
        assert "op signin" in report.checks[0].fix


class TestCheckOpForwardRelay:
    def test_skip_on_macos(self, report: DoctorReport) -> None:
        with patch("mcpanvil.doctor.platform.system", return_value="Darwin"):
            check_op_forward_relay(report)
        assert report.checks[0].status == "skip"
        assert "not Linux" in report.checks[0].detail

    def test_skip_on_linux_host(
        self, report: DoctorReport, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DEVCONTAINER", raising=False)
        with (
            patch("mcpanvil.doctor.platform.system", return_value="Linux"),
            patch("mcpanvil.doctor.os.path.exists", return_value=False),
        ):
            check_op_forward_relay(report)
        assert report.checks[0].status == "skip"
        assert "not a container" in report.checks[0].detail

    def test_pass_when_port_open(
        self, report: DoctorReport, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DEVCONTAINER", "true")
        fake_socket = MagicMock()
        fake_socket.__enter__ = MagicMock(return_value=fake_socket)
        fake_socket.__exit__ = MagicMock(return_value=False)
        with (
            patch("mcpanvil.doctor.platform.system", return_value="Linux"),
            patch("mcpanvil.doctor.os.path.exists", return_value=False),
            patch(
                "mcpanvil.doctor.socket.create_connection", return_value=fake_socket
            ) as mock_conn,
        ):
            check_op_forward_relay(report)
        mock_conn.assert_called_once_with(("127.0.0.1", 18340), timeout=2)
        assert report.checks[0].status == "pass"

    def test_fail_when_port_closed(
        self, report: DoctorReport, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DEVCONTAINER", "true")
        with (
            patch("mcpanvil.doctor.platform.system", return_value="Linux"),
            patch("mcpanvil.doctor.os.path.exists", return_value=False),
            patch(
                "mcpanvil.doctor.socket.create_connection",
                side_effect=ConnectionRefusedError(),
            ),
        ):
            check_op_forward_relay(report)
        assert report.checks[0].status == "fail"
        assert "not reachable" in report.checks[0].detail
        assert "socat" in report.checks[0].fix
        assert "op-forward service install" in report.checks[0].fix

    def test_fail_on_socket_timeout(
        self, report: DoctorReport, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DEVCONTAINER", "true")
        with (
            patch("mcpanvil.doctor.platform.system", return_value="Linux"),
            patch("mcpanvil.doctor.os.path.exists", return_value=False),
            patch(
                "mcpanvil.doctor.socket.create_connection",
                side_effect=TimeoutError("timed out"),
            ),
        ):
            check_op_forward_relay(report)
        assert report.checks[0].status == "fail"
        assert "op-forward service install" in report.checks[0].fix


class TestCheckEnvCredentials:
    def test_warn_when_no_candidates(self, report: DoctorReport, clean_env: None) -> None:
        check_env_credentials(report)
        assert report.checks[0].status == "warn"
        assert "no credential-shaped" in report.checks[0].detail

    def test_classifies_static_token(
        self, report: DoctorReport, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NETBOX_TOKEN", "abc123")
        with patch("mcpanvil.doctor.shutil.which", return_value=None):
            check_env_credentials(report)
        assert report.checks[0].status == "pass"
        assert "1 found" in report.checks[0].detail
        assert "1 static" in report.checks[0].detail

    def test_classifies_op_reference(
        self, report: DoctorReport, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NETBOX_TOKEN", "op://Vault/Item/field")
        with patch("mcpanvil.doctor.shutil.which", return_value=None):
            check_env_credentials(report)
        assert report.checks[0].status == "pass"
        assert "1 op://" in report.checks[0].detail

    def test_classifies_vault_reference(
        self, report: DoctorReport, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("APP_SECRET", "vault://secret/path")
        with patch("mcpanvil.doctor.shutil.which", return_value=None):
            check_env_credentials(report)
        assert report.checks[0].status == "pass"
        assert "1 vault://" in report.checks[0].detail

    def test_classifies_empty(
        self, report: DoctorReport, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FOO_PASSWORD", "")
        with patch("mcpanvil.doctor.shutil.which", return_value=None):
            check_env_credentials(report)
        assert report.checks[0].status == "pass"
        assert "1 empty" in report.checks[0].detail

    def test_classifies_mixed(
        self, report: DoctorReport, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("A_TOKEN", "static-val")
        monkeypatch.setenv("B_API_KEY", "op://Vault/Item/key")
        monkeypatch.setenv("C_SECRET", "")
        with patch("mcpanvil.doctor.shutil.which", return_value=None):
            check_env_credentials(report)
        assert report.checks[0].status == "pass"
        detail = report.checks[0].detail
        assert "3 found" in detail
        assert "1 static" in detail
        assert "1 op://" in detail
        assert "1 empty" in detail

    def test_op_resolution_smoke_test_pass(
        self, report: DoctorReport, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NETBOX_TOKEN", "op://Vault/Item/field")
        op_read_ok = MagicMock(returncode=0, stdout="resolved-secret\n", stderr="")
        with (
            patch("mcpanvil.doctor.shutil.which", return_value="/usr/bin/op"),
            patch("mcpanvil.doctor.subprocess.run", return_value=op_read_ok),
        ):
            check_env_credentials(report)
        assert len(report.checks) == 2
        resolution = report.checks[1]
        assert resolution.status == "pass"
        assert "NETBOX_TOKEN" in resolution.name
        assert "resolved successfully" in resolution.detail
        assert "redacted" in resolution.detail
        assert "resolved-secret" not in resolution.detail

    def test_op_resolution_smoke_test_fail(
        self, report: DoctorReport, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NETBOX_TOKEN", "op://Vault/Item/field")
        op_read_fail = MagicMock(returncode=1, stdout="", stderr="item not found")
        with (
            patch("mcpanvil.doctor.shutil.which", return_value="/usr/bin/op"),
            patch("mcpanvil.doctor.subprocess.run", return_value=op_read_fail),
        ):
            check_env_credentials(report)
        resolution = report.checks[1]
        assert resolution.status == "fail"
        assert "item not found" in resolution.detail

    def test_op_resolution_timeout(
        self, report: DoctorReport, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NETBOX_TOKEN", "op://Vault/Item/field")
        with (
            patch("mcpanvil.doctor.shutil.which", return_value="/usr/bin/op"),
            patch(
                "mcpanvil.doctor.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="op", timeout=15),
            ),
        ):
            check_env_credentials(report)
        resolution = report.checks[1]
        assert resolution.status == "fail"
        assert "timed out" in resolution.detail


class TestRenderReport:
    def test_renders_pass(self) -> None:
        report = DoctorReport()
        report.add(CheckResult(name="OS", status="pass", detail="Linux"))
        out = render_report(report, use_color=False)
        assert "[OK]" in out
        assert "OS" in out
        assert "Linux" in out
        assert "all checks passed" in out

    def test_renders_fail_with_fix(self) -> None:
        report = DoctorReport()
        report.add(
            CheckResult(
                name="keyctl",
                status="fail",
                detail="not installed",
                fix="apt-get install keyutils",
            )
        )
        out = render_report(report, use_color=False)
        assert "[FAIL]" in out
        assert "fix: apt-get install keyutils" in out
        assert "1 check(s) failed" in out

    def test_no_color_in_non_tty(self) -> None:
        report = DoctorReport()
        report.add(CheckResult(name="OS", status="pass", detail="Linux"))
        out = render_report(report, use_color=False)
        assert "\033[" not in out

    def test_warn_treated_as_passing(self) -> None:
        report = DoctorReport()
        report.add(CheckResult(name="op CLI", status="warn", detail="missing"))
        out = render_report(report, use_color=False)
        assert "[WARN]" in out
        assert "all checks passed" in out

    def test_skip_treated_as_passing(self) -> None:
        report = DoctorReport()
        report.add(CheckResult(name="keyctl", status="skip", detail="not Linux"))
        out = render_report(report, use_color=False)
        assert "[SKIP]" in out
        assert "all checks passed" in out

    def test_never_prints_credential_values(
        self, monkeypatch: pytest.MonkeyPatch, clean_env: None
    ) -> None:
        """The most critical safety guarantee: secret values must never appear."""
        secret = "secret_value_xyz_1234567890"
        monkeypatch.setenv("NETBOX_TOKEN", secret)

        report = DoctorReport()
        check_env_credentials(report)
        rendered = render_report(report, use_color=False)

        assert secret not in rendered
        for check in report.checks:
            assert secret not in check.name
            assert secret not in check.detail
            assert secret not in check.fix

    def test_op_resolution_never_prints_resolved_secret(
        self, monkeypatch: pytest.MonkeyPatch, clean_env: None
    ) -> None:
        """When op:// resolves, the resolved value must not appear in output."""
        ref = "op://Vault/Item/field"
        resolved_secret = "ULTRA_SECRET_RESOLVED_VALUE_99"
        monkeypatch.setenv("NETBOX_TOKEN", ref)
        op_read_ok = MagicMock(returncode=0, stdout=resolved_secret + "\n", stderr="")

        with (
            patch("mcpanvil.doctor.shutil.which", return_value="/usr/bin/op"),
            patch("mcpanvil.doctor.subprocess.run", return_value=op_read_ok),
        ):
            report = DoctorReport()
            check_env_credentials(report)

        rendered = render_report(report, use_color=False)
        assert resolved_secret not in rendered
        for check in report.checks:
            assert resolved_secret not in check.detail


class TestRun:
    def test_returns_zero_when_all_pass(self, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch("mcpanvil.doctor.check_os") as m_os,
            patch("mcpanvil.doctor.check_keyctl") as m_keyctl,
            patch("mcpanvil.doctor.check_op_cli") as m_op_cli,
            patch("mcpanvil.doctor.check_op_auth") as m_op_auth,
            patch("mcpanvil.doctor.check_op_forward_relay") as m_relay,
            patch("mcpanvil.doctor.check_env_credentials") as m_env,
        ):

            def add_pass(check_name: str):
                def adder(report: DoctorReport) -> None:
                    report.add(CheckResult(name=check_name, status="pass", detail="ok"))

                return adder

            m_os.side_effect = add_pass("OS")
            m_keyctl.side_effect = add_pass("keyctl")
            m_op_cli.side_effect = add_pass("op CLI")
            m_op_auth.side_effect = add_pass("op auth")
            m_relay.side_effect = add_pass("relay")
            m_env.side_effect = add_pass("env")

            rc = run()

        assert rc == 0
        captured = capsys.readouterr()
        assert "all checks passed" in captured.out

    def test_returns_one_when_any_fail(self, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch("mcpanvil.doctor.check_os") as m_os,
            patch("mcpanvil.doctor.check_keyctl") as m_keyctl,
            patch("mcpanvil.doctor.check_op_cli") as m_op_cli,
            patch("mcpanvil.doctor.check_op_auth") as m_op_auth,
            patch("mcpanvil.doctor.check_op_forward_relay") as m_relay,
            patch("mcpanvil.doctor.check_env_credentials") as m_env,
        ):

            def add_pass(name: str):
                def adder(report: DoctorReport) -> None:
                    report.add(CheckResult(name=name, status="pass", detail="ok"))

                return adder

            def add_fail(report: DoctorReport) -> None:
                report.add(CheckResult(name="keyctl", status="fail", detail="missing"))

            m_os.side_effect = add_pass("OS")
            m_keyctl.side_effect = add_fail
            m_op_cli.side_effect = add_pass("op CLI")
            m_op_auth.side_effect = add_pass("op auth")
            m_relay.side_effect = add_pass("relay")
            m_env.side_effect = add_pass("env")

            rc = run()

        assert rc == 1
        captured = capsys.readouterr()
        assert "1 check(s) failed" in captured.out


class TestDoctorReport:
    def test_failure_count(self) -> None:
        report = DoctorReport()
        report.add(CheckResult(name="a", status="pass"))
        report.add(CheckResult(name="b", status="fail"))
        report.add(CheckResult(name="c", status="fail"))
        report.add(CheckResult(name="d", status="warn"))
        report.add(CheckResult(name="e", status="skip"))
        assert report.failure_count == 2
        assert report.all_passed is False

    def test_all_passed_with_warns_and_skips(self) -> None:
        report = DoctorReport()
        report.add(CheckResult(name="a", status="pass"))
        report.add(CheckResult(name="b", status="warn"))
        report.add(CheckResult(name="c", status="skip"))
        assert report.all_passed is True
        assert report.failure_count == 0
