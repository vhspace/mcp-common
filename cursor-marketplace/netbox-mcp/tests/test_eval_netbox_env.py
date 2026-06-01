"""Unit tests for the eval-harness NetBox credential plumbing (netbox-mcp#117).

These cover the parent-side token resolution + fail-fast preflight that keep the
spawned netbox-mcp child (and the bash/cli paths) from silently failing
credential_chain[netbox] and depressing every model's accuracy.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from evals import _netbox_env as env_mod
from evals._netbox_env import (
    EvalBinaryVersionError,
    NetboxPreflightError,
    apply_resolved_token_to_environ,
    netbox_cli_path,
    netbox_mcp_command,
    netbox_mcp_env,
    preflight_eval_binaries,
    preflight_netbox,
    prepend_repo_bin_to_path,
    repo_bin_dir,
    repo_version,
    resolve_netbox_token,
)

OPREF = "op://Employee/Together - Netbox/NETBOX_TOKEN"


@pytest.fixture(autouse=True)
def _reset_token_cache() -> None:
    """Clear the process-level resolved-token cache between tests."""
    env_mod._LAST_RESOLVED_TOKEN = None
    yield
    env_mod._LAST_RESOLVED_TOKEN = None


def test_resolve_plain_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETBOX_TOKEN", "plain-abc123")
    assert resolve_netbox_token() == "plain-abc123"


def test_resolve_empty_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NETBOX_TOKEN", raising=False)
    with pytest.raises(NetboxPreflightError, match="empty or unset"):
        resolve_netbox_token(required=True)


def test_resolve_empty_token_optional_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NETBOX_TOKEN", raising=False)
    assert resolve_netbox_token(required=False) is None


def test_resolve_opref_via_parent_op(monkeypatch: pytest.MonkeyPatch) -> None:
    """An op:// reference is resolved to a plain token *in the parent*."""
    monkeypatch.setenv("NETBOX_TOKEN", OPREF)
    # Simulate the parent having working op/1Password access.
    monkeypatch.setattr(
        "mcp_common.credential_chain._read_op_reference",
        lambda ref, timeout_s=30: "resolved-plain-token",
    )
    assert resolve_netbox_token() == "resolved-plain-token"


def test_resolve_opref_unresolvable_raises_with_op_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NETBOX_TOKEN", OPREF)
    monkeypatch.setattr(
        "mcp_common.credential_chain._read_op_reference",
        lambda ref, timeout_s=30: None,
    )
    with pytest.raises(NetboxPreflightError, match="op:// reference"):
        resolve_netbox_token(required=True)


def test_resolve_falls_back_to_cached_token_when_env_clobbered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A good token survives inspect-ai's override=True .env reload mid-eval.

    Models the real failure: the preflight resolves a usable token, then
    inspect reloads .env (override=True under VSCode/Cursor) and clobbers
    NETBOX_TOKEN with an op:// ref the child can't resolve. The cached plain
    token must still be returned.
    """
    monkeypatch.setenv("NETBOX_TOKEN", "good-plain-token")
    assert resolve_netbox_token() == "good-plain-token"  # preflight-style resolve

    # inspect clobbers the env with an unresolvable op:// ref.
    monkeypatch.setenv("NETBOX_TOKEN", OPREF)
    monkeypatch.setattr(
        "mcp_common.credential_chain._read_op_reference",
        lambda ref, timeout_s=30: None,
    )
    assert resolve_netbox_token(required=False) == "good-plain-token"
    assert netbox_mcp_env()["NETBOX_TOKEN"] == "good-plain-token"


def test_netbox_mcp_env_forwards_plain_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """The env dict handed to the spawned child carries a PLAIN token, not op://."""
    monkeypatch.setenv("NETBOX_URL", "https://netbox.test")
    monkeypatch.setenv("NETBOX_TOKEN", OPREF)
    monkeypatch.setattr(
        "mcp_common.credential_chain._read_op_reference",
        lambda ref, timeout_s=30: "child-safe-token",
    )
    child_env = netbox_mcp_env()
    assert child_env == {
        "NETBOX_URL": "https://netbox.test",
        "NETBOX_TOKEN": "child-safe-token",
    }
    assert not child_env["NETBOX_TOKEN"].startswith("op://")


