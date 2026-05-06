---
name: awx-ssh-bootstrap
description: Bootstrap AWX SSH access on freshly MAAS-deployed nodes before running prep playbooks. Use when AWX jobs fail with Permission denied or UNREACHABLE on new nodes.
---

# AWX SSH Bootstrap for Fresh Nodes

## When This Applies

- `awx-cli check-access <host>` reports **FAIL** (programmatic detection)
- AWX job fails with `Permission denied (publickey)` or `UNREACHABLE`
- Target node was recently deployed via MAAS
- Node has never had an AWX job run successfully against it

MAAS-deployed nodes only have the `ubuntu` user. AWX authenticates as `ansible` (credential id 3), so **every AWX job will fail until the `ansible` user is bootstrapped**.

## Prerequisites

1. Node status is **Deployed** in MAAS
2. Workspace has the `~/.ssh/together` private key
3. The `infra` repo is available at `/workspaces/together/infra`

## Step 0 — Check if Bootstrap is Needed

Run `awx-cli check-access` to see if AWX can already SSH to the node. This is a pure SSH probe — no AWX credentials required:

```bash
awx-cli check-access <device-name>
```

If it reports **OK**, the node already has the `ansible` user and no bootstrap is needed. If it reports **FAIL**, continue with the steps below.

## Step 1 — Resolve IPs from NetBox

Hostnames won't resolve via DNS from the workspace. Get the management IP from NetBox for each node:

```bash
netbox-cli lookup <device-name>
```

## Step 2 — Create a Temporary Inventory

Build an inventory file with `ansible_host` set to the NetBox IP for each node:

```bash
cat > /tmp/bootstrap-inventory.ini << 'EOF'
[all]
research-common-h100-059 ansible_host=192.168.229.59
research-common-h100-113 ansible_host=192.168.229.113
EOF
```

## Step 3 — Run the Bootstrap Playbook

Run `prep-awx-access.yaml` locally (not via AWX — AWX can't SSH to the node yet):

```bash
ansible-playbook infra/ansible/prep-awx-access.yaml \
  -i /tmp/bootstrap-inventory.ini \
  --user ubuntu \
  --private-key ~/.ssh/together \
  -e "ansible_ssh_extra_args='-o StrictHostKeyChecking=accept-new'" \
  -v
```

This applies the `awx-ansible-user` role which creates the `ansible` account (uid/gid 800), deploys the AWX SSH key, and configures passwordless sudo.

**Verify**: the play should complete with `changed` on all hosts and zero failures.

## Step 4 — Launch AWX Prep Jobs

Once bootstrap succeeds, AWX can reach the nodes. Launch prep jobs normally:

```bash
awx-cli launch 472 --limit "research-common-h100-059,research-common-h100-113" --wait --timeout 600
```

Template 472 runs `prep-ori-gpu-node.yaml` which handles the full node preparation.

## Quick Reference

| Phase | Tool | What Happens |
|-------|------|-------------|
| Access check | `awx-cli check-access` | Detect if bootstrap is needed |
| IP lookup | `netbox-cli lookup` | Get `ansible_host` IPs |
| Bootstrap | `ansible-playbook` (local) | Create `ansible` user + SSH key |
| Prep | `awx-cli launch 472` | Full node preparation via AWX |
| Acceptance | AWX job templates | GPU diag, NCCL tests, etc. |

## Gotchas

- **Always use `ansible_host=<IP>` in the inventory** — hostnames don't resolve from the workspace.
- **Never skip the bootstrap step** — AWX jobs will silently fail on all tasks if `ansible` doesn't exist.
- **The playbook auto-detects connectivity** — it tries your personal SSH user first, falls back to `ubuntu`. Passing `--user ubuntu` explicitly is safest for fresh nodes.
- **Clean up the temp inventory** after bootstrap: `rm /tmp/bootstrap-inventory.ini`
