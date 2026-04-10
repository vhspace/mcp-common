---
name: forge-ipa-setup
description: Use when setting up FreeIPA groups, HBAC rules, and sudo rules for forge cluster bringup. Triggers on FreeIPA, IPA, forge cluster access, HBAC, sudo rules, host groups, user groups, SSH access control.
---

# Forge IPA Setup

FreeIPA manages identity and access for forge clusters. Use this skill when bringing up a new forge cluster and configuring FreeIPA — user groups, host groups, HBAC rules, and sudo rules.

## Choose Your Path

This plugin provides two interfaces. Prefer CLI when shell access is available — it uses fewer tokens.

| Path | When to Use |
|------|-------------|
| **CLI** (`ipa-cli`) | Agent has shell access, token budget matters, compact output preferred |
| **MCP** (`ipa_setup_forge`, etc.) | No shell access, sandboxed agent, need structured JSON schema validation |

## Quick Start (triage)

**CLI:**
```bash
ipa-cli groups                    # list user groups
ipa-cli hostgroups                # list host groups
ipa-cli hbac-rules                # list HBAC rules
ipa-cli setup-forge cartesia5 \
  --hosts "host1.cloud.together.ai,host2.cloud.together.ai" \
  --users "alice,bob"
```

**MCP:**
```
ipa_list_groups()
ipa_list_hostgroups()
ipa_list_hbac_rules()
ipa_setup_forge(name="cartesia5", hosts=["host1.cloud.together.ai", "host2.cloud.together.ai"], users=["alice", "bob"])
```

## CLI Path

Requires `IPA_HOST` and `IPA_PASSWORD` env vars. Run `ipa-cli --help` for all commands.

| Task | Command |
|------|---------|
| List user groups | `ipa-cli groups` |
| List host groups | `ipa-cli hostgroups` |
| List HBAC rules | `ipa-cli hbac-rules` |
| List sudo rules | `ipa-cli sudo-rules` |
| List users | `ipa-cli users` |
| List hosts | `ipa-cli hosts` |
| Create user group | `ipa-cli create-group <name> --desc "description"` |
| Create host group | `ipa-cli create-hostgroup <name>` |
| Full forge setup | `ipa-cli setup-forge <cluster> --hosts "host1,host2" --users "alice,bob"` |

If `ipa-cli` is not on PATH, install with `uvx --from ipa-mcp ipa-cli` or run from the repo with `uv run ipa-cli`.

## MCP Path

### Read Tools (6)
| Tool | Description |
|------|-------------|
| `ipa_list_groups` | List user groups |
| `ipa_list_hostgroups` | List host groups |
| `ipa_list_hbac_rules` | List HBAC rules |
| `ipa_list_sudo_rules` | List sudo rules |
| `ipa_list_users` | List users |
| `ipa_list_hosts` | List hosts |

### Write Tools (10)
| Tool | Description |
|------|-------------|
| `ipa_create_group` | Create user group |
| `ipa_add_group_members` | Add users to group |
| `ipa_create_hostgroup` | Create host group |
| `ipa_add_hostgroup_members` | Add hosts to host group |
| `ipa_create_hbac_rule` | Create HBAC rule |
| `ipa_add_hbac_rule_members` | Add members to HBAC rule |
| `ipa_create_sudo_rule` | Create sudo rule |
| `ipa_add_sudo_rule_members` | Add members to sudo rule |
| `ipa_add_sudo_option` | Add sudo option |
| `ipa_setup_forge` | One-shot forge cluster setup |

## What Gets Created (setup-forge)

For a forge named `example`:

| Resource | Name | Purpose |
|----------|------|---------|
| User group | `ug_forge_example` | Contains users who can access the cluster |
| Host group | `hg_forge_example` | Contains the cluster's hosts |
| HBAC rule | `allow_forge_example` | Allows user group SSH access to host group (servicecat=all) |
| Sudo rule | `allow_sudo_example` | Grants user group passwordless sudo on host group |

Additionally, the host group is added to:
- `allow_forge_together_support` — HBAC rule for Together support team access
- `allow_sudo_together_forge-support` — sudo rule for Together support team

## Configuration

Required env vars:
- `IPA_HOST` — FreeIPA server hostname or URL
- `IPA_PASSWORD` — IPA admin password

Optional:
- `IPA_USERNAME` (default: `admin`)
- `IPA_VERIFY_SSL` (default: `false` — typical for self-signed certs)

## Cross-MCP Integration

- **NetBox MCP** — Look up host FQDNs before adding to IPA host groups. NetBox is source of truth for device inventory.
- **AWX MCP** — Trigger Ansible playbooks for IPA enrollment or host provisioning after forge setup.
- **MAAS MCP** — Coordinate with MAAS when commissioning nodes that will be enrolled in IPA.

## Key Gotchas

- **Hosts must be enrolled first** — Hosts must already be enrolled in IPA before they can be added to host groups.
- **Self-signed certs** — IPA servers typically use self-signed certs; set `IPA_VERIFY_SSL=false`.
- **Idempotent setup** — `ipa_setup_forge` / `ipa-cli setup-forge` skips resources that already exist.
- **Login fails** — Verify `IPA_HOST` is reachable and credentials are correct.