def test_netbox_mcp_env_unresolved_opref_forwarded_as_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Best-effort: unresolved op:// is forwarded as-is (preflight is the loud gate)."""
    monkeypatch.setenv("NETBOX_URL", "https://netbox.test")
    monkeypatch.setenv("NETBOX_TOKEN", OPREF)
    monkeypatch.setattr(
        "mcp_common.credential_chain._read_op_reference",
        lambda ref, timeout_s=30: None,
    )
    assert netbox_mcp_env()["NETBOX_TOKEN"] == OPREF


def test_apply_resolved_token_to_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    """The resolved plain token is written back to os.environ for the bash/cli paths."""
    import os

    monkeypatch.setenv("NETBOX_TOKEN", OPREF)
    monkeypatch.setattr(
        "mcp_common.credential_chain._read_op_reference",
        lambda ref, timeout_s=30: "exported-token",
    )
    assert apply_resolved_token_to_environ() == "exported-token"
    assert os.environ["NETBOX_TOKEN"] == "exported-token"


def test_preflight_missing_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NETBOX_URL", raising=False)
    monkeypatch.setenv("NETBOX_TOKEN", "plain")
    with pytest.raises(NetboxPreflightError, match="NETBOX_URL"):
        preflight_netbox()


def test_preflight_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: token resolves and the cheap GET /api/status/ succeeds."""
    monkeypatch.setenv("NETBOX_URL", "https://netbox.test")
    monkeypatch.setenv("NETBOX_TOKEN", "plain-token")

    calls: list[str] = []

    class _FakeClient:
        def __init__(self, url: str, token: str, verify_ssl: bool = True) -> None:
            self.url = url

        def get(self, endpoint: str):
            calls.append(endpoint)
            return {"netbox-version": "x"}

    monkeypatch.setattr("netbox_mcp.netbox_client.NetBoxRestClient", _FakeClient)
    info = preflight_netbox()
    assert info["status"] == "ok"
    assert info["token_source"] == "plain"
    assert calls == ["status"]


def test_preflight_opref_records_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETBOX_URL", "https://netbox.test")
    monkeypatch.setenv("NETBOX_TOKEN", OPREF)
    monkeypatch.setattr(
        "mcp_common.credential_chain._read_op_reference",
        lambda ref, timeout_s=30: "plain-from-op",
    )

    class _FakeClient:
        def __init__(self, url: str, token: str, verify_ssl: bool = True) -> None:
            assert token == "plain-from-op"  # child gets the resolved plain token

        def get(self, endpoint: str):
            return {}

    monkeypatch.setattr("netbox_mcp.netbox_client.NetBoxRestClient", _FakeClient)
    info = preflight_netbox()
    assert info["token_source"].startswith("op://")


def test_preflight_connectivity_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETBOX_URL", "https://netbox.test")
    monkeypatch.setenv("NETBOX_TOKEN", "plain-token")

    class _FakeClient:
        def __init__(self, *a, **k) -> None:
            pass

        def get(self, endpoint: str):
            raise ConnectionError("boom")

    monkeypatch.setattr("netbox_mcp.netbox_client.NetBoxRestClient", _FakeClient)
    with pytest.raises(NetboxPreflightError, match=r"GET .+/api/status/\) failed"):
        preflight_netbox()


def test_module_exposes_public_helpers() -> None:
    for name in (
        "resolve_netbox_token",
        "netbox_mcp_env",
        "apply_resolved_token_to_environ",
        "preflight_netbox",
        "NetboxPreflightError",
        "preflight_eval_binaries",
        "EvalBinaryVersionError",
        "repo_bin_dir",
        "netbox_cli_path",
        "netbox_mcp_command",
        "prepend_repo_bin_to_path",
        "repo_version",
    ):
        assert hasattr(env_mod, name)


# ---------------------------------------------------------------------------
# Binary / version preflight (netbox-mcp#137 stale-global root cause)
# ---------------------------------------------------------------------------


def test_repo_bin_dir_and_command_paths_agree() -> None:
    """The cli/mcp paths live in the eval interpreter's bin dir (the repo venv)."""
    bin_dir = repo_bin_dir()
    assert netbox_cli_path() == bin_dir / "netbox-cli"
    assert netbox_mcp_command() == str(bin_dir / "netbox-mcp")


def test_repo_version_reads_pyproject() -> None:
    """The expected version is the working-tree pyproject [project].version."""
    version = repo_version()
    assert isinstance(version, str)
    assert version[0].isdigit() and "." in version


