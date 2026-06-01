"""Tests for server.py internal helper functions and write-gate enforcement."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from maas_mcp.server import (
    _ensure_json_serializable,
    _get_boot_interface_mac,
    _migrate_copy_power_from_source,
    _migrate_set_commissioning_scriptset,
    _migrate_set_deployed,
    _migrate_sync_disks,
    _migrate_sync_hardware_info,
    _migrate_sync_interfaces,
    _migrate_sync_metadata,
    _migrate_sync_numa_and_devices,
    _migrate_sync_tags,
    _normalize_list_response,
    _resolve_audit_targets,
    _resolve_system_id,
    _select_fields,
)


def _make_ctx():
    ctx = AsyncMock()
    ctx.info = AsyncMock()
    ctx.warning = AsyncMock()
    ctx.error = AsyncMock()
    ctx.debug = AsyncMock()
    return ctx


class TestNormalizeListResponse:
    def test_list_passthrough(self):
        assert _normalize_list_response([1, 2, 3]) == [1, 2, 3]

    def test_dict_with_results(self):
        assert _normalize_list_response({"results": [4, 5]}) == [4, 5]

    def test_single_item(self):
        assert _normalize_list_response({"id": 1}) == [{"id": 1}]

    def test_none_returns_empty(self):
        assert _normalize_list_response(None) == []

    def test_empty_list(self):
        assert _normalize_list_response([]) == []


class TestEnsureJsonSerializable:
    def test_primitives(self):
        assert _ensure_json_serializable(None) is None
        assert _ensure_json_serializable(42) == 42
        assert _ensure_json_serializable("hello") == "hello"
        assert _ensure_json_serializable(True) is True
        assert _ensure_json_serializable(3.14) == 3.14

    def test_nested_dict(self):
        result = _ensure_json_serializable({"a": {"b": 1}})
        assert result == {"a": {"b": 1}}

    def test_list_of_dicts(self):
        result = _ensure_json_serializable([{"x": 1}, {"y": 2}])
        assert result == [{"x": 1}, {"y": 2}]

    def test_non_serializable_becomes_str(self):
        result = _ensure_json_serializable(object())
        assert isinstance(result, str)

    def test_dict_keys_become_str(self):
        result = _ensure_json_serializable({1: "a", 2: "b"})
        assert result == {"1": "a", "2": "b"}


class TestSelectFields:
    def test_no_fields_passthrough(self):
        obj = {"a": 1, "b": 2, "c": 3}
        assert _select_fields(obj, None) == obj

    def test_select_subset(self):
        obj = {"a": 1, "b": 2, "c": 3}
        assert _select_fields(obj, ["a", "c"]) == {"a": 1, "c": 3}

    def test_select_from_list(self):
        objs = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
        assert _select_fields(objs, ["a"]) == [{"a": 1}, {"a": 3}]

    def test_missing_fields_ignored(self):
        obj = {"a": 1}
        assert _select_fields(obj, ["a", "z"]) == {"a": 1}


class TestResolveSystemId:
    def test_system_id_passthrough(self):
        client = MagicMock()
        client.get.return_value = {"system_id": "abc123", "hostname": "test"}
        assert _resolve_system_id(client, "abc123", None) == "abc123"
        client.get.assert_called_once_with("machines/abc123")

    def test_machine_id_resolved(self):
        client = MagicMock()
        client.get.return_value = [{"system_id": "xyz789", "hostname": "test"}]
        assert _resolve_system_id(client, None, 42) == "xyz789"
        client.get.assert_called_once_with("machines", params={"id": "42"})

    def test_neither_raises(self):
        client = MagicMock()
        with pytest.raises(ValueError, match="Either system_id or machine_id"):
            _resolve_system_id(client, None, None)

    def test_machine_not_found_raises(self):
        client = MagicMock()
        client.get.return_value = []
        with pytest.raises(RuntimeError, match="not found"):
            _resolve_system_id(client, None, 999)

    def test_machine_no_system_id_raises(self):
        client = MagicMock()
        client.get.return_value = [{"hostname": "test"}]
        with pytest.raises(RuntimeError, match="has no system_id"):
            _resolve_system_id(client, None, 42)

    def test_system_id_404_falls_back_to_netbox(self):
        """When system_id returns 404, try NetBox resolution."""
        client = MagicMock()
        client.get.side_effect = [
            RuntimeError(
                "MAAS GET http://maas/MAAS/api/2.0/machines/research-common-h100-001/ failed: 404 not found"
            ),
            [{"system_id": "abc123", "hostname": "gpu001"}],
        ]

        nb_client = MagicMock()
        nb_client.lookup_device.return_value = {
            "name": "research-common-h100-001",
            "custom_fields": {"Provider_Machine_ID": "gpu001"},
        }

        with patch("maas_mcp.server._get_netbox", return_value=nb_client):
            result = _resolve_system_id(client, "research-common-h100-001", None)

        assert result == "abc123"
        nb_client.lookup_device.assert_called_once_with("research-common-h100-001")

    def test_system_id_404_no_netbox_reraises(self):
        """When system_id 404 and no NetBox configured, re-raise with hint."""
        client = MagicMock()
        err = RuntimeError(
            "MAAS GET http://maas/MAAS/api/2.0/machines/bad-id/ failed: 404 not found"
        )
        client.get.side_effect = err

        with patch("maas_mcp.server._get_netbox", side_effect=Exception("not configured")):
            with pytest.raises(RuntimeError, match="No MAAS machine matched"):
                _resolve_system_id(client, "bad-id", None)

    def test_system_id_404_netbox_no_match_reraises(self):
        """When NetBox finds no device, re-raise with hint (includes original 404)."""
        client = MagicMock()
        err = RuntimeError(
            "MAAS GET http://maas/MAAS/api/2.0/machines/unknown/ failed: 404 not found"
        )
        client.get.side_effect = err

        nb_client = MagicMock()
        nb_client.lookup_device.return_value = None

        with patch("maas_mcp.server._get_netbox", return_value=nb_client):
            with pytest.raises(RuntimeError, match="No MAAS machine matched"):
                _resolve_system_id(client, "unknown", None)

    def test_system_id_non_404_error_not_caught(self):
        """Non-404 errors are re-raised immediately."""
        client = MagicMock()
        client.get.side_effect = RuntimeError(
            "MAAS GET http://maas/MAAS/api/2.0/machines/x/ failed: 500 server error"
        )
        with pytest.raises(RuntimeError, match="500"):
            _resolve_system_id(client, "x", None)


class TestNormalizeListResponseEdgeCases:
    def test_empty_results(self):
        assert _normalize_list_response({"results": []}) == []

    def test_falsy_value_zero_wraps(self):
        assert _normalize_list_response(0) == [0]

    def test_false_wraps(self):
        assert _normalize_list_response(False) == [False]

    def test_empty_string_wraps(self):
        assert _normalize_list_response("") == [""]


class TestResolveAuditTargets:
    def test_baseline_by_system_id(self):
        client = MagicMock()
        client.get.return_value = {"system_id": "bl1", "hostname": "baseline"}

        baseline, bl_sid, targets = _resolve_audit_targets(
            client,
            machine_ids=None,
            system_ids=["tgt1"],
            baseline_machine_id=None,
            baseline_system_id="bl1",
        )
        assert bl_sid == "bl1"
        assert targets == ["tgt1"]
        assert baseline["hostname"] == "baseline"

    def test_baseline_system_id_netbox_resolve_on_404(self):
        """Baseline NetBox-style name resolves like _resolve_system_id."""
        client = MagicMock()
        err = RuntimeError(
            "MAAS GET http://maas/MAAS/api/2.0/machines/research-x/ failed: 404 not found"
        )
        client.get.side_effect = [
            err,
            [{"system_id": "real", "hostname": "gpu099"}],
            {"system_id": "real", "hostname": "gpu099"},
        ]

        nb_client = MagicMock()
        nb_client.lookup_device.return_value = {
            "name": "research-x",
            "custom_fields": {"Provider_Machine_ID": "gpu099"},
        }

        with patch("maas_mcp.server._get_netbox", return_value=nb_client):
            baseline, bl_sid, targets = _resolve_audit_targets(
                client,
                machine_ids=None,
                system_ids=["tgt1"],
                baseline_machine_id=None,
                baseline_system_id="research-x",
            )

        assert bl_sid == "real"
        assert baseline["hostname"] == "gpu099"
        assert targets == ["tgt1"]
        nb_client.lookup_device.assert_called_once_with("research-x")

    def test_baseline_by_machine_id(self):
        client = MagicMock()
        client.get.side_effect = [
            [{"system_id": "bl2", "hostname": "baseline-host"}],
            [{"system_id": "t1", "hostname": "target"}],
        ]

        _baseline, bl_sid, targets = _resolve_audit_targets(
            client,
            machine_ids=[10],
            system_ids=None,
            baseline_machine_id=5,
            baseline_system_id=None,
        )
        assert bl_sid == "bl2"
        assert targets == ["t1"]

    def test_mixed_targets(self):
        client = MagicMock()
        client.get.side_effect = [
            {"system_id": "bl", "hostname": "base"},
            [{"system_id": "from_mid", "hostname": "t"}],
        ]

        _, _, targets = _resolve_audit_targets(
            client,
            machine_ids=[99],
            system_ids=["direct_sid"],
            baseline_machine_id=None,
            baseline_system_id="bl",
        )
        assert targets == ["direct_sid", "from_mid"]

    def test_no_baseline_raises(self):
        client = MagicMock()
        with pytest.raises(ValueError, match="baseline"):
            _resolve_audit_targets(
                client,
                machine_ids=[1],
                system_ids=None,
                baseline_machine_id=None,
                baseline_system_id=None,
            )

    def test_all_targets_unresolvable_raises(self):
        client = MagicMock()
        client.get.side_effect = [
            {"system_id": "bl", "hostname": "base"},
            [],
        ]
        with pytest.raises(ValueError, match="No targets could be resolved"):
            _resolve_audit_targets(
                client,
                machine_ids=[999],
                system_ids=None,
                baseline_machine_id=None,
                baseline_system_id="bl",
            )

    def test_no_targets_at_all_raises(self):
        client = MagicMock()
        client.get.return_value = {"system_id": "bl", "hostname": "base"}
        with pytest.raises(ValueError, match="No targets could be resolved"):
            _resolve_audit_targets(
                client,
                machine_ids=None,
                system_ids=None,
                baseline_machine_id=None,
                baseline_system_id="bl",
            )


class TestWriteGates:
    """Verify all write tools refuse by default.

    FastMCP v3's @mcp.tool decorator returns the original function, so we
    call it directly without a .fn accessor.
    """

    @pytest.mark.asyncio
    async def test_set_power_params_refuses(self):
        from fastmcp.exceptions import ToolError

        from maas_mcp.server import maas_set_machine_power_parameters

        with pytest.raises(ToolError, match="allow_write"):
            await maas_set_machine_power_parameters(
                ctx=_make_ctx(), system_id="abc", allow_write=False
            )

    @pytest.mark.asyncio
    async def test_run_machine_op_refuses(self):
        from fastmcp.exceptions import ToolError

        from maas_mcp.server import maas_run_machine_op

        with pytest.raises(ToolError, match="allow_write"):
            await maas_run_machine_op(
                ctx=AsyncMock(), system_id="abc", op="power_on", allow_write=False
            )

    @pytest.mark.asyncio
    async def test_set_bmc_password_refuses(self):
        from fastmcp.exceptions import ToolError

        from maas_mcp.server import maas_set_bmc_account_password_from_maas

        with pytest.raises(ToolError, match="allow_write"):
            await maas_set_bmc_account_password_from_maas(
                ctx=AsyncMock(), system_id="abc", new_password="x", allow_write=False
            )

    @pytest.mark.asyncio
    async def test_create_bmc_account_refuses(self):
        from fastmcp.exceptions import ToolError

        from maas_mcp.server import maas_create_bmc_account_redfish

        with pytest.raises(ToolError, match="allow_write"):
            await maas_create_bmc_account_redfish(
                ctx=_make_ctx(),
                bmc_host="1.2.3.4",
                username="u",
                password="p",
                redfish_admin_user="a",
                redfish_admin_password="a",
                allow_write=False,
            )


class FakePowerClient:
    def __init__(self, power_response: dict, *, power_type: str = "ipmi") -> None:
        self.power_response = power_response
        self.power_type = power_type
        self.put_calls: list[tuple[str, dict]] = []

    def get(self, path: str, params: dict | None = None):  # type: ignore[no-untyped-def]
        if path == "machines/src" and params == {"op": "power_parameters"}:
            return self.power_response
        if path == "machines/src" and params is None:
            return {"system_id": "src", "power_type": self.power_type}
        raise AssertionError(f"unexpected get {path} {params}")

    def put(self, path: str, data: dict | None = None, *, params: dict | None = None):  # type: ignore[no-untyped-def]
        self.put_calls.append((path, data or {}))
        return {}


class TestMigrateCopyPowerFromSource:
    def test_dry_run_lists_keys(self):
        src = FakePowerClient(
            {"power_address": "10.0.0.1", "power_user": "u", "power_pass": "secret"}
        )
        tgt = FakePowerClient({})
        out = _migrate_copy_power_from_source(src, "src", tgt, "tgt", dry_run=True)
        assert out["ok"] is True
        assert "power_type" in out["would_update_keys"]
        assert "power_parameters_power_address" in out["would_update_keys"]
        assert not tgt.put_calls

    def test_write_applies_put(self):
        src = FakePowerClient(
            {"power_address": "10.0.0.2", "power_user": "maas", "power_pass": "x"}
        )
        tgt = FakePowerClient({})
        out = _migrate_copy_power_from_source(src, "src", tgt, "tgt", dry_run=False)
        assert out["ok"] is True
        assert tgt.put_calls
        path, data = tgt.put_calls[0]
        assert path == "machines/tgt"
        assert data["power_type"] == "ipmi"
        assert data["power_parameters_power_address"] == "10.0.0.2"
        assert data["power_parameters_power_user"] == "maas"

    def test_redacted_password_still_updates_address_user(self):
        """Source API often redacts power_pass; we still copy address and user."""
        src = FakePowerClient({"power_address": "10.0.0.1", "power_user": "u", "power_pass": "***"})
        tgt = FakePowerClient({})
        out = _migrate_copy_power_from_source(src, "src", tgt, "tgt", dry_run=False)
        assert out["ok"] is True
        assert tgt.put_calls
        _path, data = tgt.put_calls[0]
        assert data["power_parameters_power_address"] == "10.0.0.1"
        assert data["power_parameters_power_user"] == "u"
        assert "power_parameters_power_pass" not in data

    def test_copies_privilege_level_and_cipher_suite(self):
        src = FakePowerClient(
            {
                "power_address": "10.0.0.1",
                "power_user": "u",
                "power_pass": "x",
                "privilege_level": "ADMINISTRATOR",
                "cipher_suite_id": "3",
            }
        )
        tgt = FakePowerClient({})
        out = _migrate_copy_power_from_source(src, "src", tgt, "tgt", dry_run=False)
        assert out["ok"] is True
        _path, data = tgt.put_calls[0]
        assert data["power_parameters_privilege_level"] == "ADMINISTRATOR"
        assert data["power_parameters_cipher_suite_id"] == "3"


# ---------------------------------------------------------------------------
# _migrate_sync_interfaces tests
# ---------------------------------------------------------------------------


def _make_machine(status: str, ifaces: list[dict]) -> dict:
    return {"system_id": "tgt", "status_name": status, "interface_set": ifaces}


def _make_source_machine(ifaces: list[dict]) -> dict:
    return {"system_id": "src", "interface_set": ifaces}


def _phys(iface_id: int, name: str, mac: str, mtu: int = 1500) -> dict:
    return {
        "id": iface_id,
        "name": name,
        "mac_address": mac,
        "type": "physical",
        "effective_mtu": mtu,
    }


class FakeMigrateClient:
    """Fake MAAS client for interface sync tests."""

    def __init__(self, machine: dict) -> None:
        self.machine = machine
        self.post_calls: list[tuple[str, dict, dict | None]] = []
        self.delete_calls: list[str] = []
        self.put_calls: list[tuple[str, dict]] = []
        self._next_id = 9000

    def get(self, path: str, params: dict | None = None):  # type: ignore[no-untyped-def]
        if "machines/" in path and params is None:
            return self.machine
        if params and params.get("op") == "power_parameters":
            return {}
        raise AssertionError(f"unexpected get {path} {params}")

    def post(self, path: str, data: dict | None = None, *, params: dict | None = None):  # type: ignore[no-untyped-def]
        self.post_calls.append((path, data or {}, params))
        self._next_id += 1
        return {"id": self._next_id}

    def delete(self, path: str) -> None:  # type: ignore[no-untyped-def]
        self.delete_calls.append(path)

    def put(self, path: str, data: dict | None = None, *, params: dict | None = None):  # type: ignore[no-untyped-def]
        self.put_calls.append((path, data or {}))
        return {}


class TestMigrateSyncInterfaces:
    def test_dry_run_plans_create_and_delete(self):
        src = FakeMigrateClient(
            _make_source_machine(
                [
                    _phys(1, "enp48s0np0", "aa:bb:cc:dd:ee:01"),
                    _phys(2, "enp211s0np0", "aa:bb:cc:dd:ee:02"),
                ]
            )
        )
        tgt = FakeMigrateClient(
            _make_machine(
                "New",
                [
                    _phys(100, "eth0", "ff:ff:ff:ff:ff:ff"),
                ],
            )
        )
        out = _migrate_sync_interfaces(src, "src", tgt, "tgt", dry_run=True)
        assert out["ok"] is True
        assert out["dry_run"] is True
        assert len(out["would_create"]) == 2
        assert len(out["would_delete"]) == 1
        assert out["would_delete"][0]["mac"] == "ff:ff:ff:ff:ff:ff"
        assert not tgt.post_calls
        assert not tgt.delete_calls

    def test_write_creates_and_deletes(self):
        src = FakeMigrateClient(
            _make_source_machine(
                [
                    _phys(1, "enp48s0np0", "aa:bb:cc:dd:ee:01"),
                    _phys(2, "enp211s0np0", "aa:bb:cc:dd:ee:02"),
                ]
            )
        )
        tgt = FakeMigrateClient(
            _make_machine(
                "Ready",
                [
                    _phys(100, "eth0", "ff:ff:ff:ff:ff:ff"),
                ],
            )
        )
        out = _migrate_sync_interfaces(src, "src", tgt, "tgt", dry_run=False)
        assert out["ok"] is True
        assert len(out["deleted"]) == 1
        assert len(out["created"]) == 2
        assert tgt.delete_calls == ["nodes/tgt/interfaces/100"]
        assert len(tgt.post_calls) == 2
        for _path, data, params in tgt.post_calls:
            assert params == {"op": "create_physical"}
            assert "mac_address" in data
            assert "name" in data

    def test_skips_matching_macs(self):
        shared_mac = "aa:bb:cc:dd:ee:01"
        src = FakeMigrateClient(
            _make_source_machine(
                [
                    _phys(1, "enp48s0np0", shared_mac),
                    _phys(2, "enp211s0np0", "aa:bb:cc:dd:ee:02"),
                ]
            )
        )
        tgt = FakeMigrateClient(
            _make_machine(
                "New",
                [
                    _phys(100, "eth0", shared_mac),
                ],
            )
        )
        out = _migrate_sync_interfaces(src, "src", tgt, "tgt", dry_run=True)
        assert len(out["skipped"]) == 1
        assert out["skipped"][0]["mac"] == shared_mac
        assert len(out["would_create"]) == 1
        assert len(out["would_delete"]) == 0

    def test_rejects_deployed_state(self):
        src = FakeMigrateClient(_make_source_machine([]))
        tgt = FakeMigrateClient(_make_machine("Deployed", []))
        out = _migrate_sync_interfaces(src, "src", tgt, "tgt", dry_run=True)
        assert out["ok"] is False
        assert "Deployed" in out["error"]

    def test_idempotent_all_match(self):
        macs = [
            _phys(1, "enp48s0np0", "aa:bb:cc:dd:ee:01"),
            _phys(2, "enp211s0np0", "aa:bb:cc:dd:ee:02"),
        ]
        src = FakeMigrateClient(_make_source_machine(macs))
        tgt = FakeMigrateClient(
            _make_machine(
                "Ready",
                [
                    _phys(100, "eth0", "aa:bb:cc:dd:ee:01"),
                    _phys(101, "eth1", "aa:bb:cc:dd:ee:02"),
                ],
            )
        )
        out = _migrate_sync_interfaces(src, "src", tgt, "tgt", dry_run=False)
        assert out["ok"] is True
        assert len(out["skipped"]) == 2
        assert len(out["deleted"]) == 0
        assert len(out["created"]) == 0
        assert not tgt.post_calls
        assert not tgt.delete_calls

    def test_nondefault_mtu(self):
        src = FakeMigrateClient(
            _make_source_machine(
                [
                    _phys(1, "enp48s0np0", "aa:bb:cc:dd:ee:01", mtu=9000),
                ]
            )
        )
        tgt = FakeMigrateClient(_make_machine("New", []))
        out = _migrate_sync_interfaces(src, "src", tgt, "tgt", dry_run=False)
        assert out["ok"] is True
        _, data, _ = tgt.post_calls[0]
        assert data["mtu"] == "9000"


# ---------------------------------------------------------------------------
# _migrate_sync_metadata tests
# ---------------------------------------------------------------------------


class FakeMetadataClient(FakeMigrateClient):
    """FakeMigrateClient extended with zones/pools listing."""

    def __init__(self, machine: dict, zones: list | None = None, pools: list | None = None) -> None:
        super().__init__(machine)
        self._zones = zones or []
        self._pools = pools or []

    def get(self, path: str, params: dict | None = None):  # type: ignore[no-untyped-def]
        if path == "zones":
            return self._zones
        if path == "resourcepools":
            return self._pools
        return super().get(path, params)


class TestMigrateSyncMetadata:
    def _source(self) -> dict:
        return {
            "system_id": "src",
            "hostname": "gpu018",
            "architecture": "amd64/generic",
            "description": "test node",
            "cpu_count": 128,
            "memory": 1048576,
            "zone": {"name": "ori-tx", "id": 1},
            "pool": {"name": "gpu-pool", "id": 2},
        }

    def test_dry_run_lists_fields(self):
        src = FakeMigrateClient({"system_id": "src", **self._source()})
        tgt = FakeMigrateClient(_make_machine("Ready", []))
        out = _migrate_sync_metadata(src, "src", tgt, "tgt", dry_run=True)
        assert out["ok"] is True
        assert out["dry_run"] is True
        assert "hostname" in out["would_update"]
        assert out["would_update"]["zone"] == "ori-tx"
        assert out["would_update"]["pool"] == "gpu-pool"
        assert not tgt.put_calls

    def test_write_sends_put(self):
        src = FakeMigrateClient({"system_id": "src", **self._source()})
        tgt = FakeMetadataClient(
            _make_machine("Ready", []),
            zones=[{"name": "ori-tx"}],
            pools=[{"name": "gpu-pool"}],
        )
        out = _migrate_sync_metadata(src, "src", tgt, "tgt", dry_run=False)
        assert out["ok"] is True
        assert "hostname" in out["updated_fields"]
        assert tgt.put_calls
        path, data = tgt.put_calls[0]
        assert path == "machines/tgt"
        assert data["hostname"] == "gpu018"
        assert data["cpu_count"] == "128"
        assert data["zone"] == "ori-tx"

    def test_skips_zero_cpu_memory(self):
        src_data = self._source()
        src_data["cpu_count"] = 0
        src_data["memory"] = 0
        src = FakeMigrateClient({"system_id": "src", **src_data})
        tgt = FakeMigrateClient(_make_machine("Ready", []))
        out = _migrate_sync_metadata(src, "src", tgt, "tgt", dry_run=True)
        assert "cpu_count" not in out["would_update"]
        assert "memory" not in out["would_update"]

    def test_empty_source_skips(self):
        src = FakeMigrateClient({"system_id": "src"})
        tgt = FakeMigrateClient(_make_machine("Ready", []))
        out = _migrate_sync_metadata(src, "src", tgt, "tgt", dry_run=False)
        assert out.get("skipped") is True

    def test_os_fields_included(self):
        src_data = {
            "system_id": "src",
            "hostname": "gpu018",
            "osystem": "ubuntu",
            "distro_series": "jammy",
            "hwe_kernel": "ga-22.04",
        }
        src = FakeMigrateClient(src_data)
        tgt = FakeMigrateClient(_make_machine("Ready", []))
        out = _migrate_sync_metadata(src, "src", tgt, "tgt", dry_run=True)
        assert out["would_update"]["osystem"] == "ubuntu"
        assert out["would_update"]["distro_series"] == "jammy"
        assert out["would_update"]["hwe_kernel"] == "ga-22.04"


# ---------------------------------------------------------------------------
# _migrate_sync_disks tests
# ---------------------------------------------------------------------------


def _disk(name: str, model: str, serial: str, size: int = 960197124096) -> dict:
    return {
        "name": name,
        "model": model,
        "serial": serial,
        "size": size,
        "block_size": 512,
        "type": "physical",
    }


class FakeDiskClient(FakeMigrateClient):
    """Extends FakeMigrateClient with blockdevice_set on the machine."""

    def __init__(self, machine: dict) -> None:
        super().__init__(machine)


class TestMigrateSyncDisks:
    def test_dry_run_plans_create(self):
        src = FakeDiskClient(
            {
                "system_id": "src",
                "blockdevice_set": [_disk("nvme0n1", "Micron_7450", "AAA111")],
            }
        )
        tgt = FakeDiskClient(
            {
                "system_id": "tgt",
                "status_name": "Ready",
                "blockdevice_set": [],
            }
        )
        out = _migrate_sync_disks(src, "src", tgt, "tgt", dry_run=True)
        assert out["ok"] is True
        assert len(out["would_create"]) == 1
        assert out["would_create"][0]["name"] == "nvme0n1"
        assert not tgt.post_calls

    def test_write_creates_disk(self):
        src = FakeDiskClient(
            {
                "system_id": "src",
                "blockdevice_set": [_disk("nvme0n1", "Micron_7450", "AAA111")],
            }
        )
        tgt = FakeDiskClient(
            {
                "system_id": "tgt",
                "status_name": "Ready",
                "blockdevice_set": [],
            }
        )
        out = _migrate_sync_disks(src, "src", tgt, "tgt", dry_run=False)
        assert out["ok"] is True
        assert len(out["created"]) == 1
        assert tgt.post_calls
        path, data, _params = tgt.post_calls[0]
        assert "blockdevices" in path
        assert data["serial"] == "AAA111"

    def test_skips_existing_serial(self):
        disk = _disk("nvme0n1", "Micron_7450", "AAA111")
        src = FakeDiskClient({"system_id": "src", "blockdevice_set": [disk]})
        tgt = FakeDiskClient(
            {
                "system_id": "tgt",
                "status_name": "Ready",
                "blockdevice_set": [disk],
            }
        )
        out = _migrate_sync_disks(src, "src", tgt, "tgt", dry_run=True)
        assert len(out["would_create"]) == 0
        assert len(out["skipped"]) == 1


# ---------------------------------------------------------------------------
# _migrate_sync_tags tests
# ---------------------------------------------------------------------------


class FakeTagClient(FakeMigrateClient):
    """Returns tag_names + a tags list."""

    def get(self, path: str, params: dict | None = None):  # type: ignore[no-untyped-def]
        if "machines/" in path:
            return self.machine
        if path == "tags":
            return self.machine.get("_all_tags", [])
        raise AssertionError(f"unexpected get {path}")


class TestMigrateSyncTags:
    def test_dry_run_plans_tags(self):
        src = FakeTagClient(
            {
                "system_id": "src",
                "tag_names": ["forge_b65c909e", "node_b65c909e-29"],
            }
        )
        tgt = FakeTagClient(
            {
                "system_id": "tgt",
                "status_name": "Ready",
                "tag_names": [],
                "_all_tags": [],
            }
        )
        out = _migrate_sync_tags(src, "src", tgt, "tgt", dry_run=True)
        assert out["ok"] is True
        assert len(out["would_add_to_machine"]) == 2
        assert len(out["would_create_tags"]) == 2

    def test_write_creates_and_adds(self):
        src = FakeTagClient(
            {
                "system_id": "src",
                "tag_names": ["forge_b65c909e"],
            }
        )
        tgt = FakeTagClient(
            {
                "system_id": "tgt",
                "status_name": "Ready",
                "tag_names": [],
                "_all_tags": [],
            }
        )
        out = _migrate_sync_tags(src, "src", tgt, "tgt", dry_run=False)
        assert out["ok"] is True
        assert "forge_b65c909e" in out["tags_created"]
        assert "forge_b65c909e" in out["tags_added"]

    def test_skips_existing_tags(self):
        src = FakeTagClient(
            {
                "system_id": "src",
                "tag_names": ["already_there"],
            }
        )
        tgt = FakeTagClient(
            {
                "system_id": "tgt",
                "status_name": "Ready",
                "tag_names": ["already_there"],
                "_all_tags": [{"name": "already_there"}],
            }
        )
        out = _migrate_sync_tags(src, "src", tgt, "tgt", dry_run=True)
        assert len(out["already_on_machine"]) == 1
        assert len(out["would_add_to_machine"]) == 0

    def test_no_tags_skips(self):
        src = FakeTagClient({"system_id": "src", "tag_names": []})
        tgt = FakeTagClient({"system_id": "tgt", "status_name": "Ready", "tag_names": []})
        out = _migrate_sync_tags(src, "src", tgt, "tgt", dry_run=True)
        assert out.get("skipped") is True


# ---------------------------------------------------------------------------
# _migrate_sync_hardware_info tests
# ---------------------------------------------------------------------------


class TestMigrateSyncHardwareInfo:
    def test_dry_run_lists_keys(self):
        src = FakeMigrateClient(
            {
                "system_id": "src",
                "hardware_info": {
                    "system_vendor": "Supermicro",
                    "cpu_model": "AMD EPYC 9534",
                    "system_serial": "S923069X4415576",
                },
            }
        )
        tgt = FakeMigrateClient(
            {
                "system_id": "tgt",
                "id": 42,
                "status_name": "Ready",
                "interface_set": [],
            }
        )
        out = _migrate_sync_hardware_info(
            src,
            "src",
            tgt,
            "tgt",
            dry_run=True,
            db_url="postgresql://fake/test",
        )
        assert out["ok"] is True
        assert "system_vendor" in out["would_write"]
        assert out["db_configured"] is True

    def test_dry_run_without_db_url_still_ok(self):
        src = FakeMigrateClient(
            {
                "system_id": "src",
                "hardware_info": {"system_vendor": "Supermicro"},
            }
        )
        tgt = FakeMigrateClient({"system_id": "tgt", "status_name": "Ready", "interface_set": []})
        out = _migrate_sync_hardware_info(src, "src", tgt, "tgt", dry_run=True, db_url=None)
        assert out["ok"] is True
        assert out["db_configured"] is False

    def test_no_db_url_errors_on_write(self):
        src = FakeMigrateClient(
            {
                "system_id": "src",
                "hardware_info": {"system_vendor": "Supermicro"},
            }
        )
        tgt = FakeMigrateClient({"system_id": "tgt", "status_name": "Ready", "interface_set": []})
        out = _migrate_sync_hardware_info(src, "src", tgt, "tgt", dry_run=False, db_url=None)
        assert out["ok"] is False
        assert "database" in out["error"].lower()

    def test_filters_unknown_values(self):
        src = FakeMigrateClient(
            {
                "system_id": "src",
                "hardware_info": {
                    "system_vendor": "Supermicro",
                    "cpu_model": "Unknown",
                    "chassis_type": "",
                },
            }
        )
        tgt = FakeMigrateClient({"system_id": "tgt", "status_name": "Ready", "interface_set": []})
        out = _migrate_sync_hardware_info(
            src,
            "src",
            tgt,
            "tgt",
            dry_run=True,
            db_url="postgresql://fake/test",
        )
        assert "system_vendor" in out["would_write"]
        assert "cpu_model" not in out["would_write"]
        assert "chassis_type" not in out["would_write"]

    def test_empty_hardware_info_skips(self):
        src = FakeMigrateClient(
            {
                "system_id": "src",
                "hardware_info": {"system_vendor": "Unknown", "cpu_model": "Unknown"},
            }
        )
        tgt = FakeMigrateClient({"system_id": "tgt", "status_name": "Ready", "interface_set": []})
        out = _migrate_sync_hardware_info(
            src, "src", tgt, "tgt", dry_run=True, db_url="postgresql://f/t"
        )
        assert out.get("skipped") is True


# ---------------------------------------------------------------------------
# _migrate_sync_interfaces rename tests
# ---------------------------------------------------------------------------


class TestMigrateSyncInterfacesRename:
    def test_dry_run_detects_rename(self):
        shared_mac = "aa:bb:cc:dd:ee:01"
        src = FakeMigrateClient(
            _make_source_machine(
                [
                    _phys(1, "enp48s0np0", shared_mac),
                ]
            )
        )
        tgt = FakeMigrateClient(
            _make_machine(
                "Ready",
                [
                    _phys(100, "eth0", shared_mac),
                ],
            )
        )
        out = _migrate_sync_interfaces(src, "src", tgt, "tgt", dry_run=True)
        assert out["ok"] is True
        assert len(out["would_rename"]) == 1
        assert out["would_rename"][0]["old_name"] == "eth0"
        assert out["would_rename"][0]["new_name"] == "enp48s0np0"

    def test_write_renames_interface(self):
        shared_mac = "aa:bb:cc:dd:ee:01"
        src = FakeMigrateClient(
            _make_source_machine(
                [
                    _phys(1, "enp48s0np0", shared_mac),
                ]
            )
        )
        tgt = FakeMigrateClient(
            _make_machine(
                "Ready",
                [
                    _phys(100, "eth0", shared_mac),
                ],
            )
        )
        out = _migrate_sync_interfaces(src, "src", tgt, "tgt", dry_run=False)
        assert out["ok"] is True
        assert len(out["renamed"]) == 1
        assert out["renamed"][0]["new_name"] == "enp48s0np0"
        assert tgt.put_calls
        path, data = tgt.put_calls[0]
        assert path == "nodes/tgt/interfaces/100"
        assert data["name"] == "enp48s0np0"

    def test_no_rename_when_names_match(self):
        shared_mac = "aa:bb:cc:dd:ee:01"
        src = FakeMigrateClient(
            _make_source_machine(
                [
                    _phys(1, "enp48s0np0", shared_mac),
                ]
            )
        )
        tgt = FakeMigrateClient(
            _make_machine(
                "Ready",
                [
                    _phys(100, "enp48s0np0", shared_mac),
                ],
            )
        )
        out = _migrate_sync_interfaces(src, "src", tgt, "tgt", dry_run=True)
        assert out["would_rename"] == []

    def test_write_returns_renamed_empty_list(self):
        shared_mac = "aa:bb:cc:dd:ee:01"
        src = FakeMigrateClient(
            _make_source_machine(
                [
                    _phys(1, "enp48s0np0", shared_mac),
                ]
            )
        )
        tgt = FakeMigrateClient(
            _make_machine(
                "Ready",
                [
                    _phys(100, "enp48s0np0", shared_mac),
                ],
            )
        )
        out = _migrate_sync_interfaces(src, "src", tgt, "tgt", dry_run=False)
        assert out["renamed"] == []


# ---------------------------------------------------------------------------
# _migrate_sync_metadata pool/zone auto-creation tests
# ---------------------------------------------------------------------------


class TestMigrateSyncMetadataAutoCreate:
    def _source(self) -> dict:
        return {
            "system_id": "src",
            "hostname": "gpu018",
            "architecture": "amd64/generic",
            "zone": {"name": "new-zone", "id": 99},
            "pool": {"name": "new-pool", "id": 88},
        }

    def test_auto_creates_zone_and_pool(self):
        src = FakeMigrateClient({"system_id": "src", **self._source()})
        tgt = FakeMetadataClient(
            _make_machine("Ready", []),
            zones=[{"name": "default"}],
            pools=[{"name": "default"}],
        )
        out = _migrate_sync_metadata(src, "src", tgt, "tgt", dry_run=False)
        assert out["ok"] is True
        assert "zone:new-zone" in out.get("auto_created", [])
        assert "pool:new-pool" in out.get("auto_created", [])
        post_paths = [p for p, _d, _kw in tgt.post_calls]
        assert "zones" in post_paths
        assert "resourcepools" in post_paths

    def test_skips_existing_zone_and_pool(self):
        src = FakeMigrateClient({"system_id": "src", **self._source()})
        tgt = FakeMetadataClient(
            _make_machine("Ready", []),
            zones=[{"name": "new-zone"}],
            pools=[{"name": "new-pool"}],
        )
        out = _migrate_sync_metadata(src, "src", tgt, "tgt", dry_run=False)
        assert out["ok"] is True
        assert out.get("auto_created") is None or len(out.get("auto_created", [])) == 0
        post_paths = [p for p, _d, _kw in tgt.post_calls]
        assert "zones" not in post_paths
        assert "resourcepools" not in post_paths


# ---------------------------------------------------------------------------
# _migrate_sync_disks partition tests
# ---------------------------------------------------------------------------


class TestMigrateSyncDisksPartitions:
    def _disk_with_parts(self) -> dict:
        return {
            "name": "nvme0n1",
            "model": "Micron_7450",
            "serial": "AAA111",
            "size": 960197124096,
            "block_size": 512,
            "type": "physical",
            "partition_table_type": "GPT",
            "partitions": [
                {
                    "uuid": "part-uuid-1",
                    "size": 500000000,
                    "bootable": True,
                    "index": 1,
                    "filesystem": {
                        "uuid": "fs-uuid-1",
                        "fstype": "ext4",
                        "mount_point": "/boot",
                        "mount_options": "",
                    },
                },
                {
                    "uuid": "part-uuid-2",
                    "size": 460000000000,
                    "bootable": False,
                    "index": 2,
                    "filesystem": None,
                },
            ],
        }

    def test_dry_run_shows_partition_plan(self):
        src = FakeDiskClient(
            {
                "system_id": "src",
                "blockdevice_set": [self._disk_with_parts()],
            }
        )
        tgt = FakeDiskClient(
            {
                "system_id": "tgt",
                "status_name": "Ready",
                "blockdevice_set": [],
            }
        )
        out = _migrate_sync_disks(src, "src", tgt, "tgt", dry_run=True)
        assert out["ok"] is True
        assert len(out["would_create_partitions"]) == 1
        plan = out["would_create_partitions"][0]
        assert plan["table_type"] == "GPT"
        assert len(plan["partitions"]) == 2
        assert plan["partitions"][0]["filesystem"]["fstype"] == "ext4"

    def test_dry_run_partition_db_not_configured(self):
        src = FakeDiskClient(
            {
                "system_id": "src",
                "blockdevice_set": [self._disk_with_parts()],
            }
        )
        tgt = FakeDiskClient(
            {
                "system_id": "tgt",
                "status_name": "Ready",
                "blockdevice_set": [],
            }
        )
        out = _migrate_sync_disks(src, "src", tgt, "tgt", dry_run=True, db_url=None)
        assert out["partition_db_configured"] is False

    def test_write_without_db_url_still_creates_disks(self):
        src = FakeDiskClient(
            {
                "system_id": "src",
                "blockdevice_set": [self._disk_with_parts()],
            }
        )
        tgt = FakeDiskClient(
            {
                "system_id": "tgt",
                "status_name": "Ready",
                "blockdevice_set": [],
            }
        )
        out = _migrate_sync_disks(src, "src", tgt, "tgt", dry_run=False, db_url=None)
        assert out["ok"] is True
        assert len(out["created"]) == 1
        assert "partitions_created" not in out

    def test_disk_without_partitions_no_plan(self):
        src = FakeDiskClient(
            {
                "system_id": "src",
                "blockdevice_set": [_disk("nvme0n1", "Micron_7450", "AAA111")],
            }
        )
        tgt = FakeDiskClient(
            {
                "system_id": "tgt",
                "status_name": "Ready",
                "blockdevice_set": [],
            }
        )
        out = _migrate_sync_disks(src, "src", tgt, "tgt", dry_run=True)
        assert "would_create_partitions" not in out


# ---------------------------------------------------------------------------
# _migrate_sync_numa_and_devices tests
# ---------------------------------------------------------------------------


class FakeNumaClient(FakeMigrateClient):
    """Fake client that also handles /nodes/{sid}/devices."""

    def __init__(self, machine: dict, devices: list | None = None) -> None:
        super().__init__(machine)
        self._devices = devices or []

    def get(self, path: str, params: dict | None = None):  # type: ignore[no-untyped-def]
        if "/devices" in path:
            return self._devices
        return super().get(path, params)


class TestMigrateSyncNumaAndDevices:
    def _source_machine(self) -> dict:
        return {
            "system_id": "src",
            "numanode_set": [
                {"index": 0, "memory": 65536, "cores": [0, 1, 2, 3]},
                {"index": 1, "memory": 65536, "cores": [4, 5, 6, 7]},
            ],
            "interface_set": [
                {
                    "type": "physical",
                    "mac_address": "aa:bb:cc:dd:ee:01",
                    "numa_node": 0,
                    "link_speed": 100000,
                    "interface_speed": 100000,
                    "link_connected": True,
                },
            ],
        }

    def _devices(self) -> list:
        return [
            {
                "bus": 1,
                "hardware_type": 1,
                "vendor_id": "10de",
                "product_id": "2330",
                "vendor_name": "NVIDIA",
                "product_name": "H100",
                "commissioning_driver": "nvidia",
                "bus_number": 1,
                "device_number": 0,
                "pci_address": "0000:00:1f.0",
                "numa_node": 0,
            },
        ]

    def test_dry_run_reports_counts(self):
        src = FakeNumaClient(self._source_machine(), self._devices())
        tgt = FakeMigrateClient(
            {
                "system_id": "tgt",
                "status_name": "Ready",
                "interface_set": [],
            }
        )
        out = _migrate_sync_numa_and_devices(
            src,
            "src",
            tgt,
            "tgt",
            dry_run=True,
            db_url="postgresql://fake/test",
        )
        assert out["ok"] is True
        assert out["dry_run"] is True
        assert out["would_sync_numa_nodes"] == 2
        assert out["would_sync_devices"] == 1
        assert out["db_configured"] is True

    def test_dry_run_without_db_url(self):
        src = FakeNumaClient(self._source_machine(), self._devices())
        tgt = FakeMigrateClient(
            {
                "system_id": "tgt",
                "status_name": "Ready",
                "interface_set": [],
            }
        )
        out = _migrate_sync_numa_and_devices(
            src,
            "src",
            tgt,
            "tgt",
            dry_run=True,
            db_url=None,
        )
        assert out["ok"] is True
        assert out["db_configured"] is False

    def test_write_without_db_url_errors(self):
        src = FakeNumaClient(self._source_machine(), self._devices())
        tgt = FakeMigrateClient(
            {
                "system_id": "tgt",
                "status_name": "Ready",
                "interface_set": [],
            }
        )
        out = _migrate_sync_numa_and_devices(
            src,
            "src",
            tgt,
            "tgt",
            dry_run=False,
            db_url=None,
        )
        assert out["ok"] is False
        assert "database" in out["error"].lower()

    def test_empty_numa_dry_run(self):
        src = FakeNumaClient(
            {
                "system_id": "src",
                "numanode_set": [],
                "interface_set": [],
            }
        )
        tgt = FakeMigrateClient(
            {
                "system_id": "tgt",
                "status_name": "Ready",
                "interface_set": [],
            }
        )
        out = _migrate_sync_numa_and_devices(
            src,
            "src",
            tgt,
            "tgt",
            dry_run=True,
            db_url="postgresql://fake/test",
        )
        assert out["ok"] is True
        assert out["would_sync_numa_nodes"] == 0
        assert out["would_sync_devices"] == 0


# ---------------------------------------------------------------------------
# _get_boot_interface_mac tests
# ---------------------------------------------------------------------------


class TestGetBootInterfaceMac:
    def test_extracts_mac(self):
        machine = {"boot_interface": {"mac_address": "AA:BB:CC:DD:EE:01"}}
        assert _get_boot_interface_mac(machine) == "aa:bb:cc:dd:ee:01"

    def test_none_when_missing(self):
        assert _get_boot_interface_mac({}) is None
        assert _get_boot_interface_mac({"boot_interface": None}) is None

    def test_none_when_no_mac(self):
        assert _get_boot_interface_mac({"boot_interface": {"id": 5}}) is None


# ---------------------------------------------------------------------------
# _migrate_set_commissioning_scriptset tests
# ---------------------------------------------------------------------------


class TestMigrateSetCommissioningScriptset:
    def test_dry_run(self):
        out = _migrate_set_commissioning_scriptset("tgt1", dry_run=True, db_url="pg://fake")
        assert out["ok"] is True
        assert out["dry_run"] is True
        assert out["would_create_scriptset"] is True
        assert out["db_configured"] is True

    def test_dry_run_no_db(self):
        out = _migrate_set_commissioning_scriptset("tgt1", dry_run=True, db_url=None)
        assert out["ok"] is True
        assert out["db_configured"] is False

    def test_write_no_db_errors(self):
        out = _migrate_set_commissioning_scriptset("tgt1", dry_run=False, db_url=None)
        assert out["ok"] is False
        assert "database" in out["error"].lower()


# ---------------------------------------------------------------------------
# _migrate_set_deployed tests
# ---------------------------------------------------------------------------


class TestMigrateSetDeployed:
    def test_dry_run(self):
        out = _migrate_set_deployed("tgt1", dry_run=True, db_url="pg://fake")
        assert out["ok"] is True
        assert out["dry_run"] is True
        assert out["would_set_status"] == 6
        assert out["db_configured"] is True

    def test_dry_run_no_db(self):
        out = _migrate_set_deployed("tgt1", dry_run=True, db_url=None)
        assert out["ok"] is True
        assert out["db_configured"] is False

    def test_write_no_db_errors(self):
        out = _migrate_set_deployed("tgt1", dry_run=False, db_url=None)
        assert out["ok"] is False
        assert "database" in out["error"].lower()


# ---------------------------------------------------------------------------
# _migrate_sync_interfaces boot_interface dry_run tests
# ---------------------------------------------------------------------------


class TestMigrateSyncInterfacesBootIface:
    def test_dry_run_reports_boot_interface(self):
        src_machine = _make_source_machine(
            [
                _phys(1, "enp48s0np0", "aa:bb:cc:dd:ee:01"),
            ]
        )
        src_machine["boot_interface"] = {"mac_address": "AA:BB:CC:DD:EE:01", "id": 1}
        src = FakeMigrateClient(src_machine)
        tgt = FakeMigrateClient(_make_machine("New", []))
        out = _migrate_sync_interfaces(src, "src", tgt, "tgt", dry_run=True, db_url="pg://fake")
        assert out["would_set_boot_interface_mac"] == "aa:bb:cc:dd:ee:01"
        assert out["boot_interface_db_configured"] is True

    def test_dry_run_no_boot_iface_on_source(self):
        src = FakeMigrateClient(
            _make_source_machine(
                [
                    _phys(1, "enp48s0np0", "aa:bb:cc:dd:ee:01"),
                ]
            )
        )
        tgt = FakeMigrateClient(_make_machine("New", []))
        out = _migrate_sync_interfaces(src, "src", tgt, "tgt", dry_run=True)
        assert "would_set_boot_interface_mac" not in out
