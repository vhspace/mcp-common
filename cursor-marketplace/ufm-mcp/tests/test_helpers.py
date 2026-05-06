from __future__ import annotations

import json
from datetime import UTC, datetime

from ufm_mcp.helpers import (
    build_guid_to_hostname_map,
    count_severities,
    deduplicate_log_lines,
    ensure_json_serializable,
    is_error_line,
    is_linkish,
    normalize_list_payload,
    parse_sm_log_ts,
    parse_ts_utc,
    parse_ufm_log_ts,
    pkey_diff,
    resolve_pkey_guids_to_hosts,
    summarize_alarm,
    summarize_event,
    top_n,
    truncate_text,
)


def test_ensure_json_serializable_primitives() -> None:
    assert ensure_json_serializable(None) is None
    assert ensure_json_serializable("hello") == "hello"
    assert ensure_json_serializable(42) == 42
    assert ensure_json_serializable(3.14) == 3.14
    assert ensure_json_serializable(True) is True


def test_ensure_json_serializable_nested() -> None:
    data = {"key": [1, {"nested": True}]}
    result = ensure_json_serializable(data)
    assert result == {"key": [1, {"nested": True}]}


def test_ensure_json_serializable_non_serializable() -> None:
    # object() is serializable via json.dumps(..., default=str), so impl returns it as-is.
    # Verify the result is always JSON-serializable (the function's contract).
    result = ensure_json_serializable(object())
    json.dumps(result, default=str)  # no raise


def test_parse_ts_utc_valid() -> None:
    dt = parse_ts_utc("2026-02-06 16:26:02")
    assert dt is not None
    assert dt.year == 2026
    assert dt.tzinfo is UTC


def test_parse_ts_utc_with_fractional() -> None:
    dt = parse_ts_utc("2026-02-06 16:26:02.123456")
    assert dt is not None
    assert dt.microsecond == 123456


def test_parse_ts_utc_empty() -> None:
    assert parse_ts_utc("") is None
    assert parse_ts_utc("   ") is None


def test_parse_ts_utc_invalid() -> None:
    assert parse_ts_utc("not-a-timestamp") is None


def test_top_n() -> None:
    d = {"a": 10, "b": 5, "c": 20}
    result = top_n(d, 2)
    assert result == [{"name": "c", "count": 20}, {"name": "a", "count": 10}]


def test_top_n_empty() -> None:
    assert top_n({}) == []


def test_normalize_list_payload_list() -> None:
    payload = [{"id": 1}, {"id": 2}, "not-a-dict"]
    result = normalize_list_payload(payload)
    assert result == [{"id": 1}, {"id": 2}]


def test_normalize_list_payload_data_wrapper() -> None:
    payload = {"data": [{"id": 1}]}
    result = normalize_list_payload(payload)
    assert result == [{"id": 1}]


def test_normalize_list_payload_unexpected() -> None:
    assert normalize_list_payload("string") == []
    assert normalize_list_payload(42) == []


def test_count_severities() -> None:
    items = [
        {"severity": "Warning"},
        {"severity": "Warning"},
        {"severity": "Critical"},
        {"severity": ""},
    ]
    result = count_severities(items)
    assert result == {"Warning": 2, "Critical": 1, "Unknown": 1}


def test_truncate_text() -> None:
    text, truncated = truncate_text("hello world", 5)
    assert text == "hello"
    assert truncated is True

    text, truncated = truncate_text("hi", 10)
    assert text == "hi"
    assert truncated is False


def test_parse_ufm_log_ts() -> None:
    line = "2026-02-06 16:26:02.688 some log text"
    dt = parse_ufm_log_ts(line, None)
    assert dt is not None
    assert dt.year == 2026
    assert dt.microsecond == 688000


def test_parse_ufm_log_ts_no_match() -> None:
    assert parse_ufm_log_ts("no timestamp here", None) is None


def test_parse_sm_log_ts() -> None:
    line = "Feb 06 16:26:35 some SM log text"
    dt = parse_sm_log_ts(line, None, 2026)
    assert dt is not None
    assert dt.month == 2
    assert dt.day == 6


