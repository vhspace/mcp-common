"""Tests for plugin_cli helpers."""

from mcp_common.plugin_cli import _referenced_env_vars


def test_referenced_env_vars_extracts_curly_refs_only() -> None:
    refs = _referenced_env_vars(
        {
            "A": "${TOKEN_A}",
            "B": "${TOKEN_B}",
            "C": "literal",
            "D": " ${TOKEN_A} ",
        }
    )
    assert refs == ["TOKEN_A", "TOKEN_B"]
