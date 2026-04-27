"""Tests for eval repo discovery — dynamic MCP plugin lookup."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from mcp_common.testing.eval.repo_discovery import (
    RepoInfo,
    _extract_github_repo,
    discover_repos,
    resolve_server_to_repo,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_loaded_config(name: str, repository: str):
    """Return an object that quacks like LoadedPluginConfig for the fields we use."""

    class _Cfg:
        def __init__(self, n: str, r: str) -> None:
            self.name = n
            self.repository = r

    return _Cfg(name, repository)


# ---------------------------------------------------------------------------
# _extract_github_repo
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestExtractGithubRepo:
    def test_https_url(self) -> None:
        assert _extract_github_repo("https://github.com/vhspace/netbox-mcp") == "vhspace/netbox-mcp"

    def test_https_url_with_git_suffix(self) -> None:
        assert _extract_github_repo("https://github.com/vhspace/ufm-mcp.git") == "vhspace/ufm-mcp"

    def test_ssh_style_url(self) -> None:
        assert _extract_github_repo("git@github.com/org/repo") == "org/repo"

    def test_non_github_url_returned_verbatim(self) -> None:
        url = "https://gitlab.com/foo/bar"
        assert _extract_github_repo(url) == url

    def test_trailing_slash_absent(self) -> None:
        assert _extract_github_repo("https://github.com/a/b") == "a/b"


# ---------------------------------------------------------------------------
# discover_repos
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestDiscoverRepos:
    @patch("mcp_common.marketplace_builder.discover_plugins")
    def test_basic_discovery(self, mock_discover, tmp_path: Path) -> None:
        repo_a = tmp_path / "netbox-mcp"
        repo_a.mkdir()
        cfg_a = _fake_loaded_config("netbox-mcp", "https://github.com/vhspace/netbox-mcp")
        mock_discover.return_value = [(repo_a, cfg_a)]

        repos = discover_repos(tmp_path)
        assert "netbox-mcp" in repos
        assert repos["netbox-mcp"].github_repo == "vhspace/netbox-mcp"
        assert repos["netbox-mcp"].local_path == repo_a

    @patch("mcp_common.marketplace_builder.discover_plugins")
    def test_filters_hidden_directories(self, mock_discover, tmp_path: Path) -> None:
        hidden = tmp_path / ".worktrees" / "netbox-mcp"
        hidden.mkdir(parents=True)
        visible = tmp_path / "ufm-mcp"
        visible.mkdir()

        cfg_hidden = _fake_loaded_config("netbox-mcp", "https://github.com/vhspace/netbox-mcp")
        cfg_visible = _fake_loaded_config("ufm-mcp", "https://github.com/vhspace/ufm-mcp")
        mock_discover.return_value = [(hidden, cfg_hidden), (visible, cfg_visible)]

        repos = discover_repos(tmp_path)
        assert "netbox-mcp" not in repos
        assert "ufm-mcp" in repos

    @patch("mcp_common.marketplace_builder.discover_plugins")
    def test_multiple_repos(self, mock_discover, tmp_path: Path) -> None:
        entries = []
        for name in ["netbox-mcp", "ufm-mcp", "maas-mcp"]:
            p = tmp_path / name
            p.mkdir()
            cfg = _fake_loaded_config(name, f"https://github.com/vhspace/{name}")
            entries.append((p, cfg))
        mock_discover.return_value = entries

        repos = discover_repos(tmp_path)
        assert len(repos) == 3

    @patch("mcp_common.marketplace_builder.discover_plugins", side_effect=FileNotFoundError)
    def test_missing_workspace_returns_empty(self, _mock) -> None:
        repos = discover_repos(Path("/nonexistent"))
        assert repos == {}

    @patch("mcp_common.marketplace_builder.discover_plugins", return_value=[])
    def test_empty_workspace_returns_empty(self, _mock, tmp_path: Path) -> None:
        repos = discover_repos(tmp_path)
        assert repos == {}


# ---------------------------------------------------------------------------
# resolve_server_to_repo
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestResolveServerToRepo:
    @patch("mcp_common.marketplace_builder.discover_plugins")
    def test_exact_match(self, mock_discover, tmp_path: Path) -> None:
        repo = tmp_path / "netbox-mcp"
        repo.mkdir()
        cfg = _fake_loaded_config("netbox-mcp", "https://github.com/vhspace/netbox-mcp")
        mock_discover.return_value = [(repo, cfg)]

        result = resolve_server_to_repo("netbox-mcp", tmp_path)
        assert result is not None
        assert result.name == "netbox-mcp"

    @patch("mcp_common.marketplace_builder.discover_plugins")
    def test_underscore_normalization(self, mock_discover, tmp_path: Path) -> None:
        repo = tmp_path / "netbox-mcp"
        repo.mkdir()
        cfg = _fake_loaded_config("netbox-mcp", "https://github.com/vhspace/netbox-mcp")
        mock_discover.return_value = [(repo, cfg)]

        result = resolve_server_to_repo("netbox_mcp", tmp_path)
        assert result is not None
        assert result.name == "netbox-mcp"

    @patch("mcp_common.marketplace_builder.discover_plugins")
    def test_prefix_match(self, mock_discover, tmp_path: Path) -> None:
        repo = tmp_path / "netbox-mcp"
        repo.mkdir()
        cfg = _fake_loaded_config("netbox-mcp", "https://github.com/vhspace/netbox-mcp")
        mock_discover.return_value = [(repo, cfg)]

        result = resolve_server_to_repo("netbox", tmp_path)
        assert result is not None
        assert result.name == "netbox-mcp"

    @patch("mcp_common.marketplace_builder.discover_plugins")
    def test_no_match_returns_none(self, mock_discover, tmp_path: Path) -> None:
        repo = tmp_path / "netbox-mcp"
        repo.mkdir()
        cfg = _fake_loaded_config("netbox-mcp", "https://github.com/vhspace/netbox-mcp")
        mock_discover.return_value = [(repo, cfg)]

        result = resolve_server_to_repo("unknown-server", tmp_path)
        assert result is None

    @patch("mcp_common.marketplace_builder.discover_plugins")
    def test_cache_avoids_rediscovery(self, mock_discover, tmp_path: Path) -> None:
        repo = tmp_path / "netbox-mcp"
        repo.mkdir()
        cfg = _fake_loaded_config("netbox-mcp", "https://github.com/vhspace/netbox-mcp")
        mock_discover.return_value = [(repo, cfg)]

        cache: dict[Path, dict[str, RepoInfo]] = {}
        resolve_server_to_repo("netbox-mcp", tmp_path, _cache=cache)
        resolve_server_to_repo("netbox-mcp", tmp_path, _cache=cache)

        mock_discover.assert_called_once()

    @patch("mcp_common.marketplace_builder.discover_plugins")
    def test_case_insensitive(self, mock_discover, tmp_path: Path) -> None:
        repo = tmp_path / "netbox-mcp"
        repo.mkdir()
        cfg = _fake_loaded_config("netbox-mcp", "https://github.com/vhspace/netbox-mcp")
        mock_discover.return_value = [(repo, cfg)]

        result = resolve_server_to_repo("Netbox_MCP", tmp_path)
        assert result is not None
        assert result.name == "netbox-mcp"


# ---------------------------------------------------------------------------
# RepoInfo dataclass
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestRepoInfo:
    def test_frozen(self) -> None:
        info = RepoInfo(
            name="test",
            github_url="https://github.com/org/test",
            github_repo="org/test",
            local_path=Path("/tmp/test"),
        )
        with pytest.raises(AttributeError):
            info.name = "changed"  # type: ignore[misc]

    def test_fields(self) -> None:
        info = RepoInfo(
            name="netbox-mcp",
            github_url="https://github.com/vhspace/netbox-mcp",
            github_repo="vhspace/netbox-mcp",
            local_path=Path("/workspaces/together/netbox-mcp"),
        )
        assert info.name == "netbox-mcp"
        assert info.github_url == "https://github.com/vhspace/netbox-mcp"
        assert info.github_repo == "vhspace/netbox-mcp"
        assert info.local_path == Path("/workspaces/together/netbox-mcp")
