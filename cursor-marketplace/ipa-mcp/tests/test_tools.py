"""Tests for MCP tools using mocked client responses."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastmcp.exceptions import ToolError

from ipa_mcp.server import (
    ipa_add_group_members,
    ipa_add_hostgroup_members,
    ipa_create_group,
    ipa_create_hbac_rule,
    ipa_create_hostgroup,
    ipa_create_sudo_rule,
    ipa_hbactest_explain,
    ipa_hostgroup_diff,
    ipa_list_groups,
    ipa_list_hbac_rules,
    ipa_list_hostgroups,
    ipa_list_sudo_rules,
    ipa_remove_hostgroup_members,
    ipa_setup_forge,
    ipa_show_group,
    ipa_show_hbacrule,
    ipa_show_hostgroup,
    ipa_show_user,
)
from tests.conftest import (
    GROUP_ADD_MEMBER_RESPONSE,
    GROUP_ADD_RESPONSE,
    GROUP_FIND_RESPONSE,
    GROUP_SHOW_RESPONSE,
    HBACRULE_ADD_RESPONSE,
    HBACRULE_FIND_ALL_RESPONSE,
    HBACRULE_FIND_RESPONSE,
    HBACRULE_SHOW_RESPONSE,
    HOST_SHOW_RESPONSE,
    HOSTGROUP_ADD_MEMBER_RESPONSE,
    HOSTGROUP_ADD_RESPONSE,
    HOSTGROUP_FIND_RESPONSE,
    HOSTGROUP_REMOVE_MEMBER_RESPONSE,
    HOSTGROUP_SHOW_RESPONSE,
    HOSTGROUP_SHOW_WITH_MEMBERS,
    NATIVE_HBACTEST_RESPONSE,
    SUDORULE_ADD_RESPONSE,
    SUDORULE_FIND_RESPONSE,
    USER_SHOW_FULL_RESPONSE,
    USER_SHOW_RESPONSE,
)


@pytest.fixture
def mock_client():
    """Patch _get_client to return a mock with configurable method returns."""
    with patch("ipa_mcp.server._get_client") as get_client:
        mock = get_client.return_value
        yield mock


def test_list_groups(mock_client) -> None:
    """Mock group_find, verify tool returns the results."""
    mock_client.group_find.return_value = GROUP_FIND_RESPONSE
    result = ipa_list_groups(criteria="")
    assert result == GROUP_FIND_RESPONSE["result"]
    mock_client.group_find.assert_called_once_with("")


def test_list_hostgroups(mock_client) -> None:
    """Mock hostgroup_find."""
    mock_client.hostgroup_find.return_value = HOSTGROUP_FIND_RESPONSE
    result = ipa_list_hostgroups(criteria="")
    assert result == HOSTGROUP_FIND_RESPONSE["result"]
    mock_client.hostgroup_find.assert_called_once_with("")


def test_list_hbac_rules(mock_client) -> None:
    """Mock hbacrule_find."""
    mock_client.hbacrule_find.return_value = HBACRULE_FIND_RESPONSE
    result = ipa_list_hbac_rules(criteria="")
    assert result == HBACRULE_FIND_RESPONSE["result"]
    mock_client.hbacrule_find.assert_called_once_with("")


def test_list_sudo_rules(mock_client) -> None:
    """Mock sudorule_find."""
    mock_client.sudorule_find.return_value = SUDORULE_FIND_RESPONSE
    result = ipa_list_sudo_rules(criteria="")
    assert result == SUDORULE_FIND_RESPONSE["result"]
    mock_client.sudorule_find.assert_called_once_with("")


def test_create_group(mock_client) -> None:
    """Mock group_add, verify correct args."""
    mock_client.group_add.return_value = GROUP_ADD_RESPONSE
    result = ipa_create_group(name="ug_forge_test", description="Test group")
    assert result == GROUP_ADD_RESPONSE["result"]
    mock_client.group_add.assert_called_once_with("ug_forge_test", description="Test group")


def test_add_group_members(mock_client) -> None:
    """Mock group_add_member, verify user list."""
    mock_client.group_add_member.return_value = GROUP_ADD_MEMBER_RESPONSE
    result = ipa_add_group_members(name="ug_forge_test", users=["testuser"])
    assert result == GROUP_ADD_MEMBER_RESPONSE["result"]
    mock_client.group_add_member.assert_called_once_with("ug_forge_test", user=["testuser"])


def test_create_hbac_rule(mock_client) -> None:
    """Mock hbacrule_add, verify servicecategory=all."""
    mock_client.hbacrule_add.return_value = HBACRULE_ADD_RESPONSE
    result = ipa_create_hbac_rule(name="allow_forge_test", servicecat="all")
    assert result == HBACRULE_ADD_RESPONSE["result"]
    mock_client.hbacrule_add.assert_called_once_with("allow_forge_test", servicecat="all")


def test_create_sudo_rule(mock_client) -> None:
    """Mock sudorule_add, verify cmdcategory/runasuser/runasgroup=all."""
    mock_client.sudorule_add.return_value = SUDORULE_ADD_RESPONSE
    result = ipa_create_sudo_rule(
        name="allow_sudo_test",
        cmdcat="all",
        runasusercategory="all",
        runasgroupcategory="all",
    )
    assert result == SUDORULE_ADD_RESPONSE["result"]
    mock_client.sudorule_add.assert_called_once_with(
        "allow_sudo_test",
        cmdcat="all",
        ipasudorunasusercategory="all",
        ipasudorunasgroupcategory="all",
    )


def test_create_hostgroup(mock_client) -> None:
    """Mock hostgroup_add, verify correct args."""
    mock_client.hostgroup_add.return_value = HOSTGROUP_ADD_RESPONSE
    result = ipa_create_hostgroup(name="hg_forge_test", description="Test host group")
    assert result == HOSTGROUP_ADD_RESPONSE["result"]
    mock_client.hostgroup_add.assert_called_once_with(
        "hg_forge_test", description="Test host group"
    )


def test_add_hostgroup_members(mock_client) -> None:
    """Mock hostgroup_add_member, verify host list."""
    mock_client.hostgroup_add_member.return_value = HOSTGROUP_ADD_MEMBER_RESPONSE
    result = ipa_add_hostgroup_members(name="hg_forge_test", hosts=["host1.example.com"])
    assert result == HOSTGROUP_ADD_MEMBER_RESPONSE["result"]
    mock_client.hostgroup_add_member.assert_called_once_with(
        "hg_forge_test", host=["host1.example.com"]
    )


def test_remove_hostgroup_members(mock_client) -> None:
    """Mock hostgroup_remove_member, verify host list."""
    mock_client.hostgroup_remove_member.return_value = HOSTGROUP_REMOVE_MEMBER_RESPONSE
    result = ipa_remove_hostgroup_members(name="hg_forge_test", hosts=["host1.example.com"])
    assert result == HOSTGROUP_REMOVE_MEMBER_RESPONSE["result"]
    mock_client.hostgroup_remove_member.assert_called_once_with(
        "hg_forge_test", host=["host1.example.com"]
    )


def test_remove_hostgroup_members_no_args_raises(mock_client) -> None:
    with pytest.raises(ToolError):
        ipa_remove_hostgroup_members(name="x")


def test_remove_hostgroup_members_with_hostgroups(mock_client) -> None:
    """Mock hostgroup_remove_member with nested hostgroups."""
    mock_client.hostgroup_remove_member.return_value = HOSTGROUP_REMOVE_MEMBER_RESPONSE
    result = ipa_remove_hostgroup_members(name="hg_forge_test", hostgroups=["hg_nested"])
    assert result == HOSTGROUP_REMOVE_MEMBER_RESPONSE["result"]
    mock_client.hostgroup_remove_member.assert_called_once_with(
        "hg_forge_test", hostgroup=["hg_nested"]
    )


def test_setup_forge(mock_client) -> None:
    """Mock all calls in sequence, verify the full forge setup flow."""

    # _exists checks: group_show, hostgroup_show, hbacrule_show, sudorule_show
    # All should return False (raise) so we create everything
    def show_side_effect(name: str):
        raise RuntimeError("not found")

    mock_client.group_show.side_effect = show_side_effect
    mock_client.hostgroup_show.side_effect = show_side_effect
    mock_client.hbacrule_show.side_effect = show_side_effect
    mock_client.sudorule_show.side_effect = show_side_effect

    # sudorule_add_option may raise if !authenticate already set
    mock_client.sudorule_add_option.side_effect = RuntimeError("already set")

    result = ipa_setup_forge(name="testcluster", hosts=["host1.example.com"], users=["user1"])

    assert result["forge"] == "testcluster"
    assert result["user_group"] == "ug_forge_testcluster"
    assert result["host_group"] == "hg_forge_testcluster"
    assert result["hbac_rule"] == "allow_forge_testcluster"
    assert result["sudo_rule"] == "allow_sudo_testcluster"
    assert "log" in result

    # Verify creation order
    mock_client.group_add.assert_called_once_with(
        "ug_forge_testcluster", description="Forge cluster testcluster users"
    )
    mock_client.hostgroup_add.assert_called_once_with(
        "hg_forge_testcluster", description="Forge cluster testcluster hosts"
    )
    mock_client.hostgroup_add_member.assert_any_call(
        "hg_forge_testcluster", host=["host1.example.com"]
    )
    mock_client.group_add_member.assert_any_call("ug_forge_testcluster", user=["user1"])
    mock_client.hbacrule_add.assert_called_once_with(
        "allow_forge_testcluster", servicecat="all", description="Forge testcluster access"
    )
    mock_client.hbacrule_add_user.assert_called_with(
        "allow_forge_testcluster", group=["ug_forge_testcluster"]
    )
    mock_client.hbacrule_add_host.assert_any_call(
        "allow_forge_testcluster", hostgroup=["hg_forge_testcluster"]
    )
    mock_client.sudorule_add.assert_called_once_with(
        "allow_sudo_testcluster",
        cmdcat="all",
        ipasudorunasusercategory="all",
        ipasudorunasgroupcategory="all",
        description="Forge testcluster sudo",
    )
    mock_client.sudorule_add_user.assert_called_with(
        "allow_sudo_testcluster", group=["ug_forge_testcluster"]
    )
    mock_client.sudorule_add_host.assert_any_call(
        "allow_sudo_testcluster", hostgroup=["hg_forge_testcluster"]
    )
    mock_client.hbacrule_add_host.assert_any_call(
        "allow_forge_together_support", hostgroup=["hg_forge_testcluster"]
    )
    mock_client.sudorule_add_host.assert_any_call(
        "allow_sudo_together_forge-support", hostgroup=["hg_forge_testcluster"]
    )


# ── show tools with normalized members ───────────────────────────


def test_show_group(mock_client) -> None:
    mock_client.group_show.return_value = GROUP_SHOW_RESPONSE
    result = ipa_show_group(name="ug_forge_cartesia5")
    assert result["member_users"] == ["alice", "bob"]
    assert result["member_groups"] == ["admins"]
    assert "raw_member_user" in result
    assert "raw_member_group" in result


def test_show_hostgroup(mock_client) -> None:
    mock_client.hostgroup_show.return_value = HOSTGROUP_SHOW_RESPONSE
    result = ipa_show_hostgroup(name="hg_forge_cartesia5")
    assert "node1.cloud.together.ai" in result["member_hosts"]
    assert "node2.cloud.together.ai" in result["member_hosts"]
    assert "raw_member_host" in result


def test_show_hbacrule(mock_client) -> None:
    mock_client.hbacrule_show.return_value = HBACRULE_SHOW_RESPONSE
    result = ipa_show_hbacrule(name="allow_forge_cartesia5")
    assert result["member_users"] == ["alice"]
    assert result["member_groups"] == ["ug_forge_cartesia5"]
    assert result["member_hostgroups"] == ["hg_forge_cartesia5"]


def test_show_user(mock_client) -> None:
    mock_client.user_show.return_value = USER_SHOW_FULL_RESPONSE
    result = ipa_show_user(name="mballew")
    assert "admins" in result["memberof_group"]
    assert "allow_admin" in result["memberofindirect_hbacrule"]
    assert "tech-ops-allow-everywhere" in result["memberofindirect_sudorule"]
    mock_client.user_show.assert_called_once_with("mballew")


# ── hbactest_explain tool ────────────────────────────────────────


def test_hbactest_explain_uses_native_when_available(mock_client) -> None:
    """hbactest_explain prefers IPA native hbactest."""
    mock_client.hbactest.return_value = NATIVE_HBACTEST_RESPONSE
    result = ipa_hbactest_explain(
        user="alice", targethost="node1.cloud.together.ai", service="sshd"
    )
    assert result["method"] == "native_hbactest"
    assert result["access_granted"] is True
    assert "allow_all_sshd" in result["matched_rules"]


def test_hbactest_explain_falls_back_on_native_failure(mock_client) -> None:
    """hbactest_explain falls back to client-side when native fails."""
    mock_client.hbactest.side_effect = RuntimeError("hbactest error")
    mock_client.hbacrule_find.return_value = HBACRULE_FIND_ALL_RESPONSE
    mock_client._call.side_effect = _mock_call_for_hbactest

    result = ipa_hbactest_explain(
        user="alice", targethost="node1.cloud.together.ai", service="sshd"
    )
    assert result["method"] == "client_side"
    assert result["access_granted"] is True
    assert "allow_all_sshd" in result["matched_rules"]


def test_hbactest_explain_denies_when_no_match(mock_client) -> None:
    mock_client.hbactest.side_effect = RuntimeError("fail")
    mock_client.hbacrule_find.return_value = {
        "result": [
            {
                "cn": ["restricted"],
                "ipaenabledflag": [True],
                "memberuser_user": ["bob"],
                "hostcategory": ["all"],
                "servicecategory": ["all"],
            }
        ],
        "count": 1,
    }
    mock_client._call.side_effect = _mock_call_no_groups

    result = ipa_hbactest_explain(
        user="alice", targethost="node1.cloud.together.ai", service="sshd"
    )
    assert result["access_granted"] is False
    assert result["matched_rules"] == []


def _mock_call_for_hbactest(method: str, args: list, kw: dict | None = None):
    if method == "user_show":
        return USER_SHOW_RESPONSE
    if method == "host_show":
        return HOST_SHOW_RESPONSE
    raise RuntimeError(f"unexpected call: {method}")


def _mock_call_no_groups(method: str, args: list, kw: dict | None = None):
    if method == "user_show":
        return {"result": {"uid": ["alice"], "memberof_group": []}}
    if method == "host_show":
        return {"result": {"fqdn": ["node1.cloud.together.ai"], "memberof_hostgroup": []}}
    raise RuntimeError(f"unexpected call: {method}")


# ── hostgroup_diff tool ──────────────────────────────────────────


def test_hostgroup_diff_tool(mock_client) -> None:
    mock_client.hostgroup_show.return_value = HOSTGROUP_SHOW_WITH_MEMBERS
    result = ipa_hostgroup_diff(
        hostgroup="hg_forge_cartesia5",
        expected_hosts=[
            "node1.cloud.together.ai",
            "node2.cloud.together.ai",
            "node4.cloud.together.ai",
        ],
    )
    assert result["hostgroup"] == "hg_forge_cartesia5"
    assert "node4.cloud.together.ai" in result["to_add"]
    assert "node3.cloud.together.ai" in result["to_remove"]
    assert "node1.cloud.together.ai" in result["unchanged"]


def test_hostgroup_diff_empty_hostgroup(mock_client) -> None:
    mock_client.hostgroup_show.return_value = {"result": {"cn": ["hg_empty"]}}
    result = ipa_hostgroup_diff(
        hostgroup="hg_empty",
        expected_hosts=["a.example.com"],
    )
    assert result["to_add"] == ["a.example.com"]
    assert result["to_remove"] == []
    assert result["unchanged"] == []
