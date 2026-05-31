---
name: eval-version-validation
description: Validate that an MCP eval exercises the repo's CURRENT server and CLI version, not a stale globally-installed binary. Use when running or building MCP eval suites (inspect-ai matrix, run_matrix.py), spawning an MCP server or invoking a CLI binary in an eval, or debugging eval results that look wrong/regressed.
---

# Eval version validation

Evals must exercise the **code under test**, not a stale install. A globally-installed MCP/CLI (`uv tool install`, a binary on `PATH`) can silently shadow the repo's current code, so the eval grades OLD behavior. This has masked real changes (e.g. a CLI subcommand rename evaluated against a pre-rename global binary).

## Rule

Before an eval run, confirm both the **MCP server** and any **CLI binary** under test resolve to the **repo's current version** (the working tree / the version in `pyproject.toml`), and **fail fast** if not.

## Do

- **Invoke the repo's binary, not `PATH`.** Spawn the MCP/CLI through the repo's environment — `uv run <cmd>` or the repo venv's entrypoint — never a bare `<cli>` resolved from `PATH` (which may be a stale `uv tool install`).
- **Assert the version at startup (preflight).** Compare the spawned server's reported version and the CLI's `--version` against the repo's `pyproject.toml` version (or `git rev-parse HEAD`). Abort the run with a clear message on mismatch — never silently score stale code (mirror the fail-fast credential preflight pattern).
- **Pin, don't float.** In CI/eval configs, reference the repo's installed package, not a global tool.

## Quick check

```bash
# version under test (repo)
uv run python -c "import importlib.metadata as m; print(m.version('<package>'))"
# what a bare PATH binary resolves to (the trap)
command -v <cli>; <cli> --version
# these MUST match; if the PATH binary != repo version, the eval is testing stale code
```

## Red flags

- The eval invokes a bare CLI name resolved from `PATH`.
- An MCP/CLI was `uv tool install`-ed globally and not refreshed after a code change.
- Results don't reflect a change you know landed (a rename, a new flag, a description tweak) — suspect a stale binary first.