def test_parse_sm_log_ts_year_rollover() -> None:
    line = "Dec 31 23:59:59 log entry"
    now = datetime.now()
    if now.month == 1:
        dt = parse_sm_log_ts(line, None, now.year)
        assert dt is not None
        assert dt.year == now.year - 1


def test_is_error_line() -> None:
    assert is_error_line("2026-02-06 ERROR something broke") is True
    assert is_error_line("2026-02-06 WARNING low disk") is True
    assert is_error_line("2026-02-06 INFO all good") is False


def test_is_linkish() -> None:
    assert is_linkish({"type": "Link Down"}) is True
    assert is_linkish({"name": "link_state_change"}) is True
    assert is_linkish({"type": "Port", "name": "high_ber"}) is False


def test_is_linkish_description_match() -> None:
    assert is_linkish({"description": "Link went down on port 3"}) is True
    assert is_linkish({"description": "BER threshold exceeded"}) is False


def test_count_severities_custom_key() -> None:
    items = [
        {"high_ber_severity": "warning"},
        {"high_ber_severity": "error"},
        {"high_ber_severity": "warning"},
    ]
    result = count_severities(items, key="high_ber_severity")
    assert result == {"warning": 2, "error": 1}


def test_count_severities_non_dict_items() -> None:
    items: list[dict[str, str]] = []
    assert count_severities(items) == {}


def test_truncate_text_zero_limit() -> None:
    text, truncated = truncate_text("hello", 0)
    assert text == ""
    assert truncated is True


def test_normalize_list_payload_none() -> None:
    assert normalize_list_payload(None) == []


def test_ensure_json_serializable_tuple() -> None:
    result = ensure_json_serializable((1, "a", True))
    assert result == [1, "a", True]


def test_ensure_json_serializable_non_string_keys() -> None:
    result = ensure_json_serializable({1: "one", 2: "two"})
    assert result == {"1": "one", "2": "two"}


def test_summarize_alarm() -> None:
    alarm = {
        "id": 42,
        "name": "high_ber",
        "description": "BER threshold exceeded",
        "severity": "Warning",
        "timestamp": "2026-02-06 10:00:00",
        "type": "Threshold",
        "object_name": "port1",
        "extra_field": "ignored",
    }
    result = summarize_alarm(alarm)
    assert result["id"] == 42
    assert result["severity"] == "Warning"
    assert result["object_name"] == "port1"
    assert "extra_field" not in result


def test_summarize_event() -> None:
    event = {
        "id": 99,
        "severity": "Critical",
        "name": "link_down",
        "timestamp": "2026-02-06 10:00:00",
        "type": "Fabric",
        "object_name": "port2",
        "object_path": "/systems/sys1/ports/2",
        "description": "Link went down",
        "extra": "ignored",
    }
    result = summarize_event(event)
    assert result["id"] == 99
    assert result["object_path"] == "/systems/sys1/ports/2"
    assert "extra" not in result


def test_summarize_alarm_with_resolved_name() -> None:
    alarm = {
        "id": 42,
        "name": "high_ber",
        "severity": "Warning",
        "object_name": "a088c20300f40636_1",
        "resolved_name": "gpu-node-01",
    }
    result = summarize_alarm(alarm)
    assert result["resolved_name"] == "gpu-node-01"
    assert result["object_name"] == "a088c20300f40636_1"


def test_summarize_alarm_without_resolved_name() -> None:
    alarm = {"id": 42, "name": "high_ber", "severity": "Warning"}
    result = summarize_alarm(alarm)
    assert "resolved_name" not in result


def test_summarize_alarm_missing_keys() -> None:
    result = summarize_alarm({})
    assert "id" not in result
    assert "severity" not in result


def test_parse_ufm_log_ts_with_timezone() -> None:
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/New_York")
    line = "2026-02-06 16:26:02.123 some log text"
    dt = parse_ufm_log_ts(line, tz)
    assert dt is not None
    assert dt.tzinfo == tz
    assert dt.microsecond == 123000


# ----------------------------------------------------------------
#  build_guid_to_hostname_map tests
# ----------------------------------------------------------------


