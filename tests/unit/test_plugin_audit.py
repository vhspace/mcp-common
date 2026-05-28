"""Tests for ``mcp_common.plugin_audit`` — mcp-common feature-adoption scan.

Focus: issue #99 — the agent-remediation-handler recommendation must be
satisfied by EITHER ``install_cli_exception_handler`` directly OR by the CLI
scaffolding that wires it transparently (``create_cli_app`` /
``build_cli_from_mcp``), so the audit stops false-positiving on MCPs that
correctly migrated to the dual-mode framework.
"""

from __future__ import annotations

from pathlib import Path

from mcp_common.plugin_audit import audit_repo, collect_mcp_common_imports

# The feature *name* in AUDIT_FEATURES (distinct from the import symbols).
_REMEDIATION_FEATURE = "install_cli_exception_handler"


def _write_repo(root: Path, source: str) -> Path:
    """Materialize a minimal ``<root>/src/my_mcp/server.py`` with ``source``."""
    src = root / "src" / "my_mcp"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("")
    (src / "server.py").write_text(source)
    return root


def _found(root: Path) -> set[str]:
    return set(audit_repo(root).features_found)


def _missing_recommended(root: Path) -> set[str]:
    return {f.name for f in audit_repo(root).features_missing_recommended}


class TestRemediationHandlerAlias:
    def test_create_cli_app_satisfies_check(self, tmp_path: Path) -> None:
        _write_repo(tmp_path, "from mcp_common.cli import create_cli_app, run_cli\n")
        assert _REMEDIATION_FEATURE in _found(tmp_path)
        assert _REMEDIATION_FEATURE not in _missing_recommended(tmp_path)

    def test_build_cli_from_mcp_satisfies_check(self, tmp_path: Path) -> None:
        _write_repo(
            tmp_path,
            "from mcp_common.dual_mode import build_cli_from_mcp, dual_mode_tool\n",
        )
        assert _REMEDIATION_FEATURE in _found(tmp_path)
        assert _REMEDIATION_FEATURE not in _missing_recommended(tmp_path)

    def test_direct_handler_still_satisfies_check(self, tmp_path: Path) -> None:
        """Regression: the original direct import must keep passing."""
        _write_repo(
            tmp_path,
            "from mcp_common.agent_remediation import install_cli_exception_handler\n",
        )
        assert _REMEDIATION_FEATURE in _found(tmp_path)
        assert _REMEDIATION_FEATURE not in _missing_recommended(tmp_path)

    def test_none_of_them_flags_missing(self, tmp_path: Path) -> None:
        """Regression: an MCP using none of the symbols is still flagged."""
        _write_repo(tmp_path, "from mcp_common.env import load_env\n")
        assert _REMEDIATION_FEATURE not in _found(tmp_path)
        assert _REMEDIATION_FEATURE in _missing_recommended(tmp_path)

    def test_collect_imports_sees_cli_scaffolding(self, tmp_path: Path) -> None:
        _write_repo(
            tmp_path,
            "from mcp_common.cli import create_cli_app\n"
            "from mcp_common.dual_mode import build_cli_from_mcp\n",
        )
        names = collect_mcp_common_imports(tmp_path / "src")
        assert {"create_cli_app", "build_cli_from_mcp"} <= names
