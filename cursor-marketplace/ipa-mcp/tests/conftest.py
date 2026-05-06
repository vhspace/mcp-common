"""Shared fixtures for mocking the IPA client."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

# ── Real FreeIPA API response shapes (captured) ─────────────────────

GROUP_FIND_RESPONSE = {
    "result": [
        {
            "gidnumber": ["243000000"],
            "cn": ["admins"],
            "description": ["Account administrators group"],
            "dn": "cn=admins,cn=groups,cn=accounts,dc=cloud,dc=together,dc=ai",
        },
        {
            "cn": ["ipausers"],
            "description": ["Default group for all users"],
            "dn": "cn=ipausers,cn=groups,cn=accounts,dc=cloud,dc=together,dc=ai",
        },
    ],
    "count": 2,
    "truncated": True,
}

HOSTGROUP_FIND_RESPONSE = {
    "result": [
        {
            "cn": ["hg_together_prod"],
            "description": ["Host Group for Together Prod Machines"],
            "dn": "cn=hg_together_prod,cn=hostgroups,cn=accounts,dc=cloud,dc=together,dc=ai",
        },
        {
            "cn": ["ipaservers"],
            "description": ["IPA server hosts"],
            "dn": "cn=ipaservers,cn=hostgroups,cn=accounts,dc=cloud,dc=together,dc=ai",
        },
    ],
    "count": 2,
    "truncated": True,
}

HBACRULE_FIND_RESPONSE = {
    "result": [
        {
            "cn": ["allow_forge_together_support"],
            "ipaenabledflag": [True],
            "servicecategory": ["all"],
            "dn": "ipaUniqueID=de869888-45ed-11ef-9726-56000476138e,cn=hbac,dc=cloud,dc=together,dc=ai",
        },
    ],
    "count": 1,
    "truncated": False,
    "summary": "1 HBAC rule matched",
}

SUDORULE_FIND_RESPONSE = {
    "result": [
        {
            "ipasudoopt": ["!authenticate"],
            "cn": ["allow_sudo_together_forge-support"],
            "cmdcategory": ["all"],
            "ipaenabledflag": [True],
            "ipasudorunasusercategory": ["all"],
            "ipasudorunasgroupcategory": ["all"],
            "dn": "ipaUniqueID=f6f706b0-0109-11ef-98e9-56000476138e,cn=sudorules,cn=sudo,dc=cloud,dc=together,dc=ai",
        },
    ],
    "count": 1,
    "truncated": False,
    "summary": "1 Sudo Rule matched",
}

GROUP_ADD_RESPONSE = {
    "result": {
        "cn": ["ug_forge_test"],
        "gidnumber": ["243000999"],
        "dn": "cn=ug_forge_test,cn=groups,cn=accounts,dc=cloud,dc=together,dc=ai",
    },
    "summary": 'Added group "ug_forge_test"',
}

GROUP_ADD_MEMBER_RESPONSE = {
    "result": {"cn": ["ug_forge_test"], "member_user": ["testuser"]},
    "failed": {"member": {"user": [], "group": []}},
    "completed": 1,
}

HOSTGROUP_ADD_RESPONSE = {
    "result": {
        "cn": ["hg_forge_test"],
        "description": ["Test host group"],
        "dn": "cn=hg_forge_test,cn=hostgroups,cn=accounts,dc=cloud,dc=together,dc=ai",
    },
    "summary": 'Added hostgroup "hg_forge_test"',
}

HOSTGROUP_ADD_MEMBER_RESPONSE = {
    "result": {"cn": ["hg_forge_test"], "member_host": ["host1.example.com"]},
    "failed": {"member": {"host": [], "hostgroup": []}},
    "completed": 1,
}

HOSTGROUP_REMOVE_MEMBER_RESPONSE = {
    "result": {"cn": ["hg_forge_test"]},
    "failed": {"member": {"host": [], "hostgroup": []}},
    "completed": 1,
}

HBACRULE_ADD_RESPONSE = {
    "result": {
        "cn": ["allow_forge_test"],
        "ipaenabledflag": [True],
        "servicecategory": ["all"],
    },
    "summary": 'Added HBAC rule "allow_forge_test"',
}

SUDORULE_ADD_RESPONSE = {
    "result": {
        "cn": ["allow_sudo_test"],
        "ipaenabledflag": [True],
        "cmdcategory": ["all"],
        "ipasudorunasusercategory": ["all"],
        "ipasudorunasgroupcategory": ["all"],
    },
    "summary": 'Added Sudo Rule "allow_sudo_test"',
}


GROUP_SHOW_RESPONSE = {
    "result": {
        "cn": ["ug_forge_cartesia5"],
        "gidnumber": ["243000100"],
        "description": ["Forge cluster cartesia5 users"],
        "member_user": [
            "uid=alice,cn=users,cn=accounts,dc=cloud,dc=together,dc=ai",
            "uid=bob,cn=users,cn=accounts,dc=cloud,dc=together,dc=ai",
        ],
        "member_group": [
            "cn=admins,cn=groups,cn=accounts,dc=cloud,dc=together,dc=ai",
        ],
        "dn": "cn=ug_forge_cartesia5,cn=groups,cn=accounts,dc=cloud,dc=together,dc=ai",
    },
}

HOSTGROUP_SHOW_RESPONSE = {
    "result": {
        "cn": ["hg_forge_cartesia5"],
        "description": ["Forge cluster cartesia5 hosts"],
        "member_host": [
            "fqdn=node1.cloud.together.ai,cn=computers,cn=accounts,dc=cloud,dc=together,dc=ai",
            "fqdn=node2.cloud.together.ai,cn=computers,cn=accounts,dc=cloud,dc=together,dc=ai",
        ],
        "dn": "cn=hg_forge_cartesia5,cn=hostgroups,cn=accounts,dc=cloud,dc=together,dc=ai",
    },
}

HBACRULE_SHOW_RESPONSE = {
    "result": {
        "cn": ["allow_forge_cartesia5"],
        "ipaenabledflag": [True],
        "servicecategory": ["all"],
        "memberuser_user": [
            "uid=alice,cn=users,cn=accounts,dc=cloud,dc=together,dc=ai",
        ],
        "memberuser_group": [
            "cn=ug_forge_cartesia5,cn=groups,cn=accounts,dc=cloud,dc=together,dc=ai",
        ],
        "memberhost_hostgroup": [
            "cn=hg_forge_cartesia5,cn=hostgroups,cn=accounts,dc=cloud,dc=together,dc=ai",
        ],
        "dn": "ipaUniqueID=abc123,cn=hbac,dc=cloud,dc=together,dc=ai",
    },
}

HBACRULE_FIND_ALL_RESPONSE = {
    "result": [
        {
            "cn": ["allow_all_sshd"],
            "ipaenabledflag": [True],
            "usercategory": ["all"],
            "hostcategory": ["all"],
            "servicecategory": ["all"],
        },
        {
            "cn": ["allow_forge_cartesia5"],
            "ipaenabledflag": [True],
            "servicecategory": ["all"],
            "memberuser_group": [
                "cn=ug_forge_cartesia5,cn=groups,cn=accounts,dc=cloud,dc=together,dc=ai",
            ],
            "memberhost_hostgroup": [
                "cn=hg_forge_cartesia5,cn=hostgroups,cn=accounts,dc=cloud,dc=together,dc=ai",
            ],
        },
        {
            "cn": ["disabled_rule"],
            "ipaenabledflag": [False],
            "usercategory": ["all"],
            "hostcategory": ["all"],
            "servicecategory": ["all"],
        },
    ],
    "count": 3,
    "truncated": False,
}

USER_SHOW_RESPONSE = {
    "result": {
        "uid": ["alice"],
        "memberof_group": ["ug_forge_cartesia5", "admins"],
    },
}

USER_SHOW_FULL_RESPONSE = {
    "result": {
        "uid": ["mballew"],
        "givenname": ["Mark"],
        "sn": ["Ballew"],
        "homedirectory": ["/home/mballew"],
        "loginshell": ["/bin/bash"],
        "memberof_group": ["admins", "ug_together_tech_ops", "ipausers"],
        "memberofindirect_hbacrule": ["allow_admin", "allow_together_tech_ops"],
        "memberofindirect_sudorule": ["tech-ops-allow-everywhere", "allow_sudo_dev"],
        "dn": "uid=mballew,cn=users,cn=accounts,dc=cloud,dc=together,dc=ai",
    },
}

HOST_SHOW_RESPONSE = {
    "result": {
        "fqdn": ["node1.cloud.together.ai"],
        "memberof_hostgroup": ["hg_forge_cartesia5"],
    },
}

HOSTGROUP_SHOW_WITH_MEMBERS = {
    "result": {
        "cn": ["hg_forge_cartesia5"],
        "member_host": [
            "node1.cloud.together.ai",
            "node2.cloud.together.ai",
            "node3.cloud.together.ai",
        ],
    },
}

NATIVE_HBACTEST_RESPONSE = {
    "result": {
        "value": True,
        "matched": ["allow_all_sshd", "allow_forge_cartesia5"],
        "notmatched": ["disabled_rule"],
    },
}


@pytest.fixture
def mock_ipa_client() -> MagicMock:
    """Create a mock IPAClient with configurable method return values."""
    client = MagicMock()
    return client
