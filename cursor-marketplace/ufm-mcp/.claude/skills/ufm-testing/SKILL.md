---
name: ufm-testing
description: Use when testing the UFM MCP server after code changes. Provides structured protocol for validating tool functionality.
---

# UFM MCP Testing Protocol

## When to Use

- After code changes to server.py, helpers.py, site_manager.py, or config.py
- Validating MCP server connectivity with a live UFM instance
- Before creating pull requests

## Prerequisites

1. UFM MCP server is connected (verify with tool list)
2. UFM_URL and UFM_TOKEN environment variables are set
3. Access to a live UFM instance

## Test Sequence

### 1. Connectivity
- Call `ufm_get_version` to verify basic connectivity
- Call `ufm_get_config` to confirm configuration is loaded

### 2. Read Operations
- `ufm_list_alarms(limit=5)` -- verify alarm retrieval
- `ufm_list_events(limit=5)` -- verify event retrieval
- `ufm_get_concerns(max_items=5)` -- verify aggregation
- `ufm_get_log(log_type="UFM", length=10)` -- verify log download

### 3. Search Operations
- `ufm_search_log(query="error", log_type="UFM", max_matches=5)`
- `ufm_search_logs(query="ERR", max_matches=5)`

### 4. Port/Link Operations
- `ufm_get_high_ber_ports(limit=5)`
- `ufm_check_links_recent(lookback_minutes=60, max_events=5)`

### 5. Triage Aggregator
- `ufm_get_cluster_concerns(lookback_minutes=60, max_items=3)`

### 6. Multi-Site (if configured)
- `ufm_list_sites` -- verify site listing
- Repeat key tests with explicit site= parameter

## Reporting

For each test, note:
- Pass/fail status
- Response time
- Any unexpected errors or data shapes
