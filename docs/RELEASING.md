# Release Process (mcp-common monorepo)

> **Status.** The monorepo is live in `togethercomputer/mcp-common`: `mcp-common`
> and `servers/netbox-mcp` are independent uv projects, netbox-mcp pins
> `mcp-common` by git tag (`mcp-common-v0.37.0`), and the tag-triggered release
> workflow, Renovate, and the HEAD-compat canary are wired. Promotion to `main`
> is by reviewed `dev → main` PR (org rule: 1 approval).

## Packages & independent versioning

This repo holds two **independently versioned** packages:

| Package | Path | Tag format | Consumed as |
|---|---|---|---|
| `mcp-common` (shared library) | repo root, `src/mcp_common/` | `mcp-common-v{version}` | library dependency of every MCP server |
| `netbox-mcp` (server) | `servers/netbox-mcp/` | `netbox-mcp-v{version}` | `uvx --from "git+…/mcp-common@<ref>#subdirectory=servers/netbox-mcp" netbox-mcp` |

They release on **separate cadences**, with **separate tags** and **separate
`uv.lock`s**. `netbox-mcp` is an *independent uv project* — deliberately **not** a
uv workspace member — so it can pin and adopt `mcp-common` on its own schedule.
(uv forbids a workspace member from pinning another member to a version/git
source, so "workspace" and "drift" are mutually exclusive — workspaces share one
lockfile and one version of every package.)

## How `netbox-mcp` consumes `mcp-common` (intentional drift)

`servers/netbox-mcp/pyproject.toml` pins the library by **git tag** — the same
mechanism every external MCP server uses:

```toml
[project]
dependencies = ["mcp-common>=0.37,<1"]

[tool.uv.sources]
mcp-common = { git = "https://github.com/togethercomputer/mcp-common", tag = "mcp-common-v0.37.0" }
```

A new `mcp-common` release does **not** affect `netbox-mcp` until someone bumps
this pin. That is the intended drift: `mcp-common` ships on its own cadence, and
`netbox-mcp` adopts a new version **deliberately, after its own tests pass**.

### Adopting a new `mcp-common` in `netbox-mcp`

1. Bump the `tag` and the `>=` floor in `servers/netbox-mcp/pyproject.toml`.
2. `cd servers/netbox-mcp && uv lock`
3. Run netbox-mcp's tests (and any live NetBox checks).
4. Open a PR — e.g. `fix(netbox-mcp): adopt mcp-common-v0.38.0`.

**Renovate** opens these bump PRs automatically when a new `mcp-common-v*` tag
ships (native `tool.uv.sources` git-tag support); the PR's CI run is the gate.

### Dev loop — test `netbox-mcp` against unreleased (in-repo) `mcp-common`

The pin is committed, so overlay the local library **without committing** it.
From `servers/netbox-mcp/` (where `../..` is the `mcp-common` project root):

```bash
uv run --with-editable ../.. -- pytest      # one-shot; does NOT touch pyproject/uv.lock
# or, for a longer session:
uv sync && uv pip install -e ../..          # overlay editable mcp-common; `uv sync` reverts
```

