---
name: op-session-signin
description: Use when 1Password CLI resolution fails because there is no active op session — `op whoami` reports "account is not signed in" / "You are not currently signed in" / "no active op session", `mcp-common-doctor` (or `mcp-plugin-gen doctor .`) reports an `op auth` failure, or an MCP/CLI fails to start because an `op://` secret reference cannot be resolved. Triggers on op whoami failure, not signed in, op-forward, 1Password sign-in, credential chain doctor failures, and op:// resolution errors.
---

# Pause and ask the user to sign in to 1Password (op)

When `op whoami` reports no session, the 1Password CLI cannot resolve any
`op://<vault>/<item>/<field>` reference. Every secret in the togethercomputer
MCP credential chain (`CachedResolver(EnvResolver(VAR))`) that points at an
`op://` ref will fail until a human signs in. **You cannot fix this yourself
from inside an agent session — stop and ask the user.**

## When this skill fires

Any of these means there is no active `op` session:

- `op whoami` exits non-zero, or prints `account is not signed in` /
  `You are not currently signed in`.
- `mcp-common-doctor` or `mcp-plugin-gen doctor .` reports `op auth` →
  `no active op session` (or `op timed out`).
- An MCP server or its companion CLI fails to start, or a tool fails, because
  an `op://` reference could not be resolved.

## Required behavior

### 1. PAUSE — stop retrying

- **Do not** keep re-reading secrets or re-running `op://`-dependent MCP tools
  in a loop. They will keep failing identically until a session exists.
- **Do not** run `op signin` inside a Linux devcontainer. With op-forward the
  proxy blocks it and returns `subcommand "signin" is not allowed through the
  proxy`. Retrying it wastes turns.
- **Do not** treat this as a credential-resolution bug to debug. The chain is
  working; it simply has no session to authenticate against.

### 2. Ask the user to sign in (environment-specific)

Tell the user exactly where to run the sign-in, based on the environment:

- **Linux devcontainer with op-forward (macOS host):** sign in on the **macOS
  HOST**, not in the container. Ask the user to run `op signin` (or open the
  1Password desktop app and unlock it) **on the Mac**. The container reaches
  1Password through the op-forward shim + socat relay; the first resolved
  `op://` ref will trigger Touch ID on the host. There is nothing to run inside
  the container.
- **Native macOS:** ask the user to run `op signin` locally (or unlock the
  1Password desktop app), then retry.
- **Headless / CI:** if no human is present, the correct fix is an
  `OP_SERVICE_ACCOUNT_TOKEN`, not interactive sign-in. Surface that to the user
  rather than retrying.

Then wait for the user to confirm they have signed in before retrying the
operation that failed.

### 3. `op account list` succeeding does NOT mean signed in

`op account list` (and `op account get`) read locally configured accounts and
can succeed with **no active session**. Do not treat a successful
`op account list` as proof of authentication.

**`op whoami` is the authoritative session check.** Confirm a session exists
with `op whoami` (exit 0 + an account in the output) before concluding the user
is signed in and resuming `op://` reads.

> op-forward gotcha: `op` colorizes output even with `--format json`, so set
> `NO_COLOR=1` (or strip ANSI) when parsing `op` output in a script.

## Reference

- Full setup, op-forward architecture, and troubleshooting table:
  [`docs/credential-chain-setup.md`](https://github.com/togethercomputer/mcp-common/blob/main/docs/credential-chain-setup.md).
- Devcontainer secret bridging:
  [`DEVCONTAINER_1PASSWORD.md`](https://github.com/togethercomputer/mcp-common/blob/main/DEVCONTAINER_1PASSWORD.md).
- Credential conventions:
  [`docs/AGENT_CONVENTIONS.md`](https://github.com/togethercomputer/mcp-common/blob/main/docs/AGENT_CONVENTIONS.md).
