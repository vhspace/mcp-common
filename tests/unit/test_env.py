"""Tests for mcp_common.env — standardized .env loading."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mcp_common.env import _find_env_files, load_env, reset_env_state


@pytest.fixture(autouse=True)
def _reset_env():
    """Ensure load_env guard is reset between tests."""
    reset_env_state()
    yield
    reset_env_state()


@pytest.fixture
def env_dir(tmp_path: Path) -> Path:
    """Create a temp directory simulating a repo with .env."""
    return tmp_path


class TestFindEnvFiles:
    def test_no_env_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        assert _find_env_files() == []

    def test_cwd_env_file(self, env_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env_file = env_dir / ".env"
        env_file.write_text("FOO=bar\n")
        monkeypatch.chdir(env_dir)
        found = _find_env_files()
        assert len(found) == 1
        assert found[0] == env_file

    def test_parent_env_file(self, env_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        parent_env = env_dir / ".env"
        parent_env.write_text("FOO=bar\n")
        child = env_dir / "subdir"
        child.mkdir()
        monkeypatch.chdir(child)
        found = _find_env_files()
        assert len(found) == 1
        assert found[0] == parent_env

    def test_both_env_files(self, env_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Parent loaded first (lower priority), child loaded second (wins)."""
        parent_env = env_dir / ".env"
        parent_env.write_text("PARENT=yes\n")
        child = env_dir / "subdir"
        child.mkdir()
        child_env = child / ".env"
        child_env.write_text("CHILD=yes\n")
        monkeypatch.chdir(child)
        found = _find_env_files()
        assert len(found) == 2
        assert found[0] == parent_env
        assert found[1] == child_env

    def test_explicit_search_paths(self, env_dir: Path) -> None:
        existing = env_dir / "custom.env"
        existing.write_text("X=1\n")
        missing = env_dir / "nope.env"
        found = _find_env_files([existing, missing])
        assert found == [existing]

    def test_search_from_overrides_cwd(
        self, env_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """search_from uses a different base than cwd."""
        repo = env_dir / "myrepo"
        repo.mkdir()
        repo_env = repo / ".env"
        repo_env.write_text("REPO=yes\n")
        monkeypatch.chdir(env_dir)  # cwd is NOT myrepo
        found = _find_env_files(search_from=repo)
        assert repo_env in found

    def test_custom_env_file_name(self, env_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        custom = env_dir / ".env.local"
        custom.write_text("LOCAL=yes\n")
        monkeypatch.chdir(env_dir)
        found = _find_env_files(env_file=".env.local")
        assert len(found) == 1
        assert found[0] == custom


class TestLoadEnv:
    def test_loads_env_file(
        self, env_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = env_dir / ".env"
        env_file.write_text("TEST_LOAD_ENV_VAR=hello_world\n")
        monkeypatch.chdir(env_dir)
        monkeypatch.delenv("TEST_LOAD_ENV_VAR", raising=False)

        loaded = load_env()

        assert len(loaded) == 1
        assert os.environ.get("TEST_LOAD_ENV_VAR") == "hello_world"

        monkeypatch.delenv("TEST_LOAD_ENV_VAR", raising=False)

    def test_default_override_is_false(
        self, env_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Default override=False means existing env vars are preserved."""
        env_file = env_dir / ".env"
        env_file.write_text("TEST_DEFAULT_OVERRIDE=from_dotenv\n")
        monkeypatch.chdir(env_dir)
        monkeypatch.setenv("TEST_DEFAULT_OVERRIDE", "from_shell")

        load_env()  # default override=False

        assert os.environ.get("TEST_DEFAULT_OVERRIDE") == "from_shell"

        monkeypatch.delenv("TEST_DEFAULT_OVERRIDE", raising=False)

    def test_override_true_replaces_existing(
        self, env_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = env_dir / ".env"
        env_file.write_text("TEST_OVERRIDE_VAR=from_dotenv\n")
        monkeypatch.chdir(env_dir)
        monkeypatch.setenv("TEST_OVERRIDE_VAR", "from_shell")

        load_env(override=True)

        assert os.environ.get("TEST_OVERRIDE_VAR") == "from_dotenv"

        monkeypatch.delenv("TEST_OVERRIDE_VAR", raising=False)

    def test_override_false_preserves_existing(
        self, env_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = env_dir / ".env"
        env_file.write_text("TEST_NOOVERRIDE_VAR=from_dotenv\n")
        monkeypatch.chdir(env_dir)
        monkeypatch.setenv("TEST_NOOVERRIDE_VAR", "from_shell")

        load_env(override=False)

        assert os.environ.get("TEST_NOOVERRIDE_VAR") == "from_shell"

        monkeypatch.delenv("TEST_NOOVERRIDE_VAR", raising=False)

    def test_idempotent_by_default(
        self, env_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = env_dir / ".env"
        env_file.write_text("TEST_IDEM_VAR=first\n")
        monkeypatch.chdir(env_dir)
        monkeypatch.delenv("TEST_IDEM_VAR", raising=False)

        first = load_env()
        assert len(first) == 1

        env_file.write_text("TEST_IDEM_VAR=second\n")
        second = load_env()
        assert second == []
        assert os.environ.get("TEST_IDEM_VAR") == "first"

        monkeypatch.delenv("TEST_IDEM_VAR", raising=False)

    def test_force_reloads(
        self, env_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = env_dir / ".env"
        env_file.write_text("TEST_FORCE_VAR=first\n")
        monkeypatch.chdir(env_dir)
        monkeypatch.delenv("TEST_FORCE_VAR", raising=False)

        load_env(override=True)
        assert os.environ.get("TEST_FORCE_VAR") == "first"

        env_file.write_text("TEST_FORCE_VAR=second\n")
        load_env(_force=True, override=True)
        assert os.environ.get("TEST_FORCE_VAR") == "second"

        monkeypatch.delenv("TEST_FORCE_VAR", raising=False)

    def test_child_overrides_parent_with_override_true(
        self, env_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With override=True, later files win. Child is loaded after parent."""
        parent_env = env_dir / ".env"
        parent_env.write_text("TEST_PRECEDENCE=parent\n")
        child = env_dir / "repo"
        child.mkdir()
        child_env = child / ".env"
        child_env.write_text("TEST_PRECEDENCE=child\n")
        monkeypatch.chdir(child)
        monkeypatch.delenv("TEST_PRECEDENCE", raising=False)

        load_env(override=True)

        # Child (.env in repo dir) is loaded second and wins
        assert os.environ.get("TEST_PRECEDENCE") == "child"

        monkeypatch.delenv("TEST_PRECEDENCE", raising=False)

    def test_no_env_files_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        loaded = load_env()
        assert loaded == []

    def test_explicit_search_paths(
        self, env_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        custom = env_dir / "my.env"
        custom.write_text("TEST_CUSTOM_PATH=works\n")
        monkeypatch.delenv("TEST_CUSTOM_PATH", raising=False)

        loaded = load_env(search_paths=[custom])
        assert len(loaded) == 1
        assert os.environ.get("TEST_CUSTOM_PATH") == "works"

        monkeypatch.delenv("TEST_CUSTOM_PATH", raising=False)

    def test_search_from_parameter(
        self, env_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """search_from allows file-relative .env lookup independent of cwd."""
        repo = env_dir / "myrepo"
        repo.mkdir()
        repo_env = repo / ".env"
        repo_env.write_text("TEST_SEARCH_FROM=found\n")
        monkeypatch.chdir(env_dir / "..")  # cwd far from repo
        monkeypatch.delenv("TEST_SEARCH_FROM", raising=False)

        loaded = load_env(search_from=repo)

        assert repo_env in loaded
        assert os.environ.get("TEST_SEARCH_FROM") == "found"

        monkeypatch.delenv("TEST_SEARCH_FROM", raising=False)

    def test_search_from_accepts_string(
        self, env_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = env_dir / "strrepo"
        repo.mkdir()
        repo_env = repo / ".env"
        repo_env.write_text("TEST_STR_FROM=yes\n")
        monkeypatch.delenv("TEST_STR_FROM", raising=False)

        loaded = load_env(search_from=str(repo))

        assert repo_env in loaded
        assert os.environ.get("TEST_STR_FROM") == "yes"

        monkeypatch.delenv("TEST_STR_FROM", raising=False)

    def test_env_file_parameter(
        self, env_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        custom = env_dir / ".env.local"
        custom.write_text("TEST_ENV_FILE=custom_name\n")
        monkeypatch.chdir(env_dir)
        monkeypatch.delenv("TEST_ENV_FILE", raising=False)

        loaded = load_env(env_file=".env.local")

        assert len(loaded) == 1
        assert os.environ.get("TEST_ENV_FILE") == "custom_name"

        monkeypatch.delenv("TEST_ENV_FILE", raising=False)


class TestImport:
    def test_importable_from_package(self) -> None:
        from mcp_common import load_env as imported

        assert callable(imported)
