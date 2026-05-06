# CHANGELOG

<!-- version list -->

## Unreleased

### Bug Fixes

- Use Freshdesk REST API for IREN `list_tickets` and `get_ticket` operations
  instead of brittle Playwright browser scraping
  ([#59](https://github.com/vhspace/dc-support-mcp/issues/59),
  [#60](https://github.com/vhspace/dc-support-mcp/issues/60))
  - `list_tickets` now respects `--limit` and `--status` parameters and
    paginates beyond the first page
  - `get_ticket` now returns populated `created`, `assignee`, and `comments`
    fields via the ticket and conversations API
  - Browser scraping preserved as automatic fallback when the API is unavailable


## v1.12.0 (2026-04-24)

### Chores

- Bump mcp-common to v0.8.0 ([#38](https://github.com/vhspace/dc-support-mcp/pull/38),
  [`e4126e5`](https://github.com/vhspace/dc-support-mcp/commit/e4126e5bd08a827bec90d5be20bc692773ab9b1a))

- Bump mcp-common to v0.8.0 (#38) ([#49](https://github.com/vhspace/dc-support-mcp/pull/49),
  [`cc19506`](https://github.com/vhspace/dc-support-mcp/commit/cc195068fed20c377cebc131d6add717f626df84))

- Update release badge to v1.11.1
  ([`cea4164`](https://github.com/vhspace/dc-support-mcp/commit/cea4164b3443c2657fc2ae28f7b6b4e3e0d236be))

### Features

- Add set_node_active RTB API support (issue #51)
  ([`1bef475`](https://github.com/vhspace/dc-support-mcp/commit/1bef475461a559b29ec301d707cf0a048c1dc805))


## v1.11.1 (2026-04-23)

### Bug Fixes

- Detect ORI login failures and logged-out API responses (#24)
  ([#48](https://github.com/vhspace/dc-support-mcp/pull/48),
  [`18997ad`](https://github.com/vhspace/dc-support-mcp/commit/18997ada3fb15cb2eb9001d04b340d8c342bd3aa))

### Chores

- Update release badge to v1.11.0
  ([`6c5494e`](https://github.com/vhspace/dc-support-mcp/commit/6c5494ea5276495b8fe362707ea87e1b21f19ede))


## v1.11.0 (2026-04-23)

### Chores

- Update release badge to v1.10.0
  ([`f5d3075`](https://github.com/vhspace/dc-support-mcp/commit/f5d307592783164bb8cf4ce165924eaf75378205))

### Features

- Add ORI ticket status updates and CLI update-ticket command (#20)
  ([#47](https://github.com/vhspace/dc-support-mcp/pull/47),
  [`b2938df`](https://github.com/vhspace/dc-support-mcp/commit/b2938dfba9db0352ebcd457938cdcaf14431b5f1))

- Add RTB outage type enum, local validation, and --list-outage-types (#39)
  ([#46](https://github.com/vhspace/dc-support-mcp/pull/46),
  [`26ab05c`](https://github.com/vhspace/dc-support-mcp/commit/26ab05cf8696085570856c553cf79afdf3b770a2))


## v1.10.0 (2026-04-23)

### Chores

- Update release badge to v1.9.3
  ([`8f4538a`](https://github.com/vhspace/dc-support-mcp/commit/8f4538a02550eb0c063c5462f0860703b017154d))

### Features

- Improve KB article fetching with direct access, deeper discovery, and attachment support (#34)
  ([#45](https://github.com/vhspace/dc-support-mcp/pull/45),
  [`5c7a9c3`](https://github.com/vhspace/dc-support-mcp/commit/5c7a9c3d2ff7975826aeffa26fbc9dd5dc7ecc4b))


## v1.9.3 (2026-04-23)

### Bug Fixes

- Pass explicit assignee to RTB triage API and expose via CLI/MCP
  ([#44](https://github.com/vhspace/dc-support-mcp/pull/44),
  [`36a0704`](https://github.com/vhspace/dc-support-mcp/commit/36a070482c06d6ca9f0cd3cf7e0a95663c5982dd))

- Pass explicit assignee to RTB triage API and expose via CLI/MCP (#19)
  ([#44](https://github.com/vhspace/dc-support-mcp/pull/44),
  [`36a0704`](https://github.com/vhspace/dc-support-mcp/commit/36a070482c06d6ca9f0cd3cf7e0a95663c5982dd))

- Review findings ([#44](https://github.com/vhspace/dc-support-mcp/pull/44),
  [`36a0704`](https://github.com/vhspace/dc-support-mcp/commit/36a070482c06d6ca9f0cd3cf7e0a95663c5982dd))

### Chores

- Update release badge to v1.9.2
  ([`dfc032e`](https://github.com/vhspace/dc-support-mcp/commit/dfc032e47d1e282d4a9651cf6e0aa90e11fcc1e4))


## v1.9.2 (2026-04-23)

### Bug Fixes

- Surface error details when create-service-request fails
  ([#43](https://github.com/vhspace/dc-support-mcp/pull/43),
  [`1769b8e`](https://github.com/vhspace/dc-support-mcp/commit/1769b8e687d200a8634dc9da03521eccd31a5f35))

### Chores

- Update release badge to v1.9.1
  ([`1d52dc9`](https://github.com/vhspace/dc-support-mcp/commit/1d52dc9907925a28383fa8d9f1bd5e4a856aea89))


## v1.9.1 (2026-04-23)

### Bug Fixes

- Apply JSON error output to triage command error paths
  ([#41](https://github.com/vhspace/dc-support-mcp/pull/41),
  [`dca3486`](https://github.com/vhspace/dc-support-mcp/commit/dca3486cfb5aeab9cd13a59b47f9ec1950a2c2e9))

- Resolve empty ticket list truthiness bug and add auth diagnostics
  ([#42](https://github.com/vhspace/dc-support-mcp/pull/42),
  [`3d1d348`](https://github.com/vhspace/dc-support-mcp/commit/3d1d348f48bc03fc10551a848c876477f82aad53))

- Respect --json flag on empty/error results in all CLI commands
  ([#41](https://github.com/vhspace/dc-support-mcp/pull/41),
  [`dca3486`](https://github.com/vhspace/dc-support-mcp/commit/dca3486cfb5aeab9cd13a59b47f9ec1950a2c2e9))

### Chores

- Gitignore worktrees directory
  ([`3a7aa47`](https://github.com/vhspace/dc-support-mcp/commit/3a7aa474db4a47272b974a9f9a8811fa38c0d55d))

### Documentation

- Add auth-status and --verbose to skill files
  ([#42](https://github.com/vhspace/dc-support-mcp/pull/42),
  [`3d1d348`](https://github.com/vhspace/dc-support-mcp/commit/3d1d348f48bc03fc10551a848c876477f82aad53))


## v1.9.0 (2026-04-19)

### Bug Fixes

- Prevent account lockout from excessive Playwright logins
  ([#36](https://github.com/vhspace/dc-support-mcp/issues/36)):
  - 5-minute auth cooldown persisted to disk for cross-process CLI protection
  - Proactive session probe on cookie load (skipped for cookies <1h old)
  - Sliding-window cookie timestamp refresh on successful API calls
  - Vendor-specific file locking on cookie I/O (degrades on non-POSIX)
  - Atomic cookie writes (temp file + os.replace) to prevent corruption
  - `COOKIE_MAX_AGE` increased from 2h to 8h to match Atlassian session TTL


## v1.8.1 (2026-04-14)

### Bug Fixes

- Increase timeout for Atlassian two-step login password field
  ([#33](https://github.com/vhspace/dc-support-mcp/pull/33),
  [`f56ca33`](https://github.com/vhspace/dc-support-mcp/commit/f56ca3354c5ad672afef336a55ddae47b9eb46bd))

### Chores

- Update release badge to v1.8.0
  ([`eccae0f`](https://github.com/vhspace/dc-support-mcp/commit/eccae0fd8d1a1379772d327d6f06a961c70223b6))

### Continuous Integration

- Notify mcp-common marketplace on release
  ([`b096d6c`](https://github.com/vhspace/dc-support-mcp/commit/b096d6c2e0c78efcbba268aa281a933363d1d8b7))


## v1.8.0 (2026-04-09)

### Bug Fixes

- Remove unsupported hooks field from Claude plugin manifest
  ([`7d289ab`](https://github.com/vhspace/dc-support-mcp/commit/7d289ab7c70f82e6d8b74389d460013da1af0fd7))

- Resolve mypy no-any-return errors and apply ruff formatting
  ([`1c06603`](https://github.com/vhspace/dc-support-mcp/commit/1c06603a19899e57b3a361c85e60cddea8c35bab))

### Chores

- Migrate plugin version source to pyproject
  ([`ff78f79`](https://github.com/vhspace/dc-support-mcp/commit/ff78f794cc832fe975be7f232e947d03983031bf))

### Continuous Integration

- Add repo-local auto-updated release badge
  ([`347e8df`](https://github.com/vhspace/dc-support-mcp/commit/347e8dfb643edddc70a37306dd4fbb1e36d6674a))

- Trigger release workflow on push to main
  ([`1f43a8e`](https://github.com/vhspace/dc-support-mcp/commit/1f43a8e3d36d0de743185883abd622ef8fecaf28))

### Documentation

- Add release version badge to README
  ([`955ecd6`](https://github.com/vhspace/dc-support-mcp/commit/955ecd6fc7022c962cce8976352480812aaf4264))

### Features

- Add Claude marketplace registry entry artifact
  ([`73ce593`](https://github.com/vhspace/dc-support-mcp/commit/73ce5931e3cbefe375b62bf12895d7c1d735ed2c))

- Use pinned git+https server source and remove smoke gate
  ([`f21fb15`](https://github.com/vhspace/dc-support-mcp/commit/f21fb15d2b4147cd54050d546db3b1d8b01184de))

## v1.0.0 (2026-03-13)

### Bug Fixes

- Route ORI ticket creation through Playwright instead of REST API
  ([#14](https://github.com/vhspace/dc-support-mcp/pull/14),
  [`00d937a`](https://github.com/vhspace/dc-support-mcp/commit/00d937a7afac4a0f5f3e97bc06169142b28e2b02))


## v0.4.3 (2026-03-11)

### Bug Fixes

- Skill guidance + hook WORKSPACE_ROOT fix
  ([#13](https://github.com/vhspace/dc-support-mcp/pull/13),
  [`aed8a23`](https://github.com/vhspace/dc-support-mcp/commit/aed8a23ef285c73c07c2fe073b0a3f5f179ec5cd))

### Chores

- Release v0.4.3
  ([`fa6e920`](https://github.com/vhspace/dc-support-mcp/commit/fa6e920881d88b79e2f50c5cfcec41b3f9ecec4a))


## v0.4.2 (2026-03-11)

### Chores

- Release v0.4.2
  ([`273e4e9`](https://github.com/vhspace/dc-support-mcp/commit/273e4e97bebf2b39b827dfb28e0050146fab61ca))

### Features

- Add mcp-plugin.toml and pre-commit hook for plugin generation
  ([#12](https://github.com/vhspace/dc-support-mcp/pull/12),
  [`f430598`](https://github.com/vhspace/dc-support-mcp/commit/f430598201c1c49d30c58fb7a328fb515538ab76))


## v0.4.1 (2026-03-11)

### Chores

- Release v0.4.1
  ([`4759caf`](https://github.com/vhspace/dc-support-mcp/commit/4759caf9c434196f6eb883f3c54402cd968e089e))

### Features

- Add SessionStart hooks for automatic CLI setup
  ([#11](https://github.com/vhspace/dc-support-mcp/pull/11),
  [`4969f89`](https://github.com/vhspace/dc-support-mcp/commit/4969f890299b88abeb933135dbc93f751664cfcd))


## v0.4.0 (2026-03-11)

### Chores

- Release v0.4.0
  ([`ab1a14d`](https://github.com/vhspace/dc-support-mcp/commit/ab1a14d68e15f726ebff819ed4ca23aef12d84f4))


## v0.2.0 (2026-03-11)

### Chores

- Release v0.2.0
  ([`4d0ccfd`](https://github.com/vhspace/dc-support-mcp/commit/4d0ccfdf89588edd89e4d8d11fbf1d08712cf7c5))

### Features

- Add dc-support-cli companion CLI for token-efficient agent workflows
  ([#10](https://github.com/vhspace/dc-support-mcp/pull/10),
  [`9a9d50a`](https://github.com/vhspace/dc-support-mcp/commit/9a9d50a3a3f8f6879788b259dabe5354e60318ca))


## v0.3.0 (2026-03-10)

### Features

- Add alert silencing tool and auto-suggest in triage workflow
  ([#9](https://github.com/vhspace/dc-support-mcp/pull/9),
  [`e9ff6b8`](https://github.com/vhspace/dc-support-mcp/commit/e9ff6b86e95817688659bf60f849e14e17739e69))

- Add Hypertec vendor, RTB integration, and content sanitization
  ([#8](https://github.com/vhspace/dc-support-mcp/pull/8),
  [`e5c55a5`](https://github.com/vhspace/dc-support-mcp/commit/e5c55a5a24eeb9973a0f14ef89b10031b274916a))


## v0.1.3 (2026-03-10)

### Chores

- Release v0.1.3
  ([`4972e5e`](https://github.com/vhspace/dc-support-mcp/commit/4972e5ec4f7dfaacd31837ca9cb50256cad86048))

### Continuous Integration

- Update branch triggers from master to main
  ([`33db84d`](https://github.com/vhspace/dc-support-mcp/commit/33db84d3fd381d4aa5b55540c0cc9bddc8722700))


## v0.1.2 (2026-03-07)

### Bug Fixes

- Update IREN Freshdesk selectors for ticket list, detail, and login
  ([`4ee5812`](https://github.com/vhspace/dc-support-mcp/commit/4ee58124603b46cce12c26eedc94be413fb191a3))

### Chores

- Release v0.1.2
  ([`1ad7e42`](https://github.com/vhspace/dc-support-mcp/commit/1ad7e42a3678448a786008218b8adc4a656ca37a))


## v0.1.1 (2026-03-06)

### Bug Fixes

- Set semantic-release branch to master
  ([`8601921`](https://github.com/vhspace/dc-support-mcp/commit/8601921953cf0ef78c3f5406ca3faa9ea2d255db))

### Chores

- Release v0.1.1
  ([`6a89b14`](https://github.com/vhspace/dc-support-mcp/commit/6a89b14e553abc8f06c13b8650d147090d6ee9a4))

### Features

- Add mcpServers config, skills, and fix plugin metadata
  ([`a76d822`](https://github.com/vhspace/dc-support-mcp/commit/a76d8224d3d6a2c1b6fa290c8ab7768aee902045))


## v0.1.0 (2026-03-05)

- Initial Release