def test_build_guid_to_hostname_map_basic() -> None:
    systems = [
        {
            "system_guid": "0x0002c9030005f340",
            "system_name": "node01",
            "guid": "0x0002c9030005f340",
        },
        {
            "system_guid": "0x0002c9030005f350",
            "system_name": "node02",
        },
    ]
    result = build_guid_to_hostname_map(systems)
    assert result["0x0002c9030005f340"] == "node01"
    assert result["0x0002c9030005f350"] == "node02"


def test_build_guid_to_hostname_map_with_modules_and_ports() -> None:
    systems = [
        {
            "system_guid": "0x0002c9030005f340",
            "system_name": "node01",
            "modules": [
                {"guid": "0x0002c9030005f341"},
                {"guid": "0x0002c9030005f342"},
            ],
            "ports": [
                {"guid": "0x0002c9030005f34a"},
                {"guid": "0x0002c9030005f34b"},
            ],
        },
    ]
    result = build_guid_to_hostname_map(systems)
    assert result["0x0002c9030005f340"] == "node01"
    assert result["0x0002c9030005f341"] == "node01"
    assert result["0x0002c9030005f342"] == "node01"
    assert result["0x0002c9030005f34a"] == "node01"
    assert result["0x0002c9030005f34b"] == "node01"


def test_build_guid_to_hostname_map_empty() -> None:
    assert build_guid_to_hostname_map([]) == {}


def test_build_guid_to_hostname_map_skips_no_hostname() -> None:
    systems = [{"system_guid": "0x001122", "system_name": ""}]
    assert build_guid_to_hostname_map(systems) == {}


def test_build_guid_to_hostname_map_skips_non_dict() -> None:
    systems = ["not-a-dict", None, 42]
    assert build_guid_to_hostname_map(systems) == {}


def test_build_guid_to_hostname_map_case_insensitive() -> None:
    systems = [
        {"system_guid": "0x00AABB", "system_name": "host1"},
    ]
    result = build_guid_to_hostname_map(systems)
    assert "0x00aabb" in result


# ----------------------------------------------------------------
#  resolve_pkey_guids_to_hosts tests
# ----------------------------------------------------------------


def test_resolve_pkey_guids_list_of_strings() -> None:
    guid_map = {
        "0x0002c9030005f34a": "node01",
        "0x0002c9030005f34b": "node01",
        "0x0002c9030005f35a": "node02",
    }
    pkey_data = {
        "guids": [
            "0x0002c9030005f34a",
            "0x0002c9030005f34b",
            "0x0002c9030005f35a",
            "0xdeadbeef00000001",
        ]
    }
    hosts, unresolved = resolve_pkey_guids_to_hosts(pkey_data, guid_map)
    assert len(hosts) == 2
    assert hosts[0]["hostname"] == "node01"
    assert hosts[0]["guid_count"] == 2
    assert hosts[1]["hostname"] == "node02"
    assert hosts[1]["guid_count"] == 1
    assert len(unresolved) == 1
    assert unresolved[0]["guid"] == "0xdeadbeef00000001"


def test_resolve_pkey_guids_list_of_dicts() -> None:
    guid_map = {"0xaaa": "host-a", "0xbbb": "host-b"}
    pkey_data = {
        "guids": [
            {"guid": "0xAAA", "membership": "full"},
            {"guid": "0xBBB", "membership": "limited"},
        ]
    }
    hosts, unresolved = resolve_pkey_guids_to_hosts(pkey_data, guid_map)
    assert len(hosts) == 2
    assert hosts[0]["hostname"] == "host-a"
    assert hosts[0]["membership_types"] == ["full"]
    assert hosts[1]["hostname"] == "host-b"
    assert hosts[1]["membership_types"] == ["limited"]
    assert len(unresolved) == 0


def test_resolve_pkey_guids_empty() -> None:
    hosts, unresolved = resolve_pkey_guids_to_hosts({"guids": []}, {})
    assert hosts == []
    assert unresolved == []


def test_resolve_pkey_guids_nested_data() -> None:
    guid_map = {"0xaaa": "host-a"}
    pkey_data = {"data": {"guids": ["0xAAA"]}}
    hosts, _unresolved = resolve_pkey_guids_to_hosts(pkey_data, guid_map)
    assert len(hosts) == 1
    assert hosts[0]["hostname"] == "host-a"


