# MCP Release Process

## How releases work (automated)

Every MCP repo uses `python-semantic-release` to automate versioning and releases.
The pipeline runs on every push to `main`:

```
Merge PR to main
  → CI: python-semantic-release analyzes commit messages
  → If feat:/fix: found: bumps pyproject.toml, creates git tag, creates GitHub Release
  → Release workflow dispatches to mcp-common rebuild-marketplaces
  → Marketplace directories are rebuilt with latest versions from all repos
```

## Rules

### Do NOT manually manage versions

- **Never edit `pyproject.toml` version** -- semantic-release owns it
- **Never run `git tag`** -- semantic-release creates tags
- **Never commit `chore: release vX.Y.Z`** -- this bypasses semantic-release

### Use conventional commit messages

Semantic-release reads commit messages to determine the version bump:

| Prefix | Bump | Example |
|--------|------|---------|
| `feat:` | Minor (0.X.0) | `feat: add inventory-audit command` |
| `fix:` | Patch (0.0.X) | `fix: normalize hostname case in diff` |
| `feat!:` or `BREAKING CHANGE:` | Major (X.0.0) | `feat!: remove deprecated tool` |
| `docs:`, `chore:`, `refactor:`, `test:` | No release | `docs: update skill file` |

If your PR has a mix, the highest-priority prefix wins.

### Squash merge PRs

Always squash-merge PRs. The squash commit message becomes the semantic-release
input. Make sure the squash message has the right prefix:
- `feat: add inventory-audit command (#63)` -- triggers minor bump
- `fix: normalize hostname case (#63)` -- triggers patch bump

## What triggers a marketplace update

1. Semantic-release creates a GitHub Release
2. The release workflow dispatches `mcp-release` event to `vhspace/mcp-common`
3. `rebuild-marketplaces.yml` clones all MCP repos at their latest release tag
4. Runs `mcp-plugin-gen` to rebuild marketplace directories
5. Creates a PR on mcp-common with updated marketplace artifacts

### Required secret

`MARKETPLACE_DISPATCH_PAT` must be available as an org-level secret on `vhspace`
with `repo` scope. This is set at:
**Organization Settings > Secrets and variables > Actions > Organization secrets**

## Deploying to the workspace

After a release, update the workspace:

```bash
# Check what's stale
/workspaces/together/scripts/mcp-release.sh --check

# Auto-update everything
/workspaces/together/scripts/mcp-release.sh
```

This updates `uv tool` installs and `.cursor/mcp.json` version tags.

## Manual release (escape hatch)

If semantic-release doesn't trigger (e.g., all commits are `chore:`/`docs:`),
you can force a release via the GitHub Actions UI:

1. Go to the repo's Actions tab
2. Select the "Release" workflow
3. Click "Run workflow" on the `main` branch

Or create a GitHub Release manually:

```bash
gh release create vX.Y.Z --repo vhspace/<repo> --title "vX.Y.Z" --generate-notes
```

Then trigger the marketplace rebuild:

```bash
gh workflow run rebuild-marketplaces.yml --repo vhspace/mcp-common
```

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

### Using pyproject.toml data tables (rare)

If data genuinely cannot live inside the package source tree, declare it in
`pyproject.toml`:

```toml
[tool.uv.build-backend.data]
data = "data"
```

Prefer moving data into the package over this approach — it is simpler and
requires no build config.

### Examples

- [vhspace/network-mcp#9](https://github.com/vhspace/network-mcp/issues/9) — `switch_db/` at repo root broke marketplace installs
- [vhspace/network-mcp#10](https://github.com/vhspace/network-mcp/pull/10) — fix PR (merged pattern)
- [vhspace/redfish-mcp#119](https://github.com/vhspace/redfish-mcp/issues/119) — `hardware_db/` at repo root, same class of bug

## Troubleshooting

### Marketplace not updating after release

1. Check if the GitHub Release was created: `gh release list --repo vhspace/<repo> --limit 1`
2. Check if the dispatch fired: look for `Notify mcp-common marketplace` step in the release workflow run
3. Check `MARKETPLACE_DISPATCH_PAT` secret is set (org-level)
4. Check `rebuild-marketplaces.yml` run status: `gh run list --repo vhspace/mcp-common --workflow rebuild-marketplaces.yml --limit 3`
5. If rate-limited (HTTP 429), wait and retry: `gh workflow run rebuild-marketplaces.yml --repo vhspace/mcp-common`

### Version in marketplace is stale

The `rebuild-marketplaces.yml` clones repos at their **latest GitHub Release tag**.
If a repo has git tags but no GitHub Release, the marketplace uses an older version.
Fix: create a GitHub Release from the tag:

```bash
gh release create vX.Y.Z --repo vhspace/<repo> --title "vX.Y.Z" --generate-notes
gh workflow run rebuild-marketplaces.yml --repo vhspace/mcp-common
```