def test_prepend_repo_bin_to_path_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """The repo venv bin is placed FIRST on PATH and dedup'd on repeat calls."""
    fake_bin = "/fake/repo/.venv/bin"
    monkeypatch.setattr(env_mod, "repo_bin_dir", lambda: Path(fake_bin))
    monkeypatch.setenv("PATH", os.pathsep.join(["/usr/local/bin", "/usr/bin"]))

    assert prepend_repo_bin_to_path() == fake_bin
    assert os.environ["PATH"].split(os.pathsep)[0] == fake_bin

    # Idempotent: a second call does not duplicate the entry.
    prepend_repo_bin_to_path()
    assert os.environ["PATH"].split(os.pathsep).count(fake_bin) == 1


def _stub_repo_bin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Make ``repo_bin_dir`` point at *tmp_path* with both console scripts present."""
    bin_dir = tmp_path.resolve()
    (bin_dir / "netbox-cli").write_text("#!stub\n", encoding="utf-8")
    (bin_dir / "netbox-mcp").write_text("#!stub\n", encoding="utf-8")
    monkeypatch.setattr(env_mod, "repo_bin_dir", lambda: bin_dir)
    return bin_dir


def test_preflight_eval_binaries_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Happy path: cli/mcp exist in the repo bin and both report the repo version."""
    bin_dir = _stub_repo_bin(monkeypatch, tmp_path)
    monkeypatch.setenv("PATH", "/usr/local/bin")
    monkeypatch.setattr(env_mod, "repo_version", lambda: "9.9.9")
    monkeypatch.setattr(env_mod, "installed_version", lambda: "9.9.9")
    monkeypatch.setattr(env_mod, "_cli_reported_version", lambda p: "9.9.9")
    monkeypatch.setattr(env_mod.shutil, "which", lambda name: str(bin_dir / "netbox-cli"))

    info = preflight_eval_binaries()
    assert info["status"] == "ok"
    assert info["expected_version"] == "9.9.9"
    assert info["netbox_cli_version"] == "9.9.9"
    assert info["netbox_mcp_version"] == "9.9.9"
    # PATH was prepended with the repo bin (the #137 anti-stale fix).
    assert os.environ["PATH"].split(os.pathsep)[0] == str(bin_dir)


def test_preflight_eval_binaries_stale_global_path_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A `netbox-cli` resolving OUTSIDE the repo bin is the #137 failure → abort."""
    _stub_repo_bin(monkeypatch, tmp_path)
    monkeypatch.setattr(env_mod, "repo_version", lambda: "9.9.9")
    monkeypatch.setattr(env_mod, "installed_version", lambda: "9.9.9")
    monkeypatch.setattr(env_mod, "_cli_reported_version", lambda p: "9.9.9")
    # PATH resolves a stale global binary, not the repo bin.
    monkeypatch.setattr(env_mod.shutil, "which", lambda name: "/usr/local/bin/netbox-cli")

    with pytest.raises(EvalBinaryVersionError, match="stale/global"):
        preflight_eval_binaries()


def test_preflight_eval_binaries_version_mismatch_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A resolved CLI version != working-tree build aborts (would test wrong code)."""
    bin_dir = _stub_repo_bin(monkeypatch, tmp_path)
    monkeypatch.setattr(env_mod, "repo_version", lambda: "9.9.9")
    monkeypatch.setattr(env_mod, "installed_version", lambda: "9.9.9")
    monkeypatch.setattr(env_mod, "_cli_reported_version", lambda p: "2.14.1")  # stale
    monkeypatch.setattr(env_mod.shutil, "which", lambda name: str(bin_dir / "netbox-cli"))

    with pytest.raises(EvalBinaryVersionError, match="version mismatch"):
        preflight_eval_binaries()


def test_preflight_eval_binaries_missing_binary_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If the repo console scripts aren't in the eval venv bin, abort clearly."""
    empty = tmp_path.resolve()  # no netbox-cli / netbox-mcp created
    monkeypatch.setattr(env_mod, "repo_bin_dir", lambda: empty)
    monkeypatch.setattr(env_mod, "repo_version", lambda: "9.9.9")

    with pytest.raises(EvalBinaryVersionError, match="not found"):
        preflight_eval_binaries()
