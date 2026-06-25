---
name: cli-discovery
description: Use when an agent needs to discover or version-check the togethercomputer/mcp-common companion CLIs. Triggers on "check the version of the mcp-common CLI tools", "what CLIs does mcp-common ship", "<cli> --version", "--help", netbox-cli, redfish-cli, ufm-cli, awx-cli, dc-support-cli, network-cli, pip show mcp-common, uv tool list, mcp-common-doctor, or mcp-plugin-gen being used to answer a CLI-version question.
---

# CLI discovery for the six mcp-common `*-cli` tools

`togethercomputer/mcp-common` ships **six** companion CLIs, one per server,
each on PATH as a `*-cli` binary. These are the version-reporting CLIs.

| CLI | Server |
|---|---|
| `awx-cli` | `awx-mcp` |
| `dc-support-cli` | `dc-support-mcp` |
| `network-cli` | `mcp-network` |
| `netbox-cli` | `netbox-mcp` |
| `redfish-cli` | `redfish-mcp` |
| `ufm-cli` | `ufm-mcp` |

## `--version` works on every one (eager, no credentials)

Every one of the six supports an **eager root `--version` flag**:

- **Needs no credentials** — short-circuits before any command and before
  client setup, so it works off-VPN / without `NETBOX_TOKEN`, `AWX_TOKEN`, etc.
- Prints the installed package version as a **single clean line on stdout**,
  exit 0.
- `--help` likewise works without creds and lists the subcommands.

Discover the flag with `<cli> --help`, or just run `<cli> --version`.

## The rabbit-hole trap — do NOT spiral on these

When asked "check the version of the mcp-common CLI tools", agents commonly
spiral on the wrong things. **None of these answer the question:**

- `mcp-common` (the **library**) — not a CLI. `pip show mcp-common` reports the
  *library* version, unrelated to any `*-cli`'s version.
- `mcp-plugin-gen` and `mcp-common-doctor` — helper scripts, not
  version-reporting CLIs for the six servers.
- `uv tool list` — lists installed tools, does not report a CLI's version.

**Run each `*-cli --version`.** That is the only correct answer.

## `ufm-cli` has BOTH a `--version` flag AND a `version` subcommand

They answer different questions — do not conflate them:

- `ufm-cli --version` → installed **CLI package version** (e.g. `1.11.2`).
- `ufm-cli version` → **live UFM server** version JSON
  (`ufm_release_version`, etc.). Needs UFM creds.

"The version of ufm-cli" = `--version`.

## `redfish-cli` is the one documented exception

The other five pass `package_name=` to `build_cli_from_mcp` to wire
`--version`; `redfish-cli` keeps a bespoke `--version` merged into its own
root callback (Typer allows only one root callback, and redfish-cli needs its
own for global `--user`/`--password`). User-facing behavior is identical:
`redfish-cli --version` → `2.26.3`, exit 0, no creds.

## Slow startup — wait, don't assume it hangs

`--version` is an eager short-circuit and **does return**, but Python import +
Typer construction takes a few seconds. Measured:

- `netbox-cli` ~3s, `network-cli` ~3.3s, `ufm-cli` ~3.4s, `awx-cli` ~3.5s,
  `dc-support-cli` ~2.8s, `redfish-cli` **~6.7s** (slowest).

Set a bash timeout comfortably above ~7s (the eval suite uses 180s). Do not
abandon `--version` for `pip show` / `uv tool list` because startup took a few
seconds.

## `network-cli` keeps `--version` / `--help` log-free on stderr

`network-cli --version` prints just the version on stdout with **empty stderr**
(no `Loaded N site(s)` log). If you capture stdout+stderr together, the version
is the clean stdout token — don't report a log line as the version. A real
command (`network-cli sites`) still logs normally.

## Quick reference

```bash
for c in awx-cli dc-support-cli network-cli netbox-cli redfish-cli ufm-cli; do
  printf '%s: ' "$c"; "$c" --version
done
```

## Reference

- Long-form companion:
  [`docs/AGENT_CONVENTIONS.md` — CLI discovery](https://github.com/togethercomputer/mcp-common/blob/main/docs/AGENT_CONVENTIONS.md#cli-discovery-the-six--cli-tools-and---version).
- Framework `--version` wiring:
  `src/mcp_common/dual_mode/builder.py` (`_attach_version_option`).
- `redfish-cli` exception NOTE:
  `servers/redfish-mcp/src/redfish_mcp/cli.py`.
