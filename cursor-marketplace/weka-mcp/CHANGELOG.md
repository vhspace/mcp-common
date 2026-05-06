# CHANGELOG

<!-- version list -->

## v1.0.1 (2026-04-17)

### Bug Fixes

- Add version scope, hosted-cluster safety warning, and trim UNKNOWN nodes docs
  ([#22](https://github.com/vhspace/weka-mcp/pull/22),
  [`2941c82`](https://github.com/vhspace/weka-mcp/commit/2941c823f2eb73f9cb758392c32b0cab3d8268cf))

- Document that weka local status UNKNOWN nodes are normal with org-scoped creds
  ([#22](https://github.com/vhspace/weka-mcp/pull/22),
  [`2941c82`](https://github.com/vhspace/weka-mcp/commit/2941c823f2eb73f9cb758392c32b0cab3d8268cf))

- Use correct weka local setup client pattern for hosted clusters
  ([#22](https://github.com/vhspace/weka-mcp/pull/22),
  [`2941c82`](https://github.com/vhspace/weka-mcp/commit/2941c823f2eb73f9cb758392c32b0cab3d8268cf))

### Chores

- Update release badge to v1.0.0
  ([`8e9cb39`](https://github.com/vhspace/weka-mcp/commit/8e9cb396132429906e22f4df45ffb72ae36a2474))

### Continuous Integration

- Notify mcp-common marketplace on release
  ([`b159ee6`](https://github.com/vhspace/weka-mcp/commit/b159ee67d5f2dcc3db7a2d63c7bf447991d85ae0))


## v1.0.0 (2026-04-09)

### Bug Fixes

- Remove unsupported hooks field from Claude plugin manifest
  ([`f48a3ea`](https://github.com/vhspace/weka-mcp/commit/f48a3ea251cfb45cf90ca5a0646504489dd27df0))

- Resolve ruff N806 lint errors and reformat
  ([`f78defe`](https://github.com/vhspace/weka-mcp/commit/f78defe16b86c7d0a9a91fd30ffe0dc6b6216085))

### Chores

- Migrate plugin version source to pyproject
  ([`c271c53`](https://github.com/vhspace/weka-mcp/commit/c271c53885ea124406029c43a46b15033cd39d88))

### Continuous Integration

- Add repo-local auto-updated release badge
  ([`8a8d72e`](https://github.com/vhspace/weka-mcp/commit/8a8d72eda7abc3b10d86743af8237f72045e835e))

- Trigger release workflow on push to main
  ([`b156368`](https://github.com/vhspace/weka-mcp/commit/b156368174060530066f3bb610337292b40739f6))

### Features

- Add Claude marketplace registry entry artifact
  ([`d40dfdd`](https://github.com/vhspace/weka-mcp/commit/d40dfddc310d5cadcd2e990e61fca01af7ad6c00))

- Agent-friendly summaries and response unwrapping
  ([#13](https://github.com/vhspace/weka-mcp/pull/13),
  [`23698ab`](https://github.com/vhspace/weka-mcp/commit/23698ab07f88e98671fd967bb8814680c79f4aec))

- Use pinned git+https server source and fix release workflow
  ([`a373ef9`](https://github.com/vhspace/weka-mcp/commit/a373ef9aeac3ca83a5925e04554ec8b58c428ac9))


## v0.6.0 (2026-04-07)

### Bug Fixes

- Bump mcp-common to v0.6.1 (sync/async wrapper fix)
  ([`ed20b17`](https://github.com/vhspace/weka-mcp/commit/ed20b177ec052755e35fc8235b4f21c88604d68c))

### Chores

- Release v0.6.0
  ([`7b1a7bc`](https://github.com/vhspace/weka-mcp/commit/7b1a7bc5ad65b54b480090f2762a13db0ca0dc3e))

### Features

- Wire mcp-common agent remediation and SSL warning suppression
  ([`c6ce2a6`](https://github.com/vhspace/weka-mcp/commit/c6ce2a691420aa26e4b12f2314d326796575e496))


## v0.5.0 (2026-03-20)

### Chores

- Release v0.5.0
  ([`7565b16`](https://github.com/vhspace/weka-mcp/commit/7565b16ecff88f026f2de78f692eea4a28636986))

### Features

- Add multi-site support for managing multiple Weka clusters
  ([#12](https://github.com/vhspace/weka-mcp/pull/12),
  [`8b0568e`](https://github.com/vhspace/weka-mcp/commit/8b0568eed392b15bea5292b4d4828fdd49b14502))


## v0.4.0 (2026-03-20)

### Chores

- Release v0.4.0
  ([`4d1d9db`](https://github.com/vhspace/weka-mcp/commit/4d1d9db8158ab4149939953f637706cf66ea6a97))

### Features

- Achieve 1:1 MCP-CLI parity and add comprehensive CLI tests
  ([#10](https://github.com/vhspace/weka-mcp/pull/10),
  [`33a71b9`](https://github.com/vhspace/weka-mcp/commit/33a71b981e996fd8601f61a96413c6496be0d2dc))

- Add dedicated read-only tools and CLI commands for all resource types
  ([#10](https://github.com/vhspace/weka-mcp/pull/10),
  [`33a71b9`](https://github.com/vhspace/weka-mcp/commit/33a71b981e996fd8601f61a96413c6496be0d2dc))

- Add org quota update and filesystem resize tools
  ([#10](https://github.com/vhspace/weka-mcp/pull/10),
  [`33a71b9`](https://github.com/vhspace/weka-mcp/commit/33a71b981e996fd8601f61a96413c6496be0d2dc))

- Org management, filesystem ops, and dedicated read-only tools
  ([#10](https://github.com/vhspace/weka-mcp/pull/10),
  [`33a71b9`](https://github.com/vhspace/weka-mcp/commit/33a71b981e996fd8601f61a96413c6496be0d2dc))


## v0.3.5 (2026-03-13)

### Bug Fixes

- Remove invalid org_uid from create-user, users inherit session org
  ([`fd14673`](https://github.com/vhspace/weka-mcp/commit/fd146732fcefff79c07477f2012172fd9e224bb1))


## v0.3.4 (2026-03-13)

### Bug Fixes

- Use total_capacity (bytes) instead of capacity for filesystem creation
  ([`b015504`](https://github.com/vhspace/weka-mcp/commit/b01550415211b0cfd62989bb1a358015d688f6c3))


## v0.3.3 (2026-03-13)

### Bug Fixes

- Convert org quota from GB to bytes for Weka API
  ([`eacb55a`](https://github.com/vhspace/weka-mcp/commit/eacb55ab5023fb4695979799194fa0b13237bc4e))


## v0.3.2 (2026-03-13)

### Bug Fixes

- Include username/password in create-org payload
  ([`afc38cb`](https://github.com/vhspace/weka-mcp/commit/afc38cb39fa06e105a8a2e011753d2da91b566ef))


## v0.3.1 (2026-03-13)

### Bug Fixes

- Handle dict-wrapped auth tokens in Weka login response
  ([`97144be`](https://github.com/vhspace/weka-mcp/commit/97144be4d5568d1ec2b2c3bd96f7c48c0c536309))


## v0.3.0 (2026-03-13)

### Chores

- Release v0.3.0
  ([`16ec1cf`](https://github.com/vhspace/weka-mcp/commit/16ec1cfaa2c25ab5d0d05774c45163c07ebc9427))

### Features

- Add org login, create org/user/fsgroup tools ([#9](https://github.com/vhspace/weka-mcp/pull/9),
  [`c12762e`](https://github.com/vhspace/weka-mcp/commit/c12762e0d275faff00a554e2925bc91cfaa40efc))


## v0.2.3 (2026-03-11)

### Bug Fixes

- Skill guidance + hook WORKSPACE_ROOT fix ([#8](https://github.com/vhspace/weka-mcp/pull/8),
  [`9881220`](https://github.com/vhspace/weka-mcp/commit/9881220bb214312a69a2c1bb94b91abfbbee1df6))

### Chores

- Release v0.2.3
  ([`a5b2b2f`](https://github.com/vhspace/weka-mcp/commit/a5b2b2ff651da2cd12b7ab6dfa9b3c5094bcc512))


## v0.2.2 (2026-03-11)

### Chores

- Release v0.2.2
  ([`e1b2a14`](https://github.com/vhspace/weka-mcp/commit/e1b2a14febb0a350601051c3e36a70e9f0f739a5))

### Features

- Add mcp-plugin.toml and pre-commit hook for plugin generation
  ([#7](https://github.com/vhspace/weka-mcp/pull/7),
  [`6c1203e`](https://github.com/vhspace/weka-mcp/commit/6c1203ebfb59bc73bc048a5942ed6678be5aecea))


## v0.2.1 (2026-03-11)

### Chores

- Release v0.2.1
  ([`acca519`](https://github.com/vhspace/weka-mcp/commit/acca519bc1c3d6fbda00be81634362e430e393cf))

### Features

- Add SessionStart hooks for automatic CLI setup ([#6](https://github.com/vhspace/weka-mcp/pull/6),
  [`9d974d5`](https://github.com/vhspace/weka-mcp/commit/9d974d57f8e0200f4807df4fecd9bbecead899bf))


## v0.2.0 (2026-03-11)

### Chores

- Release v0.2.0
  ([`e15439d`](https://github.com/vhspace/weka-mcp/commit/e15439da56b7b205b1ad10f0b1e310aa06449ead))

### Features

- Add weka-cli companion CLI for token-efficient agent workflows
  ([#5](https://github.com/vhspace/weka-mcp/pull/5),
  [`f7b291a`](https://github.com/vhspace/weka-mcp/commit/f7b291af7091beb8a9be887c7be6d8eb9e48dafd))


## v0.1.2 (2026-03-10)

### Chores

- Release v0.1.2
  ([`789c8e8`](https://github.com/vhspace/weka-mcp/commit/789c8e805942a0631360ec4ad83a2a5dd018f8b1))

### Features

- Add production HTTP transport, Helm chart, and health endpoint
  ([`a0e38cd`](https://github.com/vhspace/weka-mcp/commit/a0e38cde58f612b0f0d5403896200ea9af2fea2f))


## v0.1.1 (2026-03-06)

### Chores

- Release v0.1.1
  ([`4a36494`](https://github.com/vhspace/weka-mcp/commit/4a36494b4d23c4f1bb31fa50e64ab988c2ce2ca2))

### Features

- Add mcpServers config, skills, and fix plugin metadata
  ([`b2fab89`](https://github.com/vhspace/weka-mcp/commit/b2fab89e2f87f69bc277368fc06c1a1d7425e6af))


## v0.1.0 (2026-03-05)

- Initial Release