uv has no committed dev/prod source switch ([astral-sh/uv#9258](https://github.com/astral-sh/uv/issues/9258)), so the overlay above is the supported pattern. Do **not** commit a `{ path = "../.." }` source.

### HEAD-compat canary (drift signal)

`.github/workflows/canary.yml` is a **separate, non-required** workflow that builds
`netbox-mcp` against `mcp-common` **HEAD** (the overlay above) on every push/PR.
Red = netbox has drifted in a way that breaks against the latest library (bump +
fix soon); green = safe to bump the pin. It never blocks merges.

## Per-package releases (tag-triggered)

Releases are **tag-triggered** (`.github/workflows/release.yml`), which works under
the protected-`main` rule with no deploy key or bypass. Each package has its own
tag namespace so versions never collide:

- `mcp-common` → tag `mcp-common-v{version}`
- `netbox-mcp` → tag `netbox-mcp-v{version}`

To cut a release:

1. Bump the package's `pyproject.toml` version in a reviewed PR (optionally via
   `uv run semantic-release version --no-push` using its `[tool.semantic_release]`
   config) and merge to `main`.
2. Push the matching tag, e.g. `git tag mcp-common-v0.38.0 && git push origin mcp-common-v0.38.0`.
3. The Release workflow builds that package and publishes a GitHub Release with
   the built artifacts.

Adopting a new `mcp-common` in `netbox-mcp` is then the pin bump described in
"Adopting a new mcp-common" above (Renovate opens that PR automatically).

## Conventional commits

Semantic-release reads commit messages to determine the bump. Scope the type to
the package you changed:

| Prefix | Bump | Example |
|--------|------|---------|
| `feat(...)` | Minor (0.X.0) | `feat(netbox-mcp): add inventory-audit command` |
| `fix(...)` | Patch (0.0.X) | `fix(mcp-common): normalize hostname case` |
| `feat(...)!` or `BREAKING CHANGE:` | Major (X.0.0) | `feat(mcp-common)!: remove deprecated helper` |
| `docs:`, `chore:`, `refactor:`, `test:`, `ci:` | No release | `docs: update RELEASING` |

## Branch model

`dev` is the long-running integration branch (full CI on every push/PR); `main`
is protected and holds released code. Promote `dev → main` via a reviewed PR for
major changes; releases cut from `main`.

### Versioning

- Bump a package's `pyproject.toml` `version` in a **reviewed PR** (optionally
  computed with `uv run semantic-release version --no-push`), then push the
  matching per-package tag — see [Per-package releases](#per-package-releases-tag-triggered).
- Use the namespaced tag scheme (`mcp-common-v*` / `netbox-mcp-v*`); don't push a
  bare `v<version>` tag — it's ambiguous across packages and won't trigger a release.

## Marketplace artifacts

The `cursor-marketplace/`, `claude-marketplace/`, `opencode-marketplace/`, and
`openhands-marketplace/` directories are **generated** from in-repo `servers/*`
by `rebuild-marketplaces.yml` — no cross-repo cloning and no dispatch PAT. After a
server's `mcp-plugin.toml` (or skills/rules) change, regenerate:

```bash
uv run mcp-plugin-gen generate servers/<name>
uv run python -m mcp_common.marketplace_builder --repos-dir servers --output-dir .
```

(The pre-commit hook also regenerates these.)

## Packaging Static Data

Runtime data files (JSON, YAML, schemas, hardware DBs, templates) **must** live
inside `src/<package>/` so they are included in the installed wheel. If the data
directory sits at the repo root, it will not exist when the package is installed
via `uvx`, `uv tool install`, or the Cursor marketplace.

### The anti-pattern

```python
# BROKEN — escapes to repo root, which doesn't exist in site-packages/
db_dir = Path(__file__).parent.parent / "hardware_db"
```

`Path(__file__).parents[N]` only works from a git checkout. In an installed
package `__file__` resolves to something like
`.../site-packages/my_mcp/server.py`, so climbing to `parents[2]` lands in
`site-packages/` — not the repo root.

### The correct pattern

```
repo/
  src/my_mcp/
    __init__.py
    data/          ← data lives INSIDE the package
      models.json
    server.py
  data -> src/my_mcp/data   ← symlink for backwards compat
```

```python
# CORRECT — resolves relative to the module, works everywhere
data_dir = Path(__file__).parent / "data"
```

### Checklist for moving data into a package

1. `mv data_dir/ src/<package>/data_dir/`
2. Update all `Path(__file__)` resolution to use `.parent / "data_dir"`
3. Create a symlink at the old root location: `ln -s src/<package>/data_dir data_dir`
4. If an env-var override exists (e.g. `MY_MCP_DATA_DIR`), keep it working
5. Verify with `uvx --from . my-mcp` that data is found at runtime

## Troubleshooting

### Marketplace not updating after a server change

1. Confirm the server's `mcp-plugin.toml` / `pyproject.toml` version changed.
2. Run the rebuild locally (commands above) and check the diff under `*-marketplace/`.
3. Re-run the workflow: `gh workflow run rebuild-marketplaces.yml --repo togethercomputer/mcp-common`.

### `netbox-mcp` install resolves the wrong `mcp-common`

`uvx --from "git+…/mcp-common@<ref>#subdirectory=servers/netbox-mcp"` resolves
`mcp-common` from `servers/netbox-mcp`'s pinned `tool.uv.sources` tag — not from
`<ref>`. To ship a new pairing, bump the pin (see "Adopting a new mcp-common")
and cut a `netbox-mcp-v*` release.
