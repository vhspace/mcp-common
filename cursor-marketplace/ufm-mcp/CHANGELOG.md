# CHANGELOG

<!-- version list -->

## v1.7.0 (2026-05-02)

### Chores

- Bump mcp-common to v0.8.0 (#24) ([#47](https://github.com/vhspace/ufm-mcp/pull/47),
  [`e87f61a`](https://github.com/vhspace/ufm-mcp/commit/e87f61a8cdb088c82dbec04d61f1d23fe11d6332))

- Update release badge to v1.6.0
  ([`6d4f1ea`](https://github.com/vhspace/ufm-mcp/commit/6d4f1ea077efd8aca30f61bf1afa06c62a89b960))

### Continuous Integration

- Gate marketplace notify step on PAT being configured (closes #60)
  ([#66](https://github.com/vhspace/ufm-mcp/pull/66),
  [`ef7cef8`](https://github.com/vhspace/ufm-mcp/commit/ef7cef8b3303c19774d83045b5ec9449dc073d39))

### Features

- UploadIbdiagnet tool/CLI + host-ibdiagnet-collect skill (#57, #58)
  ([#67](https://github.com/vhspace/ufm-mcp/pull/67),
  [`2f112e2`](https://github.com/vhspace/ufm-mcp/commit/2f112e2af7011d17e0fbed3aa470648a1d6fab96))


## v1.6.0 (2026-04-23)

### Chores

- Update release badge to v1.5.0
  ([`13d8f27`](https://github.com/vhspace/ufm-mcp/commit/13d8f27b7ed9a832c332ff01e586bffcba75aefd))

### Features

- Add ufm-opensm-restart skill for safe SM recovery (#23)
  ([#46](https://github.com/vhspace/ufm-mcp/pull/46),
  [`d8eb8cc`](https://github.com/vhspace/ufm-mcp/commit/d8eb8cc5703f7a4e7467afc77edc71564b3fe715))


## v1.5.0 (2026-04-23)

### Chores

- Update release badge to v1.4.0
  ([`f870e5e`](https://github.com/vhspace/ufm-mcp/commit/f870e5ed59bd43ab37b06bc4b87e4a647c6695b6))

### Features

- Add FEC counters, remote node info, and error counters to ports output (#33)
  ([#45](https://github.com/vhspace/ufm-mcp/pull/45),
  [`fd1ff72`](https://github.com/vhspace/ufm-mcp/commit/fd1ff72da94aa7a4336c9bbfaa3480d1607aa4bd))


## v1.4.0 (2026-04-23)

### Chores

- Update release badge to v1.3.0
  ([`a3ba586`](https://github.com/vhspace/ufm-mcp/commit/a3ba5862903c66b8e38cb4077ed19762dbc68429))

### Features

- Add switches command to list all switches with health summary (#35)
  ([#44](https://github.com/vhspace/ufm-mcp/pull/44),
  [`8e4fb57`](https://github.com/vhspace/ufm-mcp/commit/8e4fb573fa59f26eabbca7cd8dc90e6a44746938))


## v1.3.0 (2026-04-23)

### Chores

- Update release badge to v1.2.3
  ([`8518ef0`](https://github.com/vhspace/ufm-mcp/commit/8518ef028733e94f4382ad6776433df39f413fa4))

### Features

- Make port_numbers optional in ports command (#32)
  ([#43](https://github.com/vhspace/ufm-mcp/pull/43),
  [`156ea24`](https://github.com/vhspace/ufm-mcp/commit/156ea249692baafa0f54c2e1aebd993eb49fab10))


## v1.2.3 (2026-04-23)

### Bug Fixes

- Deduplicate repeated log lines in concerns output
  ([#41](https://github.com/vhspace/ufm-mcp/pull/41),
  [`dd08677`](https://github.com/vhspace/ufm-mcp/commit/dd08677273051ffd3a62d2beaaa3d4acd3b687fa))

- Deduplicate repeated log lines in concerns output (#36)
  ([#41](https://github.com/vhspace/ufm-mcp/pull/41),
  [`dd08677`](https://github.com/vhspace/ufm-mcp/commit/dd08677273051ffd3a62d2beaaa3d4acd3b687fa))

- Resolve opaque GUIDs to hostnames in alarms output
  ([#42](https://github.com/vhspace/ufm-mcp/pull/42),
  [`a6248e8`](https://github.com/vhspace/ufm-mcp/commit/a6248e8e87da57fb156e6d7132a4a9e76dd93cc9))

- Resolve opaque GUIDs to hostnames in alarms output (#34)
  ([#42](https://github.com/vhspace/ufm-mcp/pull/42),
  [`a6248e8`](https://github.com/vhspace/ufm-mcp/commit/a6248e8e87da57fb156e6d7132a4a9e76dd93cc9))

- Review findings ([#42](https://github.com/vhspace/ufm-mcp/pull/42),
  [`a6248e8`](https://github.com/vhspace/ufm-mcp/commit/a6248e8e87da57fb156e6d7132a4a9e76dd93cc9))

- Review findings ([#41](https://github.com/vhspace/ufm-mcp/pull/41),
  [`dd08677`](https://github.com/vhspace/ufm-mcp/commit/dd08677273051ffd3a62d2beaaa3d4acd3b687fa))

- Review findings ([#40](https://github.com/vhspace/ufm-mcp/pull/40),
  [`9a3b7a4`](https://github.com/vhspace/ufm-mcp/commit/9a3b7a4b91cacfd9020275eee05a5e10c81eb5c4))

- Unhealthy command returns empty output with no context
  ([#40](https://github.com/vhspace/ufm-mcp/pull/40),
  [`9a3b7a4`](https://github.com/vhspace/ufm-mcp/commit/9a3b7a4b91cacfd9020275eee05a5e10c81eb5c4))

- Unhealthy command returns empty output with no context (#37)
  ([#40](https://github.com/vhspace/ufm-mcp/pull/40),
  [`9a3b7a4`](https://github.com/vhspace/ufm-mcp/commit/9a3b7a4b91cacfd9020275eee05a5e10c81eb5c4))

### Chores

- Update release badge to v1.2.2
  ([`f95e86e`](https://github.com/vhspace/ufm-mcp/commit/f95e86ec404d07ee02dce22d2b2c19525afdd786))


## v1.2.2 (2026-04-23)

### Bug Fixes

- Cache Settings instance in CLI to avoid redundant env parsing
  ([#39](https://github.com/vhspace/ufm-mcp/pull/39),
  [`298b98d`](https://github.com/vhspace/ufm-mcp/commit/298b98da9ccab76d458e1178b6655142bf2a2446))

### Chores

- Update release badge to v1.2.1
  ([`6967448`](https://github.com/vhspace/ufm-mcp/commit/696744814d2324f500cba7d5a9bdb8e3d8d0f73d))


## v1.2.1 (2026-04-23)

### Bug Fixes

- Topaz CLI commands crash -- read topaz_az_map from Settings not SiteConfig (#31)
  ([#38](https://github.com/vhspace/ufm-mcp/pull/38),
  [`fec02e6`](https://github.com/vhspace/ufm-mcp/commit/fec02e68b383858f1aa66e094e2f7d57185b0263))

### Chores

- Update release badge to v1.2.0
  ([`6ea5a4f`](https://github.com/vhspace/ufm-mcp/commit/6ea5a4f5f44209e21e411e5f1686e77a64b9a48b))


## v1.2.0 (2026-04-23)

### Bug Fixes

- Make Topaz imports lazy to avoid startup failures without gRPC
  ([#30](https://github.com/vhspace/ufm-mcp/pull/30),
  [`fc1a350`](https://github.com/vhspace/ufm-mcp/commit/fc1a350e442e18d83a26c3dff39ce7c52e99b638))

- Review findings -- dep versions, expose ListSwitches, cap ports, fix test, update skills
  ([#30](https://github.com/vhspace/ufm-mcp/pull/30),
  [`fc1a350`](https://github.com/vhspace/ufm-mcp/commit/fc1a350e442e18d83a26c3dff39ce7c52e99b638))

### Chores

- Update release badge to v1.1.0
  ([`126adcd`](https://github.com/vhspace/ufm-mcp/commit/126adcdcbd83467adb4a94c00903dd764d4dcf8f))

### Features

- Add Topaz fabric health integration ([#30](https://github.com/vhspace/ufm-mcp/pull/30),
  [`fc1a350`](https://github.com/vhspace/ufm-mcp/commit/fc1a350e442e18d83a26c3dff39ce7c52e99b638))

- Add Topaz fabric health integration (#25) ([#30](https://github.com/vhspace/ufm-mcp/pull/30),
  [`fc1a350`](https://github.com/vhspace/ufm-mcp/commit/fc1a350e442e18d83a26c3dff39ce7c52e99b638))


## v1.1.0 (2026-04-23)

### Bug Fixes

- Extract pkey body helper, correct hosts_added count
  ([#28](https://github.com/vhspace/ufm-mcp/pull/28),
  [`9a77457`](https://github.com/vhspace/ufm-mcp/commit/9a77457a7b0456302b7d9e6a0307258e37233368))

- Strip additionalProperties from pkey-add-hosts and add GUID fallback
  ([#28](https://github.com/vhspace/ufm-mcp/pull/28),
  [`9a77457`](https://github.com/vhspace/ufm-mcp/commit/9a77457a7b0456302b7d9e6a0307258e37233368))

- Strip additionalProperties from pkey-add-hosts and add GUID fallback (#26)
  ([#28](https://github.com/vhspace/ufm-mcp/pull/28),
  [`9a77457`](https://github.com/vhspace/ufm-mcp/commit/9a77457a7b0456302b7d9e6a0307258e37233368))

- Warn about unhandled removals in pkey-diff --apply, update skills
  ([#29](https://github.com/vhspace/ufm-mcp/pull/29),
  [`a4cf6d8`](https://github.com/vhspace/ufm-mcp/commit/a4cf6d88ebc8d19249707c09ee5a34f39108134c))

### Chores

- Update release badge to v1.0.1
  ([`7677e28`](https://github.com/vhspace/ufm-mcp/commit/7677e280f1a936dc2b0a96c61b2397407f0fa31f))

### Features

- Add pkey-diff command for reconciliation workflows
  ([#29](https://github.com/vhspace/ufm-mcp/pull/29),
  [`a4cf6d8`](https://github.com/vhspace/ufm-mcp/commit/a4cf6d88ebc8d19249707c09ee5a34f39108134c))

- Add pkey-diff command for reconciliation workflows (#27)
  ([#29](https://github.com/vhspace/ufm-mcp/pull/29),
  [`a4cf6d8`](https://github.com/vhspace/ufm-mcp/commit/a4cf6d88ebc8d19249707c09ee5a34f39108134c))


## v1.0.1 (2026-04-17)

### Bug Fixes

- Add compatibility fallback for pkey host add schema errors
  ([#18](https://github.com/vhspace/ufm-mcp/pull/18),
  [`5d68983`](https://github.com/vhspace/ufm-mcp/commit/5d689833309845e783462b780f3d4e79d05db32a))

- **pkey**: Clarify fallback metadata, hints, and error parsing
  ([#18](https://github.com/vhspace/ufm-mcp/pull/18),
  [`5d68983`](https://github.com/vhspace/ufm-mcp/commit/5d689833309845e783462b780f3d4e79d05db32a))

### Chores

- Migrate plugin version source to pyproject ([#18](https://github.com/vhspace/ufm-mcp/pull/18),
  [`5d68983`](https://github.com/vhspace/ufm-mcp/commit/5d689833309845e783462b780f3d4e79d05db32a))

- Update release badge to v1.0.0
  ([`4181ad8`](https://github.com/vhspace/ufm-mcp/commit/4181ad887e2463f7870bbb634f51aa2717db622c))

### Continuous Integration

- Notify mcp-common marketplace on release
  ([`3d80864`](https://github.com/vhspace/ufm-mcp/commit/3d80864e22376cadd6838427a4df59cb6bbd3575))

- Use liveness probe for conformance health check
  ([#18](https://github.com/vhspace/ufm-mcp/pull/18),
  [`5d68983`](https://github.com/vhspace/ufm-mcp/commit/5d689833309845e783462b780f3d4e79d05db32a))


## v1.0.0 (2026-04-09)

### Bug Fixes

- Remove unsupported hooks field from Claude plugin manifest
  ([`964a1ae`](https://github.com/vhspace/ufm-mcp/commit/964a1aeb555cb83d95b3da654127de9d60fef18e))

### Chores

- Migrate plugin version source to pyproject
  ([`598a175`](https://github.com/vhspace/ufm-mcp/commit/598a1757f7be223574631c2e0e7c14f4e41330ea))

### Continuous Integration

- Add repo-local auto-updated release badge
  ([`dc7db57`](https://github.com/vhspace/ufm-mcp/commit/dc7db57698f65359aee52091ee838c122e611721))

- Trigger release workflow on push to main
  ([`1d6311b`](https://github.com/vhspace/ufm-mcp/commit/1d6311bb8b5977108db67ad8fabe1ffb499e1d70))

### Features

- Add Claude marketplace registry entry artifact
  ([`cf253a2`](https://github.com/vhspace/ufm-mcp/commit/cf253a20456f92b8890490d3926cab51ee447a05))

- Use pinned git+https server source and fix release workflow
  ([`33614ac`](https://github.com/vhspace/ufm-mcp/commit/33614ac28602db03e9e91a6f83cdd4ff7de07f5b))


## v0.4.1 (2026-04-08)

### Bug Fixes

- Load .env in CLI so multi-site config works without shell exports
  ([#15](https://github.com/vhspace/ufm-mcp/pull/15),
  [`acb28d8`](https://github.com/vhspace/ufm-mcp/commit/acb28d809b1b5d6104a42a493cca676443f8c533))

- Resolve ruff lint errors and reformat for CI
  ([`0f68ec0`](https://github.com/vhspace/ufm-mcp/commit/0f68ec026da853192d409fd9bf30ee490bc01981))

### Chores

- Release v0.4.1
  ([`2afcdff`](https://github.com/vhspace/ufm-mcp/commit/2afcdffd9d2f06c142d9c7a536875ba44cb662e0))

### Continuous Integration

- Make HTTP transport tests non-blocking
  ([`de6b475`](https://github.com/vhspace/ufm-mcp/commit/de6b475e3a337264b2f98662c440ce50ea83598a))

- Make mypy non-blocking (continue-on-error)
  ([`7e7e14d`](https://github.com/vhspace/ufm-mcp/commit/7e7e14de19623f7378b06dad5f75be2183a55e95))

### Documentation

- Add release version badge to README
  ([`8732f26`](https://github.com/vhspace/ufm-mcp/commit/8732f26d420332292f011a026125123fd1e8606c))


## v0.4.0 (2026-04-07)

### Bug Fixes

- Bump mcp-common to v0.6.1 (sync/async wrapper fix)
  ([`c61ed50`](https://github.com/vhspace/ufm-mcp/commit/c61ed50791e9da8d3970d833b1ac950cbf7a88d1))

### Chores

- Release v0.4.0
  ([`d7fb785`](https://github.com/vhspace/ufm-mcp/commit/d7fb785e2e7b260c988b8dc7820029cb77c28270))

### Features

- Wire mcp-common agent remediation and SSL warning suppression
  ([`1fd991c`](https://github.com/vhspace/ufm-mcp/commit/1fd991c9c4387ffec9802720917048ef765f30f2))


## v0.3.1 (2026-04-07)

### Bug Fixes

- Handle HTTPStatusError in pkey host operations ([#13](https://github.com/vhspace/ufm-mcp/pull/13),
  [`b8de09b`](https://github.com/vhspace/ufm-mcp/commit/b8de09bfb420e2eb6db3371c6225c32766c5a511))

### Chores

- Release v0.3.1
  ([`9739a1f`](https://github.com/vhspace/ufm-mcp/commit/9739a1fa2e604478bfbca9f50b6e7d8b931f699e))


## v0.3.0 (2026-04-02)

### Bug Fixes

- Resolve pkey GUIDs to hostnames ([#10](https://github.com/vhspace/ufm-mcp/pull/10),
  [`87ccc8d`](https://github.com/vhspace/ufm-mcp/commit/87ccc8df5324df796f48b138f1b5bde08388abc6))

### Chores

- Release v0.3.0
  ([`6d1b76b`](https://github.com/vhspace/ufm-mcp/commit/6d1b76b6c11f315a9674c2a899b064c9cd7aa395))

### Features

- Add pkey management CLI commands and MCP tools
  ([`9fbcda0`](https://github.com/vhspace/ufm-mcp/commit/9fbcda0792fa9937f8bd13d4b998548bc1245b1e))

### Refactoring

- Extract _parse_json_response to DRY up HTTP client
  ([`7b15e23`](https://github.com/vhspace/ufm-mcp/commit/7b15e23deba10a27ddf448fdfcfad34e1e925f18))


## v0.2.3 (2026-03-11)

### Bug Fixes

- Skill guidance + hook WORKSPACE_ROOT fix ([#9](https://github.com/vhspace/ufm-mcp/pull/9),
  [`efe99ce`](https://github.com/vhspace/ufm-mcp/commit/efe99ce90d59af6d39b40c4883d96fbbcb226fd0))

### Chores

- Release v0.2.3
  ([`5f80856`](https://github.com/vhspace/ufm-mcp/commit/5f80856930f7bd98b7adeea482f2e99bd26dac69))


## v0.2.2 (2026-03-11)

### Chores

- Release v0.2.2
  ([`a16b2ac`](https://github.com/vhspace/ufm-mcp/commit/a16b2ac4861a7d7a288f59702cf2dd964828bb3a))

### Features

- Add mcp-plugin.toml and pre-commit hook for plugin generation
  ([#8](https://github.com/vhspace/ufm-mcp/pull/8),
  [`3dec7c1`](https://github.com/vhspace/ufm-mcp/commit/3dec7c12972853262f59151ce08e8d31bd0886e6))


## v0.2.1 (2026-03-11)

### Chores

- Release v0.2.1
  ([`dac6df8`](https://github.com/vhspace/ufm-mcp/commit/dac6df8c426bbad442f5ead8cade04afc7931f0e))

### Features

- Add SessionStart hooks for automatic CLI setup ([#7](https://github.com/vhspace/ufm-mcp/pull/7),
  [`29d50ba`](https://github.com/vhspace/ufm-mcp/commit/29d50ba998f8b05b16a8818c07a5ef41e3165003))


## v0.2.0 (2026-03-11)

### Chores

- Release v0.2.0
  ([`288ad3f`](https://github.com/vhspace/ufm-mcp/commit/288ad3f4701b883f312000372ea827b5e9d30fe4))

### Features

- Add ufm-cli companion CLI for token-efficient agent workflows
  ([#6](https://github.com/vhspace/ufm-mcp/pull/6),
  [`2d33943`](https://github.com/vhspace/ufm-mcp/commit/2d339439067c808966a01d34dea503a147704753))


## v0.1.3 (2026-03-10)

### Bug Fixes

- Use git source for mcp-common dependency
  ([`408d2d5`](https://github.com/vhspace/ufm-mcp/commit/408d2d5695ca926251f123240bf8057027c6ef6e))


## v0.1.2 (2026-03-10)

### Chores

- Release v0.1.2
  ([`5753d41`](https://github.com/vhspace/ufm-mcp/commit/5753d413996ab4d46cf1ddb5a33e0ae03632f34a))

### Features

- Add production HTTP transport, Dockerfile, Helm chart, and health endpoint
  ([`982ba89`](https://github.com/vhspace/ufm-mcp/commit/982ba89a47fb2198df3e245273b87dff97c7a86a))

- Add progress reporting and combined create-and-wait tools
  ([`d7e14b5`](https://github.com/vhspace/ufm-mcp/commit/d7e14b56003789f4b606ce4dc8bc095d11972370))

### Refactoring

- Inherit MCPSettings, use shared logging, make auth optional
  ([`88e0ad8`](https://github.com/vhspace/ufm-mcp/commit/88e0ad88135a1fc9c9626ecbb3c24110811f4e3b))


## v0.1.1 (2026-03-06)

### Chores

- Release v0.1.1
  ([`d960fdf`](https://github.com/vhspace/ufm-mcp/commit/d960fdf24da5d0efd0fba7beafbae402f894ce6b))

### Features

- Add mcpServers config, skills, and fix plugin metadata
  ([`3669cb2`](https://github.com/vhspace/ufm-mcp/commit/3669cb2b6eb98fd8ac3f537b00a168b4a74d4a79))


## v0.1.0 (2026-03-05)

- Initial Release