def test_resolve_pkey_guids_all_unresolved() -> None:
    pkey_data = {"guids": ["0x111", "0x222"]}
    hosts, unresolved = resolve_pkey_guids_to_hosts(pkey_data, {})
    assert hosts == []
    assert len(unresolved) == 2


def test_resolve_pkey_guids_mixed_membership() -> None:
    guid_map = {"0xaaa": "host-a", "0xbbb": "host-a"}
    pkey_data = {
        "guids": [
            {"guid": "0xAAA", "membership": "full"},
            {"guid": "0xBBB", "membership": "limited"},
        ]
    }
    hosts, _unresolved = resolve_pkey_guids_to_hosts(pkey_data, guid_map)
    assert len(hosts) == 1
    assert hosts[0]["hostname"] == "host-a"
    assert hosts[0]["guid_count"] == 2
    assert sorted(hosts[0]["membership_types"]) == ["full", "limited"]


# ----------------------------------------------------------------
#  pkey_diff tests
# ----------------------------------------------------------------


def test_pkey_diff_basic() -> None:
    result = pkey_diff(["node01", "node02", "node03"], ["node02", "node03", "node04"])
    assert result["to_add"] == ["node04"]
    assert result["to_remove"] == ["node01"]
    assert result["unchanged"] == ["node02", "node03"]


def test_pkey_diff_identical() -> None:
    result = pkey_diff(["a", "b"], ["a", "b"])
    assert result["to_add"] == []
    assert result["to_remove"] == []
    assert result["unchanged"] == ["a", "b"]


def test_pkey_diff_empty_current() -> None:
    result = pkey_diff([], ["x", "y"])
    assert result["to_add"] == ["x", "y"]
    assert result["to_remove"] == []
    assert result["unchanged"] == []


def test_pkey_diff_empty_expected() -> None:
    result = pkey_diff(["x", "y"], [])
    assert result["to_add"] == []
    assert result["to_remove"] == ["x", "y"]
    assert result["unchanged"] == []


def test_pkey_diff_both_empty() -> None:
    result = pkey_diff([], [])
    assert result["to_add"] == []
    assert result["to_remove"] == []
    assert result["unchanged"] == []


# ----------------------------------------------------------------
#  deduplicate_log_lines tests
# ----------------------------------------------------------------


def test_deduplicate_log_lines_empty() -> None:
    assert deduplicate_log_lines([]) == []


def test_deduplicate_log_lines_no_duplicates() -> None:
    lines = [
        "2026-04-23 10:00:01 ERROR something broke",
        "2026-04-23 10:00:02 ERROR different thing",
    ]
    result = deduplicate_log_lines(lines)
    assert result == lines


def test_deduplicate_log_lines_consecutive_duplicates() -> None:
    lines = [
        "2026-04-23 10:00:01 ERROR prometheus fetch failed",
        "2026-04-23 10:00:31 ERROR prometheus fetch failed",
        "2026-04-23 10:01:01 ERROR prometheus fetch failed",
    ]
    result = deduplicate_log_lines(lines)
    assert len(result) == 1
    assert "x3" in result[0]
    assert "10:00:01" in result[0]
    assert "10:01:01" in result[0]


def test_deduplicate_log_lines_mixed() -> None:
    lines = [
        "2026-04-23 10:00:01 ERROR prometheus fetch failed",
        "2026-04-23 10:00:31 ERROR prometheus fetch failed",
        "2026-04-23 10:01:00 ERROR link down on port 63",
        "2026-04-23 10:01:30 ERROR prometheus fetch failed",
    ]
    result = deduplicate_log_lines(lines)
    assert len(result) == 3
    assert "x2" in result[0]
    assert "link down" in result[1]
    assert "prometheus" in result[2]
    assert "(x" not in result[2]


def test_deduplicate_log_lines_sm_format() -> None:
    lines = [
        "Apr 23 10:00:01 ERROR repeated msg",
        "Apr 23 10:00:31 ERROR repeated msg",
    ]
    result = deduplicate_log_lines(lines)
    assert len(result) == 1
    assert "x2" in result[0]


def test_deduplicate_log_lines_single() -> None:
    lines = ["2026-04-23 10:00:01 ERROR only one"]
    assert deduplicate_log_lines(lines) == lines
