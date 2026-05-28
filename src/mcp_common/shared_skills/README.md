# Shared agent skills (staging directory)

This directory is the staging ground for the cross-MCP shared-skills
mechanism tracked in
[vhspace/mcp-common#95](https://github.com/vhspace/mcp-common/issues/95).

## Why this exists

Several conventions in the vhspace MCP ecosystem are easier to teach an
agent through a Cursor / Claude / OpenCode `SKILL.md` than through prose
documentation. Today every MCP plugin tree ships its own per-MCP skills,
but cross-cutting conventions (how to author a tool, how output should
look, how to file an issue when something fails) belong to mcp-common.

Once #95 lands, `mcp-plugin-gen` will promote every `SKILL.md` bundle
under this directory into the generated `skills/` tree of every
downstream MCP plugin, so all agents working on any vhspace MCP see the
same shared conventions automatically.

## Layout

Each immediate subdirectory is a single skill bundle:

```
shared_skills/
  README.md                          ← this file
  __init__.py                        ← namespace marker
  mcp-common-conventions/
    SKILL.md                         ← skill body, with required frontmatter
```

`SKILL.md` files use the standard Cursor/Claude frontmatter:

```markdown
---
name: <kebab-case-slug>
description: Use when ... . Triggers on ... .
---

# Skill title

...skill body...
```

## Adding a new shared skill (until #95 lands)

1. Create a new subdirectory whose name matches the skill's `name:` slug.
2. Write `SKILL.md` with the frontmatter shape above. Keep it tight —
   skill files are agent-runtime triggers, not long-form references.
   Long-form references belong in `docs/`.
3. Reference any companion long-form doc from inside the SKILL body so
   agents that need depth can follow the link.
4. No code changes are needed in `mcp-plugin-gen` yet; the promotion
   step lands with #95.

## Status

- `mcp-common-conventions` — proto-skill paired with
  [`docs/AGENT_CONVENTIONS.md`](../../../docs/AGENT_CONVENTIONS.md). Authored
  ahead of #95 so the promotion step has a real bundle to ship on day one.
