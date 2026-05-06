"""Tests for helpers: DN normalization, HBAC evaluation, hostgroup diff, native hbactest."""

from __future__ import annotations

from unittest.mock import MagicMock

from ipa_mcp.helpers import (
    dn_to_name,
    hbac_evaluate,
    hostgroup_diff,
    normalize_members,
    resolve_hbac_access,
)
from tests.conftest import (
    HBACRULE_FIND_ALL_RESPONSE,
    HOST_SHOW_RESPONSE,
    NATIVE_HBACTEST_RESPONSE,
    USER_SHOW_RESPONSE,
)


class TestDnToName:
    def test_cn(self) -> None:
        assert dn_to_name("cn=admins,cn=groups,cn=accounts,dc=example,dc=com") == "admins"

    def test_uid(self) -> None:
        assert dn_to_name("uid=alice,cn=users,cn=accounts,dc=example,dc=com") == "alice"

    def test_fqdn(self) -> None:
        dn = "fqdn=node1.cloud.together.ai,cn=computers,cn=accounts,dc=cloud,dc=together,dc=ai"
        assert dn_to_name(dn) == "node1.cloud.together.ai"

    def test_passthrough(self) -> None:
        assert dn_to_name("just-a-name") == "just-a-name"


class TestNormalizeMembers:
    def test_user_dns(self) -> None:
        record = {
            "cn": ["mygroup"],
            "member_user": [
                "uid=alice,cn=users,cn=accounts,dc=example,dc=com",
                "uid=bob,cn=users,cn=accounts,dc=example,dc=com",
            ],
        }
        result = normalize_members(record)
        assert result["member_users"] == ["alice", "bob"]
        assert result["raw_member_user"] == record["member_user"]
        assert result["cn"] == ["mygroup"]

    def test_host_dns(self) -> None:
        record = {
            "cn": ["hg_test"],
            "member_host": [
                "fqdn=node1.example.com,cn=computers,cn=accounts,dc=example,dc=com",
            ],
        }
        result = normalize_members(record)
        assert result["member_hosts"] == ["node1.example.com"]

    def test_hostgroup_dns(self) -> None:
        record = {
            "cn": ["hbac_rule"],
            "memberhost_hostgroup": [
                "cn=hg_forge_test,cn=hostgroups,cn=accounts,dc=example,dc=com",
            ],
        }
        result = normalize_members(record)
        assert result["member_hostgroups"] == ["hg_forge_test"]

    def test_no_dn_fields(self) -> None:
        record = {"cn": ["simple"], "description": ["no DNs here"]}
        result = normalize_members(record)
        assert result == record

    def test_memberuser_group(self) -> None:
        record = {
            "memberuser_group": [
                "cn=ug_forge_test,cn=groups,cn=accounts,dc=example,dc=com",
            ],
        }
        result = normalize_members(record)
        assert result["member_groups"] == ["ug_forge_test"]

    def test_memberhost_host(self) -> None:
        record = {
            "memberhost_host": [
                "fqdn=h1.example.com,cn=computers,cn=accounts,dc=example,dc=com",
            ],
        }
        result = normalize_members(record)
        assert result["member_hosts"] == ["h1.example.com"]


class TestHbacEvaluate:
    def test_usercategory_all_grants_access(self) -> None:
        rules = [
            {
                "cn": ["allow_all"],
                "ipaenabledflag": [True],
                "usercategory": ["all"],
                "hostcategory": ["all"],
                "servicecategory": ["all"],
            }
        ]
        result = hbac_evaluate(rules, "anyone", "anyhost.example.com", "sshd")
        assert result["access_granted"] is True
        assert "allow_all" in result["matched_rules"]

    def test_disabled_rule_skipped(self) -> None:
        rules = [
            {
                "cn": ["disabled"],
                "ipaenabledflag": [False],
                "usercategory": ["all"],
                "hostcategory": ["all"],
                "servicecategory": ["all"],
            }
        ]
        result = hbac_evaluate(rules, "alice", "host.example.com", "sshd")
        assert result["access_granted"] is False
        assert result["matched_rules"] == []

    def test_user_group_match(self) -> None:
        rules = [
            {
                "cn": ["forge_rule"],
                "ipaenabledflag": [True],
                "memberuser_group": ["cn=devs,cn=groups,cn=accounts,dc=example,dc=com"],
                "hostcategory": ["all"],
                "servicecategory": ["all"],
            }
        ]
        result = hbac_evaluate(rules, "alice", "host.example.com", "sshd", user_groups=["devs"])
        assert result["access_granted"] is True
        assert "forge_rule" in result["matched_rules"]
        assert "devs" in result["details"][0]["user_reason"]

    def test_direct_user_match(self) -> None:
        rules = [
            {
                "cn": ["direct_rule"],
                "ipaenabledflag": [True],
                "memberuser_user": ["alice"],
                "hostcategory": ["all"],
                "servicecategory": ["all"],
            }
        ]
        result = hbac_evaluate(rules, "alice", "host.example.com", "sshd")
        assert result["access_granted"] is True

    def test_host_group_match(self) -> None:
        rules = [
            {
                "cn": ["host_rule"],
                "ipaenabledflag": [True],
                "usercategory": ["all"],
                "memberhost_hostgroup": ["cn=hg_prod,cn=hostgroups,cn=accounts,dc=example,dc=com"],
                "servicecategory": ["all"],
            }
        ]
        result = hbac_evaluate(rules, "alice", "node1.example.com", "sshd", host_groups=["hg_prod"])
        assert result["access_granted"] is True
        assert "hg_prod" in result["details"][0]["host_reason"]

    def test_direct_host_match(self) -> None:
        rules = [
            {
                "cn": ["host_direct"],
                "ipaenabledflag": [True],
                "usercategory": ["all"],
                "memberhost_host": [
                    "fqdn=node1.example.com,cn=computers,cn=accounts,dc=example,dc=com"
                ],
                "servicecategory": ["all"],
            }
        ]
        result = hbac_evaluate(rules, "alice", "node1.example.com", "sshd")
        assert result["access_granted"] is True

    def test_no_matching_rules(self) -> None:
        rules = [
            {
                "cn": ["restricted"],
                "ipaenabledflag": [True],
                "memberuser_user": ["bob"],
                "hostcategory": ["all"],
                "servicecategory": ["all"],
            }
        ]
        result = hbac_evaluate(rules, "alice", "host.example.com", "sshd")
        assert result["access_granted"] is False

    def test_multiple_rules_match(self) -> None:
        rules = [
            {
                "cn": ["rule1"],
                "ipaenabledflag": [True],
                "usercategory": ["all"],
                "hostcategory": ["all"],
                "servicecategory": ["all"],
            },
            {
                "cn": ["rule2"],
                "ipaenabledflag": [True],
                "usercategory": ["all"],
                "hostcategory": ["all"],
                "servicecategory": ["all"],
            },
        ]
        result = hbac_evaluate(rules, "alice", "host.example.com", "sshd")
        assert result["access_granted"] is True
        assert len(result["matched_rules"]) == 2

    def test_service_match(self) -> None:
        rules = [
            {
                "cn": ["svc_rule"],
                "ipaenabledflag": [True],
                "usercategory": ["all"],
                "hostcategory": ["all"],
                "memberservice_hbacsvc": ["sshd"],
            }
        ]
        result = hbac_evaluate(rules, "alice", "host.example.com", "sshd")
        assert result["access_granted"] is True

    def test_method_is_client_side(self) -> None:
        rules = [
            {
                "cn": ["rule1"],
                "ipaenabledflag": [True],
                "usercategory": ["all"],
                "hostcategory": ["all"],
                "servicecategory": ["all"],
            }
        ]
        result = hbac_evaluate(rules, "alice", "host.example.com", "sshd")
        assert result["method"] == "client_side"


