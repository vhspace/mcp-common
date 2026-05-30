# Marketplace rebuild — auth & credentials

The **Rebuild Marketplaces** workflow (`.github/workflows/rebuild-marketplaces.yml`)
clones the private vhspace MCP repos and regenerates the plugin marketplace
directories. Because `mcp-common` is **public** and the source MCP repos are
**private**, the default `GITHUB_TOKEN` cannot read them (it is scoped to the
repo running the workflow). The workflow therefore authenticates with a
**short-lived, least-privilege GitHub App installation token** minted via
[`actions/create-github-app-token`](https://github.com/actions/create-github-app-token).

> The default `GITHUB_TOKEN` cannot read other private org repos, and a
> `permissions:` block can't grant cross-repo access — hence the App token.

## GitHub App

- **Name:** `vhspace-marketplace-rebuild`
- **App ID:** `3917484`
- **Permissions:** Repository → Contents: **Read-only** (covers `git clone` +
  reading release tags); Metadata: Read-only (implicit)
- **Installed on (Contents: read):** `awx-mcp`, `dc-support-mcp`, `gpu-diag-mcp`,
  `ipa-mcp`, `maas-mcp`, `netbox-mcp`, `redfish-mcp`, `ufm-mcp`, `viz-mcp`,
  `weka-mcp`

Keep the App installation list in sync with both the `repositories:` input on
the `Generate app token` step and the `REPOS` list in the clone step.

## GitHub Actions secrets (on `vhspace/mcp-common`)

| Secret | Contents |
|--------|----------|
| `MARKETPLACE_APP_ID` | the App ID — `3917484` |
| `MARKETPLACE_APP_PRIVATE_KEY` | the App's `.pem` private key (see 1Password below) |

Repository-level secrets are safe on this public repo: they are not exposed to
forked-PR workflows and **are** available to the `repository_dispatch` /
`workflow_dispatch` triggers this workflow uses.

## Credential backup (1Password)

The private key and App ID are backed up in 1Password — account
`my.1password.com`, vault **VHSpace**, item
**`vhspace-marketplace-rebuild.2026-05-30.private-key`**:

| What | `op://` reference |
|------|-------------------|
| Private key (PEM) | `op://VHSpace/vhspace-marketplace-rebuild.2026-05-30.private-key/notesPlain` |
| App ID | `op://VHSpace/vhspace-marketplace-rebuild.2026-05-30.private-key/add more/app id` |

> **Never commit the private key.** Its value lives only in 1Password and the
> `MARKETPLACE_APP_PRIVATE_KEY` GitHub Actions secret. The `op://` references
> above are pointers, not secrets, and are safe to record here.

## Validate

```bash
gh workflow run "Rebuild Marketplaces" -R vhspace/mcp-common -f dry_run=true
```

A healthy dry run logs `Cloned 10 repos`, produces no auth error, and opens no
PR. A real release `repository_dispatch` (`mcp-release`) opens a
`chore: rebuild marketplace directories` PR.

## Rotate the private key

1. GitHub App **vhspace-marketplace-rebuild** → **Private keys** →
   **Generate a private key** (downloads a new `.pem`).
2. Update the `MARKETPLACE_APP_PRIVATE_KEY` secret on `vhspace/mcp-common`.
3. Update the 1Password item (`notesPlain`).
4. **Delete the old key** in the App's settings.
5. Re-run the dry run above to confirm.
