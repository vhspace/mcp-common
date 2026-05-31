"""Tests for network profile extraction and MAC-based interface matching."""

from maas_mcp.netbox_helper import extract_network_profile, match_interfaces_by_mac

# Realistic interface_set from legacy MAAS (ORI-TX gpu001 pattern)
LEGACY_INTERFACE_SET = [
    {
        "id": 2929,
        "name": "enp48s0np0",
        "type": "physical",
        "mac_address": "58:a2:e1:2d:df:b0",
        "effective_mtu": 4092,
        "params": {"mtu": 4092},
        "children": ["bond0"],
        "links": [],
        "vlan": {"vid": 0, "fabric": "01-in-band-vlan1229", "fabric_id": 316, "name": "untagged"},
    },
    {
        "id": 2931,
        "name": "enp211s0np0",
        "type": "physical",
        "mac_address": "58:a2:e1:2d:e0:50",
        "effective_mtu": 4092,
        "params": {"mtu": 4092},
        "children": ["bond0"],
        "links": [],
        "vlan": {"vid": 0, "fabric": "01-in-band-vlan1229", "fabric_id": 316, "name": "untagged"},
    },
    {
        "id": 2932,
        "name": "enp51s0f0",
        "type": "physical",
        "mac_address": "7c:c2:55:7b:3c:a0",
        "effective_mtu": 1500,
        "params": "",
        "children": [],
        "links": [],
        "vlan": None,
    },
    {
        "id": 2933,
        "name": "enp51s0f1",
        "type": "physical",
        "mac_address": "7c:c2:55:7b:3c:a1",
        "effective_mtu": 1500,
        "params": "",
        "children": [],
        "links": [],
        "vlan": None,
    },
    {
        "id": 3066,
        "name": "bond0",
        "type": "bond",
        "mac_address": "58:a2:e1:2d:df:b0",
        "effective_mtu": 4092,
        "params": {
            "bond_downdelay": 0,
            "bond_lacp_rate": "fast",
            "bond_miimon": 100,
            "bond_mode": "active-backup",
            "bond_num_grat_arp": 1,
            "bond_updelay": 0,
            "bond_xmit_hash_policy": "layer2",
            "mtu": 4092,
        },
        "parents": ["enp211s0np0", "enp48s0np0"],
        "links": [
            {
                "id": 4153,
                "mode": "static",
                "ip_address": "192.168.229.1",
                "subnet": {
                    "cidr": "192.168.229.0/24",
                    "gateway_ip": "192.168.229.254",
                    "dns_servers": ["1.1.1.1", "8.8.8.8"],
                },
            }
        ],
        "vlan": {"vid": 0, "fabric": "01-in-band-vlan1229", "fabric_id": 316, "name": "untagged"},
    },
]

LEGACY_MACHINE = {
    "hostname": "gpu001",
    "interface_set": LEGACY_INTERFACE_SET,
}