class TestResolveHbacAccess:
    def test_native_hbactest_used_when_available(self) -> None:
        """When IPA native hbactest works, it should be used as primary."""
        client = MagicMock()
        client.hbactest.return_value = NATIVE_HBACTEST_RESPONSE
        result = resolve_hbac_access(client, "alice", "node1.cloud.together.ai", "sshd")
        assert result["method"] == "native_hbactest"
        assert result["access_granted"] is True
        assert "allow_all_sshd" in result["matched_rules"]
        client.hbactest.assert_called_once_with(
            user="alice", targethost="node1.cloud.together.ai", service="sshd"
        )

    def test_falls_back_to_client_side_on_native_failure(self) -> None:
        """When native hbactest fails, should fall back to client-side."""
        client = MagicMock()
        client.hbactest.side_effect = RuntimeError("hbactest not supported")
        client.hbacrule_find.return_value = HBACRULE_FIND_ALL_RESPONSE
        client._call.side_effect = _mock_call_for_hbactest

        result = resolve_hbac_access(client, "alice", "node1.cloud.together.ai", "sshd")
        assert result["method"] == "client_side"
        assert result["access_granted"] is True

    def test_native_missing_value_field_triggers_fallback(self) -> None:
        """If native returns unexpected shape (no 'value'), fall back."""
        client = MagicMock()
        client.hbactest.return_value = {"result": {"unexpected": "shape"}}
        client.hbacrule_find.return_value = HBACRULE_FIND_ALL_RESPONSE
        client._call.side_effect = _mock_call_for_hbactest

        result = resolve_hbac_access(client, "alice", "node1.cloud.together.ai", "sshd")
        assert result["method"] == "client_side"

    def test_native_denied_access(self) -> None:
        """Native hbactest returning value=False should report denial."""
        client = MagicMock()
        client.hbactest.return_value = {
            "result": {"value": False, "matched": [], "notmatched": ["allow_all"]},
        }
        result = resolve_hbac_access(client, "bob", "node1.example.com", "sshd")
        assert result["method"] == "native_hbactest"
        assert result["access_granted"] is False
        assert result["matched_rules"] == []
        assert "allow_all" in result["notmatched_rules"]


def _mock_call_for_hbactest(method: str, args: list, kw: dict | None = None):
    if method == "user_show":
        return USER_SHOW_RESPONSE
    if method == "host_show":
        return HOST_SHOW_RESPONSE
    raise RuntimeError(f"unexpected call: {method}")


class TestHostgroupDiff:
    def test_basic_diff(self) -> None:
        result = hostgroup_diff(
            current_members=["node1.example.com", "node2.example.com"],
            expected_members=["node2.example.com", "node3.example.com"],
        )
        assert result["to_add"] == ["node3.example.com"]
        assert result["to_remove"] == ["node1.example.com"]
        assert result["unchanged"] == ["node2.example.com"]

    def test_empty_current(self) -> None:
        result = hostgroup_diff(
            current_members=[],
            expected_members=["a.example.com", "b.example.com"],
        )
        assert result["to_add"] == ["a.example.com", "b.example.com"]
        assert result["to_remove"] == []

    def test_empty_expected(self) -> None:
        result = hostgroup_diff(
            current_members=["a.example.com"],
            expected_members=[],
        )
        assert result["to_add"] == []
        assert result["to_remove"] == ["a.example.com"]

    def test_identical(self) -> None:
        members = ["a.example.com", "b.example.com"]
        result = hostgroup_diff(current_members=members, expected_members=members)
        assert result["to_add"] == []
        assert result["to_remove"] == []
        assert sorted(result["unchanged"]) == sorted(members)
