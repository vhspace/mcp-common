"""Tests for CLI commands using typer's CliRunner."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from ipa_mcp.cli import app

runner = CliRunner()


def _mock_client_ctx():
    """Return a patch context that replaces _client() with a mock IPAClient."""
    return patch("ipa_mcp.cli._client")


class TestHostgroupDiffYesFlag:
    def test_apply_yes_skips_confirm(self) -> None:
        with _mock_client_ctx() as mock_factory:
            mock_client = MagicMock()
            mock_factory.return_value = mock_client
            mock_client.__enter__ = lambda s: s
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.hostgroup_show.return_value = {
                "result": {
                    "cn": ["hg_test"],
                    "member_host": ["node1.example.com"],
                },
            }

            result = runner.invoke(
                app,
                [
                    "hostgroup-diff",
                    "hg_test",
                    "-e",
                    "node1.example.com,node2.example.com",
                    "--apply",
                    "--yes",
                ],
            )

            assert result.exit_code == 0
            mock_client.hostgroup_add_member.assert_called_once_with(
                "hg_test", host=["node2.example.com"]
            )

    def test_apply_without_yes_prompts(self) -> None:
        with _mock_client_ctx() as mock_factory:
            mock_client = MagicMock()
            mock_factory.return_value = mock_client
            mock_client.__enter__ = lambda s: s
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.hostgroup_show.return_value = {
                "result": {
                    "cn": ["hg_test"],
                    "member_host": ["node1.example.com"],
                },
            }

            result = runner.invoke(
                app,
                [
                    "hostgroup-diff",
                    "hg_test",
                    "-e",
                    "node1.example.com,node2.example.com",
                    "--apply",
                ],
                input="n\n",
            )

            assert result.exit_code != 0
            mock_client.hostgroup_add_member.assert_not_called()

    def test_yes_without_apply_is_dry_run(self) -> None:
        with _mock_client_ctx() as mock_factory:
            mock_client = MagicMock()
            mock_factory.return_value = mock_client
            mock_client.__enter__ = lambda s: s
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.hostgroup_show.return_value = {
                "result": {
                    "cn": ["hg_test"],
                    "member_host": ["node1.example.com"],
                },
            }

            result = runner.invoke(
                app,
                [
                    "hostgroup-diff",
                    "hg_test",
                    "-e",
                    "node1.example.com,node2.example.com",
                    "--yes",
                ],
            )

            assert result.exit_code == 0
            mock_client.hostgroup_add_member.assert_not_called()
            mock_client.hostgroup_remove_member.assert_not_called()

    def test_apply_yes_short_flag(self) -> None:
        with _mock_client_ctx() as mock_factory:
            mock_client = MagicMock()
            mock_factory.return_value = mock_client
            mock_client.__enter__ = lambda s: s
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.hostgroup_show.return_value = {
                "result": {
                    "cn": ["hg_test"],
                    "member_host": ["node1.example.com"],
                },
            }

            result = runner.invoke(
                app,
                [
                    "hostgroup-diff",
                    "hg_test",
                    "-e",
                    "node1.example.com,node2.example.com",
                    "--apply",
                    "-y",
                ],
            )

            assert result.exit_code == 0
            mock_client.hostgroup_add_member.assert_called_once()


class TestHostgroupAddHosts:
    def test_adds_hosts(self) -> None:
        with _mock_client_ctx() as mock_factory:
            mock_client = MagicMock()
            mock_factory.return_value = mock_client
            mock_client.__enter__ = lambda s: s
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.hostgroup_add_member.return_value = {
                "result": {"cn": ["hg_test"], "member_host": ["h1.example.com", "h2.example.com"]},
            }

            result = runner.invoke(
                app,
                ["hostgroup-add-hosts", "hg_test", "h1.example.com,h2.example.com"],
            )

            assert result.exit_code == 0
            mock_client.hostgroup_add_member.assert_called_once_with(
                "hg_test", host=["h1.example.com", "h2.example.com"]
            )

    def test_empty_hosts_errors(self) -> None:
        result = runner.invoke(app, ["hostgroup-add-hosts", "hg_test", ""])
        assert result.exit_code != 0

    def test_json_output(self) -> None:
        with _mock_client_ctx() as mock_factory:
            mock_client = MagicMock()
            mock_factory.return_value = mock_client
            mock_client.__enter__ = lambda s: s
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.hostgroup_add_member.return_value = {
                "result": {"cn": ["hg_test"]},
            }

            result = runner.invoke(
                app,
                ["hostgroup-add-hosts", "hg_test", "h1.example.com", "--json"],
            )

            assert result.exit_code == 0
            assert '"cn"' in result.output


class TestHostgroupRemoveHosts:
    def test_removes_hosts(self) -> None:
        with _mock_client_ctx() as mock_factory:
            mock_client = MagicMock()
            mock_factory.return_value = mock_client
            mock_client.__enter__ = lambda s: s
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.hostgroup_remove_member.return_value = {
                "result": {"cn": ["hg_test"]},
            }

            result = runner.invoke(
                app,
                ["hostgroup-remove-hosts", "hg_test", "h1.example.com"],
            )

            assert result.exit_code == 0
            mock_client.hostgroup_remove_member.assert_called_once_with(
                "hg_test", host=["h1.example.com"]
            )

    def test_empty_hosts_errors(self) -> None:
        result = runner.invoke(app, ["hostgroup-remove-hosts", "hg_test", ""])
        assert result.exit_code != 0

    def test_multiple_hosts(self) -> None:
        with _mock_client_ctx() as mock_factory:
            mock_client = MagicMock()
            mock_factory.return_value = mock_client
            mock_client.__enter__ = lambda s: s
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.hostgroup_remove_member.return_value = {
                "result": {"cn": ["hg_test"]},
            }

            result = runner.invoke(
                app,
                ["hostgroup-remove-hosts", "hg_test", "h1.example.com,h2.example.com"],
            )

            assert result.exit_code == 0
            mock_client.hostgroup_remove_member.assert_called_once_with(
                "hg_test", host=["h1.example.com", "h2.example.com"]
            )
