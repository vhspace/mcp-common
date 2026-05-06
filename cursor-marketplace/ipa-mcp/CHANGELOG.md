# CHANGELOG

<!-- version list -->

## v1.3.0 (2026-04-23)

### Bug Fixes

- Address review findings -- DRY host parsing, update skills, add tests
  ([#19](https://github.com/vhspace/ipa-mcp/pull/19),
  [`4387bf2`](https://github.com/vhspace/ipa-mcp/commit/4387bf26b79cb0095b75dfacacc37f633617ebaf))

### Chores

- Update release badge to v1.2.1
  ([`43087df`](https://github.com/vhspace/ipa-mcp/commit/43087dfc3d31a3648ae704759fc8f78c92a966af))

### Features

- Add hostgroup-add-hosts and hostgroup-remove-hosts commands
  ([#19](https://github.com/vhspace/ipa-mcp/pull/19),
  [`4387bf2`](https://github.com/vhspace/ipa-mcp/commit/4387bf26b79cb0095b75dfacacc37f633617ebaf))

- Add hostgroup-add-hosts and hostgroup-remove-hosts commands (#17)
  ([#19](https://github.com/vhspace/ipa-mcp/pull/19),
  [`4387bf2`](https://github.com/vhspace/ipa-mcp/commit/4387bf26b79cb0095b75dfacacc37f633617ebaf))


## v1.2.1 (2026-04-23)

### Bug Fixes

- Add --yes flag to hostgroup-diff --apply ([#18](https://github.com/vhspace/ipa-mcp/pull/18),
  [`b6795ff`](https://github.com/vhspace/ipa-mcp/commit/b6795ff66d6687930b2406a56dfc105a5a4471d4))

- Add --yes flag to hostgroup-diff --apply (#16) ([#18](https://github.com/vhspace/ipa-mcp/pull/18),
  [`b6795ff`](https://github.com/vhspace/ipa-mcp/commit/b6795ff66d6687930b2406a56dfc105a5a4471d4))

- Update docstring and skills for --yes flag ([#18](https://github.com/vhspace/ipa-mcp/pull/18),
  [`b6795ff`](https://github.com/vhspace/ipa-mcp/commit/b6795ff66d6687930b2406a56dfc105a5a4471d4))

### Chores

- Update release badge to v1.2.0
  ([`e701bde`](https://github.com/vhspace/ipa-mcp/commit/e701bde6f0f6ea85faa588e5502ab85cdf324730))

### Continuous Integration

- Notify mcp-common marketplace on release
  ([`692ed76`](https://github.com/vhspace/ipa-mcp/commit/692ed767fb6b585bc3dac23f6801382d603ca181))


## v1.2.0 (2026-04-10)

### Chores

- Update release badge to v1.1.1
  ([`2e41ec1`](https://github.com/vhspace/ipa-mcp/commit/2e41ec1af23bf504769d606d62420b5fdb96db07))

### Features

- Use pinned git+https server source and fix release workflow
  ([`76bf3cc`](https://github.com/vhspace/ipa-mcp/commit/76bf3ccea1661d8607c7a119226a3bb90b0f4475))


## v1.1.1 (2026-04-09)

### Bug Fixes

- Remove unsupported hooks field from Claude plugin manifest
  ([`8c303bf`](https://github.com/vhspace/ipa-mcp/commit/8c303bf474ae983fd35e3a608390a49e65d637b2))

### Chores

- Update release badge to v1.1.0
  ([`3762cff`](https://github.com/vhspace/ipa-mcp/commit/3762cffbf6d0323939c09368f5dc5b962aa6daf1))


## v1.1.0 (2026-04-09)

### Chores

- Update release badge to v1.0.0
  ([`3675e2f`](https://github.com/vhspace/ipa-mcp/commit/3675e2fb35b04f8a0184698528ff682a4ee9d3d3))

### Features

- Add Claude marketplace registry entry artifact
  ([`abdde00`](https://github.com/vhspace/ipa-mcp/commit/abdde00ab5d7dcb21666d754d4175eb75f1643c1))


## v1.0.0 (2026-04-09)

### Chores

- Migrate plugin version source to pyproject
  ([`4073575`](https://github.com/vhspace/ipa-mcp/commit/4073575a2c176d269ab98a0d2bdcb771ef59fefa))

### Continuous Integration

- Add repo-local auto-updated release badge
  ([`19f9590`](https://github.com/vhspace/ipa-mcp/commit/19f9590c1e58205f7432cd0a654a5f5fa930e515))

- Trigger release workflow on push to main
  ([`1c892ee`](https://github.com/vhspace/ipa-mcp/commit/1c892eeed34c06c8bba99d46300728a4f9246427))


## v0.6.0 (2026-04-08)

### Chores

- Release v0.6.0
  ([`47cf5c0`](https://github.com/vhspace/ipa-mcp/commit/47cf5c08e1cb82640ebcb7a509bcba66aac538e8))

### Features

- Add prefer-ipa-cli skill to Cursor plugin
  ([`d314a59`](https://github.com/vhspace/ipa-mcp/commit/d314a596fccc4cdd757a499525c027fa801aec36))


## v0.5.1 (2026-04-08)

### Bug Fixes

- CLI auto-loads .env file via Settings instead of requiring manual source
  ([`2e833b0`](https://github.com/vhspace/ipa-mcp/commit/2e833b091251f9206083d2bdf46e1d57c117dd6c))


## v0.5.0 (2026-04-08)

### Chores

- Release v0.5.0
  ([`9ecb8d0`](https://github.com/vhspace/ipa-mcp/commit/9ecb8d0050d71ba5fde8442b044207299834563c))

### Continuous Integration

- Add CI workflow, fix lint/format, add README badges
  ([`acf7106`](https://github.com/vhspace/ipa-mcp/commit/acf7106d377f6cdef9297f4e554e3944ccde724f))

### Features

- Add hbactest explain with native+fallback, normalized members, and hostgroup diff
  ([`27a56a4`](https://github.com/vhspace/ipa-mcp/commit/27a56a4f195734a7109f329821c0bd732b3c700b))

- Add show-user command to CLI and MCP server ([#9](https://github.com/vhspace/ipa-mcp/pull/9),
  [`9499c8a`](https://github.com/vhspace/ipa-mcp/commit/9499c8a512a200b045555ebe1d2b7fd95ea1ede3))


## v0.4.0 (2026-04-07)

### Bug Fixes

- Bump mcp-common to v0.6.1 (sync/async wrapper fix)
  ([`99e6c32`](https://github.com/vhspace/ipa-mcp/commit/99e6c325cafb6d97390ba855cc61bba303b8342c))

### Chores

- Release v0.4.0
  ([`b02185e`](https://github.com/vhspace/ipa-mcp/commit/b02185ed895304df69bb6cb5ef051bfa9add7131))

### Features

- Wire mcp-common agent remediation and SSL warning suppression
  ([`bb10cc8`](https://github.com/vhspace/ipa-mcp/commit/bb10cc891a3a0800292d6e38621a09fbb084871c))


## v0.3.1 (2026-04-07)

### Chores

- Release v0.3.1
  ([`d60e093`](https://github.com/vhspace/ipa-mcp/commit/d60e093f635f8cd7907eef87d01e9cc0dc9eec11))

### Features

- Add setup hook and SKILL.md for CLI discoverability
  ([#5](https://github.com/vhspace/ipa-mcp/pull/5),
  [`0a8fc89`](https://github.com/vhspace/ipa-mcp/commit/0a8fc89859565e0f8e6475f79c034de82cb97d29))


## v0.2.0 (2026-03-13)

### Bug Fixes

- Address all code review findings (critical, high, medium)
  ([`d0d215b`](https://github.com/vhspace/ipa-mcp/commit/d0d215b64bb3bd65fd74057aa60786e12cd04c62))


## v0.1.1 (2026-03-13)

### Documentation

- Align README, SKILL, and project structure with other MCPs
  ([`4a0a783`](https://github.com/vhspace/ipa-mcp/commit/4a0a7836e59c2a77b6ba8b5846476681957c4658))


## v0.1.0 (2026-03-13)

- Initial Release
