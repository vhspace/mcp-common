---
name: ipa-operations
description: Use when managing FreeIPA users, host groups, HBAC rules, sudo rules, or investigating SSH access. Triggers on mentions of IPA, FreeIPA, hostgroup, HBAC, sudo rules, or SSH access control.
---

# IPA Operations

## Choose Your Path

| Path | When to Use |
|------|-------------|
| **CLI** (`ipa-cli`) | Shell access available, token budget matters, compact output |
| **MCP** (`ipa_list_hostgroups`, etc.) | No shell access, sandboxed agent, need JSON schema validation |

## Critical: Never Hardcode Credentials

**Never write raw HTTP requests to the FreeIPA API. Never hardcode credentials in scripts or transcripts.**

The CLI auto-sources credentials from `.env`. If `ipa-cli` is not on PATH, use the MCP tools instead.

## CLI Path

**IMPORTANT:** The CLI wrapper auto-sources `.env` for credentials. Never manually `source`, `export`, or `grep` env vars — just run the command directly.

| Task | Command |
|------|---------|
| List host groups | `ipa-cli hostgroups` |
| Show host group | `ipa-cli show-hostgroup hg_research_common_h100 --json` |
| List HBAC rules | `ipa-cli hbac-rules --json` |
| Show HBAC rule | `ipa-cli show-hbacrule allow_research_common_h100 --json` |
| List sudo rules | `ipa-cli sudo-rules --json` |
| Show user perms | `ipa-cli show-user mballew --json` |
| List users | `ipa-cli users --json` |
| List hosts | `ipa-cli hosts --json` |
| Diff hostgroup | `ipa-cli hostgroup-diff hg_cluster --expected "host1.fqdn,host2.fqdn" --json` |
| Apply diff | `ipa-cli hostgroup-diff hg_cluster --expected "host1.fqdn,host2.fqdn" --apply` |
| Add hosts to group | `ipa-cli hostgroup-add-hosts hg_cluster "host1.fqdn,host2.fqdn"` |
| Remove hosts from group | `ipa-cli hostgroup-remove-hosts hg_cluster "host1.fqdn,host2.fqdn"` |
| Test HBAC access | `ipa-cli hbactest-explain --user mballew --targethost node.fqdn --json` |
| Setup forge cluster | `ipa-cli setup-forge clustername --hosts "h1.fqdn,h2.fqdn" --users "user1,user2"` |

## MCP Path

| Task | Tool Call |
|------|-----------|
| List host groups | `ipa_list_hostgroups(criteria="research")` |
| Show host group | `ipa_show_hostgroup(name="hg_research_common_h100")` |
| Show user perms | `ipa_show_user(name="mballew")` |
| List HBAC rules | `ipa_list_hbac_rules()` |
| Test access | `ipa_hbactest_explain(user="mballew", targethost="node.fqdn")` |
| Diff hostgroup | `ipa_hostgroup_diff(hostgroup="hg_cluster", expected=["h1.fqdn", "h2.fqdn"])` |
| Add hosts to group | `ipa_add_hostgroup_members(name="hg_cluster", hosts=["h1.fqdn"])` |
| Remove hosts from group | `ipa_remove_hostgroup_members(name="hg_cluster", hosts=["h1.fqdn"])` |

## Naming Conventions

- Host groups: `hg_<cluster_name>` (e.g. `hg_research_common_h100`)
- HBAC rules: `allow_<cluster_name>` (e.g. `allow_research_common_h100`)
- Sudo rules: `sudo_<cluster_name>` (e.g. `sudo_research_common_h100`)
- User groups: `ug_<cluster_name>` (e.g. `ug_research_common_h100`)
- Host FQDNs: `<hostname>.cloud.together.ai`

## Common Workflow: Audit Hostgroup Membership

```bash
# 1. Get current IPA members
ipa-cli show-hostgroup hg_research_common_h100 --json

# 2. Get expected hosts from NetBox
netbox-cli list dcim.device --filter "cluster=research-common-h100,status=active" --fields "name" --json --limit 200

# 3. Diff and apply
ipa-cli hostgroup-diff hg_research_common_h100 \
  --expected "host1.cloud.together.ai,host2.cloud.together.ai" \
  --apply
```
