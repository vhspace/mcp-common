---
name: prefer-ipa-cli
description: >-
  Use ipa-cli instead of the native ipa command for FreeIPA operations.
  Triggers on ipa command usage, kinit, FreeIPA queries, user/host group
  lookups, HBAC checks, or SSH access auditing.
---

# Prefer ipa-cli Over Native ipa

## Rule

**Always use `ipa-cli` instead of the native `ipa` command.** If `ipa-cli` is on PATH, use it directly. Never run `ipa`, `kinit`, or raw FreeIPA JSON-RPC calls.

## Why

| `ipa` (native) | `ipa-cli` (preferred) |
|---|---|
| Requires Kerberos ticket (`kinit` + password interactively) | Auto-loads credentials from `.env` — zero setup |
| Only available on IPA-enrolled hosts | Installed anywhere via `uv tool` |
| Raw LDAP DN output (`uid=alice,cn=users,cn=accounts,...`) | Normalized readable names (`alice`) |
| No HBAC test explanation | `show-user` shows groups + HBAC + sudo in one call |
| No hostgroup diff | `hostgroup-diff` compares membership against expected list |

## Quick Check

```bash
which ipa-cli && ipa-cli --help
```

If not installed: `uvx --from ipa-mcp ipa-cli` or ask the user to run `mcp-release.sh`.

## Command Mapping

| Task | Native `ipa` (DO NOT USE) | `ipa-cli` (USE THIS) |
|------|--------------------------|---------------------|
| List groups | `ipa group-find` | `ipa-cli groups` |
| List host groups | `ipa hostgroup-find` | `ipa-cli hostgroups` |
| Show user perms | `ipa user-show mballew --all` | `ipa-cli show-user mballew` |
| Show group | `ipa group-show admins` | `ipa-cli show-group admins` |
| Show host group | `ipa hostgroup-show hg_name` | `ipa-cli show-hostgroup hg_name` |
| List HBAC rules | `ipa hbacrule-find` | `ipa-cli hbac-rules` |
| Show HBAC rule | `ipa hbacrule-show rule_name` | `ipa-cli show-hbacrule rule_name` |
| List sudo rules | `ipa sudorule-find` | `ipa-cli sudo-rules` |
| List users | `ipa user-find` | `ipa-cli users` |
| List hosts | `ipa host-find` | `ipa-cli hosts` |
| Test SSH access | _(no equivalent)_ | `ipa-cli hbactest-explain -u user -t host` |
| Diff hostgroup | _(no equivalent)_ | `ipa-cli hostgroup-diff hg_name -e "h1,h2"` |
| Full forge setup | _(many manual steps)_ | `ipa-cli setup-forge name --hosts "h1,h2"` |

## JSON Output

Append `--json` or `-j` to any command for structured JSON output:

```bash
ipa-cli show-user mballew --json
ipa-cli hostgroups --json
ipa-cli hbactest-explain -u alice -t node1.cloud.together.ai --json
```

## Credentials

`ipa-cli` auto-discovers credentials. Search order:
1. Environment variables (`IPA_HOST`, `IPA_PASSWORD`)
2. `.env` in current directory
3. `.env` in parent directories (walks up)
4. `/workspaces/together/.env`

**Never `source .env`, `export`, or `grep` credentials manually. Just run the command.**

## Fallback: MCP Tools

If shell access is unavailable, use the IPA MCP tools (`ipa_list_groups`, `ipa_show_user`, etc.) as a fallback. The MCP server provides the same capabilities with JSON schema validation.