class TestExtractNetworkProfile:
    def test_extracts_bond(self):
        profile = extract_network_profile(LEGACY_MACHINE)
        assert len(profile["bonds"]) == 1
        bond = profile["bonds"][0]
        assert bond["name"] == "bond0"
        assert sorted(bond["parents"]) == ["enp211s0np0", "enp48s0np0"]

    def test_bond_params(self):
        profile = extract_network_profile(LEGACY_MACHINE)
        params = profile["bonds"][0]["params"]
        assert params["bond_mode"] == "active-backup"
        assert params["bond_miimon"] == 100
        assert params["bond_lacp_rate"] == "fast"
        assert params["bond_xmit_hash_policy"] == "layer2"
        assert params["bond_num_grat_arp"] == 1
        assert params["mtu"] == 4092

    def test_bond_links(self):
        profile = extract_network_profile(LEGACY_MACHINE)
        links = profile["bonds"][0]["links"]
        assert len(links) == 1
        assert links[0]["mode"] == "static"
        assert links[0]["ip_address"] == "192.168.229.1"
        assert links[0]["subnet_cidr"] == "192.168.229.0/24"

    def test_physical_interfaces(self):
        profile = extract_network_profile(LEGACY_MACHINE)
        phys = profile["physical_interfaces"]
        assert len(phys) == 4
        names = {p["name"] for p in phys}
        assert names == {"enp48s0np0", "enp211s0np0", "enp51s0f0", "enp51s0f1"}

    def test_bond_parent_annotation(self):
        profile = extract_network_profile(LEGACY_MACHINE)
        phys_by_name = {p["name"]: p for p in profile["physical_interfaces"]}
        assert phys_by_name["enp48s0np0"]["bond_parent"] == "bond0"
        assert phys_by_name["enp211s0np0"]["bond_parent"] == "bond0"
        assert phys_by_name["enp51s0f0"]["bond_parent"] is None
        assert phys_by_name["enp51s0f1"]["bond_parent"] is None

    def test_mtu_on_physical(self):
        profile = extract_network_profile(LEGACY_MACHINE)
        phys_by_name = {p["name"]: p for p in profile["physical_interfaces"]}
        assert phys_by_name["enp48s0np0"]["mtu"] == 4092
        assert phys_by_name["enp51s0f0"]["mtu"] == 1500

    def test_gateway(self):
        profile = extract_network_profile(LEGACY_MACHINE)
        assert profile["gateway"] == "192.168.229.254"

    def test_dns_servers(self):
        profile = extract_network_profile(LEGACY_MACHINE)
        assert profile["dns_servers"] == ["1.1.1.1", "8.8.8.8"]

    def test_hostname(self):
        profile = extract_network_profile(LEGACY_MACHINE)
        assert profile["hostname"] == "gpu001"

    def test_no_bonds(self):
        machine = {
            "hostname": "simple",
            "interface_set": [
                {
                    "id": 1,
                    "name": "eth0",
                    "type": "physical",
                    "mac_address": "aa:bb:cc:dd:ee:ff",
                    "effective_mtu": 1500,
                    "params": {},
                    "links": [],
                    "children": [],
                }
            ],
        }
        profile = extract_network_profile(machine)
        assert profile["bonds"] == []
        assert len(profile["physical_interfaces"]) == 1

    def test_empty_interface_set(self):
        profile = extract_network_profile({"hostname": "empty", "interface_set": []})
        assert profile["bonds"] == []
        assert profile["physical_interfaces"] == []

    def test_vlan_info(self):
        profile = extract_network_profile(LEGACY_MACHINE)
        vlan = profile["bonds"][0]["vlan"]
        assert vlan["vid"] == 0
        assert vlan["fabric"] == "01-in-band-vlan1229"


# New MAAS interface_set (same MACs, different IDs, no bonds)
NEW_MAAS_INTERFACE_SET = [
    {"id": 9051, "name": "enp48s0np0", "type": "physical", "mac_address": "58:a2:e1:2d:df:b0"},
    {"id": 9069, "name": "enp211s0np0", "type": "physical", "mac_address": "58:a2:e1:2d:e0:50"},
    {"id": 9070, "name": "enp51s0f0", "type": "physical", "mac_address": "7c:c2:55:7b:3c:a0"},
    {"id": 9071, "name": "enp51s0f1", "type": "physical", "mac_address": "7c:c2:55:7b:3c:a1"},
]


class TestMatchInterfacesByMac:
    def test_maps_all_interfaces(self):
        profile = extract_network_profile(LEGACY_MACHINE)
        mapping = match_interfaces_by_mac(profile, NEW_MAAS_INTERFACE_SET)
        assert mapping["enp48s0np0"] == 9051
        assert mapping["enp211s0np0"] == 9069
        assert mapping["enp51s0f0"] == 9070
        assert mapping["enp51s0f1"] == 9071

    def test_partial_match(self):
        profile = extract_network_profile(LEGACY_MACHINE)
        partial_target = [
            {"id": 100, "name": "eth0", "mac_address": "58:a2:e1:2d:df:b0"},
        ]
        mapping = match_interfaces_by_mac(profile, partial_target)
        assert mapping["enp48s0np0"] == 100
        assert "enp211s0np0" not in mapping

    def test_empty_target(self):
        profile = extract_network_profile(LEGACY_MACHINE)
        mapping = match_interfaces_by_mac(profile, [])
        assert mapping == {}

    def test_case_insensitive_mac(self):
        profile = extract_network_profile(LEGACY_MACHINE)
        target = [{"id": 42, "mac_address": "58:A2:E1:2D:DF:B0"}]
        mapping = match_interfaces_by_mac(profile, target)
        assert mapping["enp48s0np0"] == 42
