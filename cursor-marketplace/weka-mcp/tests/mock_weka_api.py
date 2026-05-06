"""Mock Weka REST API v2 server for local testing.

Simulates a Weka storage cluster with realistic data shaped like Together AI
infrastructure. Run standalone via ``uvicorn mock_weka_api:app --port 14000``
or through Docker Compose (see docker-compose.mock.yml).
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Mock Weka REST API v2", version="4.3.1")

VALID_TOKEN = os.environ.get("MOCK_WEKA_TOKEN", "test-token-12345")
REFRESH_SECRET = "refresh-secret-67890"

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def _make_tokens() -> dict[str, Any]:
    return {
        "access_token": VALID_TOKEN,
        "refresh_token": REFRESH_SECRET,
        "token_type": "Bearer",
        "expires_in": 300,
    }


def _require_auth(authorization: str | None = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Bearer token")
    token = authorization.split(" ", 1)[1].strip()
    if token != VALID_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid access token")
    return token


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

_NOW = "2025-12-15T10:30:00Z"
_HOUR_AGO = "2025-12-15T09:30:00Z"
_DAY_AGO = "2025-12-14T10:30:00Z"

# ---------------------------------------------------------------------------
# Static mock data
# ---------------------------------------------------------------------------

CLUSTER_GUID = "clus-abcdef12-3456-7890-abcd-ef1234567890"

CLUSTER_DATA: dict[str, Any] = {
    "name": "together-weka-prod",
    "guid": CLUSTER_GUID,
    "status": "OK",
    "release": "4.3.1",
    "init_stage": "RUNNING",
    "is_enterprise": True,
    "hot_spare": 2,
    "hosts": {"total": 4, "active": 4, "backends": 4, "clients": 0},
    "capacity": {
        "total_bytes": 1099511627776 * 200,
        "used_bytes": 1099511627776 * 120,
        "available_bytes": 1099511627776 * 80,
        "hot_spare_bytes": 1099511627776 * 10,
    },
    "cloud": {"enabled": True, "healthy": True},
    "stripe_width": 3,
    "protection_scheme": "6+2",
    "licensing": {"mode": "PayGo"},
}

ALERTS_DATA: list[dict[str, Any]] = [
    {
        "uid": "alert-001",
        "type": "NodeNotResponding",
        "title": "Node not responding",
        "severity": "MAJOR",
        "description": "Node weka-node-03 missed 3 heartbeats",
        "hostname": "weka-node-03",
        "is_muted": False,
        "timestamp": _HOUR_AGO,
    },
    {
        "uid": "alert-002",
        "type": "DriveHealthDegraded",
        "title": "Drive health degraded",
        "severity": "MINOR",
        "description": "Drive /dev/nvme1n1 on weka-node-02 showing increased error rate",
        "hostname": "weka-node-02",
        "is_muted": False,
        "timestamp": _HOUR_AGO,
    },
    {
        "uid": "alert-003",
        "type": "CapacityWarning",
        "title": "Capacity usage above 80%",
        "severity": "WARNING",
        "description": "Filesystem training-data is at 85% capacity",
        "hostname": "",
        "is_muted": False,
        "timestamp": _DAY_AGO,
    },
    {
        "uid": "alert-004",
        "type": "LicenseExpiring",
        "title": "License expiring soon",
        "severity": "INFO",
        "description": "Cluster license expires in 30 days",
        "hostname": "",
        "is_muted": True,
        "timestamp": _DAY_AGO,
    },
]

ALERT_DESCRIPTIONS: list[dict[str, Any]] = [
    {
        "type": "NodeNotResponding",
        "description": "A backend node has stopped responding to heartbeats.",
    },
    {"type": "DriveHealthDegraded", "description": "A drive is showing signs of degraded health."},
    {"type": "CapacityWarning", "description": "A filesystem has exceeded a capacity threshold."},
    {"type": "LicenseExpiring", "description": "The cluster license is approaching expiration."},
    {"type": "RebuildInProgress", "description": "Data rebuild is in progress after a failure."},
]

EVENTS_DATA: list[dict[str, Any]] = [
    {
        "uid": "evt-001",
        "type": "ClusterHealthCheck",
        "severity": "INFO",
        "description": "Cluster health check passed",
        "timestamp": _NOW,
    },
    {
        "uid": "evt-002",
        "type": "NodeJoined",
        "severity": "INFO",
        "description": "Node weka-node-04 joined cluster",
        "timestamp": _HOUR_AGO,
    },
    {
        "uid": "evt-003",
        "type": "DriveAdded",
        "severity": "INFO",
        "description": "Drive /dev/nvme0n1 added to weka-node-04",
        "timestamp": _HOUR_AGO,
    },
    {
        "uid": "evt-004",
        "type": "SnapshotCreated",
        "severity": "INFO",
        "description": "Snapshot daily-2025-12-15 created on training-data",
        "timestamp": _HOUR_AGO,
    },
    {
        "uid": "evt-005",
        "type": "CapacityThreshold",
        "severity": "WARNING",
        "description": "Filesystem training-data exceeded 80% capacity",
        "timestamp": _DAY_AGO,
    },
    {
        "uid": "evt-006",
        "type": "NodeRestart",
        "severity": "MINOR",
        "description": "Node weka-node-03 restarted unexpectedly",
        "timestamp": _DAY_AGO,
    },
    {
        "uid": "evt-007",
        "type": "RebuildStarted",
        "severity": "WARNING",
        "description": "Rebuild started for stripe group 42",
        "timestamp": _DAY_AGO,
    },
    {
        "uid": "evt-008",
        "type": "RebuildCompleted",
        "severity": "INFO",
        "description": "Rebuild completed for stripe group 42",
        "timestamp": _DAY_AGO,
    },
]

_CONTAINER_TEMPLATE: list[dict[str, Any]] = [
    {
        "uid": "cont-0001",
        "hostname": "weka-node-01",
        "ips": ["10.0.1.1"],
        "status": "UP",
        "mode": "backend",
        "cores": 19,
        "memory": "68.72 GB",
        "drives_dedicated_cores": 2,
        "failure_domain": "fd-0001",
    },
    {
        "uid": "cont-0002",
        "hostname": "weka-node-02",
        "ips": ["10.0.1.2"],
        "status": "UP",
        "mode": "backend",
        "cores": 19,
        "memory": "68.72 GB",
        "drives_dedicated_cores": 2,
        "failure_domain": "fd-0001",
    },
    {
        "uid": "cont-0003",
        "hostname": "weka-node-03",
        "ips": ["10.0.1.3"],
        "status": "UP",
        "mode": "backend",
        "cores": 19,
        "memory": "68.72 GB",
        "drives_dedicated_cores": 2,
        "failure_domain": "fd-0002",
    },
    {
        "uid": "cont-0004",
        "hostname": "weka-node-04",
        "ips": ["10.0.1.4"],
        "status": "UP",
        "mode": "backend",
        "cores": 19,
        "memory": "68.72 GB",
        "drives_dedicated_cores": 2,
        "failure_domain": "fd-0002",
    },
]

SERVERS_DATA: list[dict[str, Any]] = [
    {
        "uid": "srv-0001",
        "hostname": "weka-node-01",
        "status": "UP",
        "ips": ["10.0.1.1"],
        "cores": 19,
        "memory_bytes": 73825566720,
        "sw_release_string": "4.3.1",
    },
    {
        "uid": "srv-0002",
        "hostname": "weka-node-02",
        "status": "UP",
        "ips": ["10.0.1.2"],
        "cores": 19,
        "memory_bytes": 73825566720,
        "sw_release_string": "4.3.1",
    },
    {
        "uid": "srv-0003",
        "hostname": "weka-node-03",
        "status": "UP",
        "ips": ["10.0.1.3"],
        "cores": 19,
        "memory_bytes": 73825566720,
        "sw_release_string": "4.3.1",
    },
    {
        "uid": "srv-0004",
        "hostname": "weka-node-04",
        "status": "UP",
        "ips": ["10.0.1.4"],
        "cores": 19,
        "memory_bytes": 73825566720,
        "sw_release_string": "4.3.1",
    },
]

DRIVES_DATA: list[dict[str, Any]] = [
    {
        "uid": "drv-0001",
        "node_id": "srv-0001",
        "hostname": "weka-node-01",
        "vendor": "Samsung",
        "model": "PM9A3",
        "path": "/dev/nvme0n1",
        "size_bytes": 3840755604480,
        "status": "ACTIVE",
        "firmware": "GDC7302Q",
    },
    {
        "uid": "drv-0002",
        "node_id": "srv-0001",
        "hostname": "weka-node-01",
        "vendor": "Samsung",
        "model": "PM9A3",
        "path": "/dev/nvme1n1",
        "size_bytes": 3840755604480,
        "status": "ACTIVE",
        "firmware": "GDC7302Q",
    },
    {
        "uid": "drv-0003",
        "node_id": "srv-0002",
        "hostname": "weka-node-02",
        "vendor": "Samsung",
        "model": "PM9A3",
        "path": "/dev/nvme0n1",
        "size_bytes": 3840755604480,
        "status": "ACTIVE",
        "firmware": "GDC7302Q",
    },
    {
        "uid": "drv-0004",
        "node_id": "srv-0002",
        "hostname": "weka-node-02",
        "vendor": "Samsung",
        "model": "PM9A3",
        "path": "/dev/nvme1n1",
        "size_bytes": 3840755604480,
        "status": "ACTIVE",
        "firmware": "GDC7302Q",
    },
    {
        "uid": "drv-0005",
        "node_id": "srv-0003",
        "hostname": "weka-node-03",
        "vendor": "Samsung",
        "model": "PM9A3",
        "path": "/dev/nvme0n1",
        "size_bytes": 3840755604480,
        "status": "ACTIVE",
        "firmware": "GDC7302Q",
    },
    {
        "uid": "drv-0006",
        "node_id": "srv-0003",
        "hostname": "weka-node-03",
        "vendor": "Samsung",
        "model": "PM9A3",
        "path": "/dev/nvme1n1",
        "size_bytes": 3840755604480,
        "status": "ACTIVE",
        "firmware": "GDC7302Q",
    },
    {
        "uid": "drv-0007",
        "node_id": "srv-0004",
        "hostname": "weka-node-04",
        "vendor": "Samsung",
        "model": "PM9A3",
        "path": "/dev/nvme0n1",
        "size_bytes": 3840755604480,
        "status": "ACTIVE",
        "firmware": "GDC7302Q",
    },
    {
        "uid": "drv-0008",
        "node_id": "srv-0004",
        "hostname": "weka-node-04",
        "vendor": "Samsung",
        "model": "PM9A3",
        "path": "/dev/nvme1n1",
        "size_bytes": 3840755604480,
        "status": "ACTIVE",
        "firmware": "GDC7302Q",
    },
]

PROCESSES_DATA: list[dict[str, Any]] = [
    {
        "uid": "proc-0001",
        "hostname": "weka-node-01",
        "type": "COMPUTE",
        "status": "UP",
        "cores": [0, 1, 2, 3],
    },
    {
        "uid": "proc-0002",
        "hostname": "weka-node-01",
        "type": "DRIVES",
        "status": "UP",
        "cores": [4, 5],
    },
    {
        "uid": "proc-0003",
        "hostname": "weka-node-01",
        "type": "FRONTEND",
        "status": "UP",
        "cores": [6],
    },
    {
        "uid": "proc-0004",
        "hostname": "weka-node-02",
        "type": "COMPUTE",
        "status": "UP",
        "cores": [0, 1, 2, 3],
    },
    {
        "uid": "proc-0005",
        "hostname": "weka-node-02",
        "type": "DRIVES",
        "status": "UP",
        "cores": [4, 5],
    },
    {
        "uid": "proc-0006",
        "hostname": "weka-node-02",
        "type": "FRONTEND",
        "status": "UP",
        "cores": [6],
    },
]

FAILURE_DOMAINS: list[dict[str, Any]] = [
    {"uid": "fd-0001", "name": "rack-a", "num_nodes": 2, "nodes": ["weka-node-01", "weka-node-02"]},
    {"uid": "fd-0002", "name": "rack-b", "num_nodes": 2, "nodes": ["weka-node-03", "weka-node-04"]},
]

TB = 1099511627776
GB = 1073741824

FILESYSTEMS_DATA: list[dict[str, Any]] = [
    {
        "uid": "fs-0001",
        "name": "training-data",
        "group_name": "default",
        "status": "READY",
        "is_creating": False,
        "is_removing": False,
        "total_budget": 100 * TB,
        "used_total": 85 * TB,
        "available_total": 15 * TB,
        "ssd_budget": 50 * TB,
        "used_ssd": 42 * TB,
        "auth_required": False,
        "data_reduction_ratio": 1.3,
    },
    {
        "uid": "fs-0002",
        "name": "model-checkpoints",
        "group_name": "default",
        "status": "READY",
        "is_creating": False,
        "is_removing": False,
        "total_budget": 50 * TB,
        "used_total": 20 * TB,
        "available_total": 30 * TB,
        "ssd_budget": 25 * TB,
        "used_ssd": 10 * TB,
        "auth_required": False,
        "data_reduction_ratio": 1.1,
    },
    {
        "uid": "fs-0003",
        "name": "scratch",
        "group_name": "ephemeral",
        "status": "READY",
        "is_creating": False,
        "is_removing": False,
        "total_budget": 20 * TB,
        "used_total": 5 * TB,
        "available_total": 15 * TB,
        "ssd_budget": 20 * TB,
        "used_ssd": 5 * TB,
        "auth_required": False,
        "data_reduction_ratio": 1.0,
    },
]

FS_GROUPS_DATA: list[dict[str, Any]] = [
    {"uid": "fsg-0001", "name": "default", "filesystems": ["fs-0001", "fs-0002"]},
    {"uid": "fsg-0002", "name": "ephemeral", "filesystems": ["fs-0003"]},
]

QUOTAS_DATA: dict[str, list[dict[str, Any]]] = {
    "fs-0001": [
        {
            "uid": "q-001",
            "path": "/",
            "hard_limit": 100 * TB,
            "soft_limit": 90 * TB,
            "used": 85 * TB,
            "owner": "root",
        },
        {
            "uid": "q-002",
            "path": "/team-a",
            "hard_limit": 40 * TB,
            "soft_limit": 35 * TB,
            "used": 30 * TB,
            "owner": "ml-team",
        },
    ],
    "fs-0002": [
        {
            "uid": "q-003",
            "path": "/",
            "hard_limit": 50 * TB,
            "soft_limit": 45 * TB,
            "used": 20 * TB,
            "owner": "root",
        },
    ],
    "fs-0003": [
        {
            "uid": "q-004",
            "path": "/",
            "hard_limit": 20 * TB,
            "soft_limit": 18 * TB,
            "used": 5 * TB,
            "owner": "root",
        },
    ],
}

SNAPSHOTS_DATA: list[dict[str, Any]] = [
    {
        "uid": "snap-0001",
        "name": "daily-2025-12-15",
        "filesystem_uid": "fs-0001",
        "filesystem_name": "training-data",
        "creation_time": _NOW,
        "is_writable": False,
        "status": "ACTIVE",
        "used_bytes": 200 * GB,
    },
    {
        "uid": "snap-0002",
        "name": "daily-2025-12-14",
        "filesystem_uid": "fs-0001",
        "filesystem_name": "training-data",
        "creation_time": _DAY_AGO,
        "is_writable": False,
        "status": "ACTIVE",
        "used_bytes": 195 * GB,
    },
    {
        "uid": "snap-0003",
        "name": "pre-release-v2",
        "filesystem_uid": "fs-0002",
        "filesystem_name": "model-checkpoints",
        "creation_time": _DAY_AGO,
        "is_writable": False,
        "status": "ACTIVE",
        "used_bytes": 50 * GB,
    },
]

SNAPSHOT_POLICIES: list[dict[str, Any]] = [
    {
        "uid": "spol-0001",
        "name": "daily-backup",
        "schedule": {"frequency": "daily", "time": "02:00", "retention_count": 7},
        "filesystem_uid": "fs-0001",
        "filesystem_name": "training-data",
        "is_active": True,
    },
]

S3_CLUSTER_STATUS: dict[str, Any] = {
    "uid": "s3-0001",
    "name": "weka-s3",
    "status": "READY",
    "enabled": True,
    "dns_name": "s3.weka.together.ai",
    "port": 9000,
    "protocol": "HTTPS",
    "auth_method": "V4",
}

S3_BUCKETS: list[dict[str, Any]] = [
    {
        "uid": "bkt-0001",
        "name": "training-datasets",
        "filesystem_uid": "fs-0001",
        "filesystem_name": "training-data",
        "path": "/s3/training-datasets",
        "policy": "none",
        "hard_quota": 50 * TB,
        "used": 30 * TB,
    },
    {
        "uid": "bkt-0002",
        "name": "model-artifacts",
        "filesystem_uid": "fs-0002",
        "filesystem_name": "model-checkpoints",
        "path": "/s3/model-artifacts",
        "policy": "none",
        "hard_quota": 20 * TB,
        "used": 8 * TB,
    },
]

INTERFACE_GROUPS: list[dict[str, Any]] = [
    {
        "uid": "ig-0001",
        "name": "nfs-group",
        "type": "NFS",
        "subnet": "10.0.2.0/24",
        "gateway": "10.0.2.1",
        "ips": ["10.0.2.10", "10.0.2.11", "10.0.2.12", "10.0.2.13"],
        "ports": [
            {"node": "weka-node-01", "port": "eth1"},
            {"node": "weka-node-02", "port": "eth1"},
        ],
        "status": "OK",
    },
]

SMB_CONFIG: dict[str, Any] = {
    "uid": "smb-0001",
    "name": "weka-smb",
    "enabled": True,
    "domain": "together.ai",
    "encryption": "desired",
    "status": "READY",
}

SMB_SHARES: list[dict[str, Any]] = [
    {
        "uid": "share-0001",
        "name": "shared-models",
        "filesystem_uid": "fs-0002",
        "filesystem_name": "model-checkpoints",
        "path": "/shared-models",
        "acl": "everyone:read",
        "encryption": "desired",
        "status": "OK",
    },
    {
        "uid": "share-0002",
        "name": "team-scratch",
        "filesystem_uid": "fs-0003",
        "filesystem_name": "scratch",
        "path": "/team-scratch",
        "acl": "ml-team:full",
        "encryption": "desired",
        "status": "OK",
    },
]

ORGANIZATIONS_DATA: list[dict[str, Any]] = [
    {"uid": "org-0001", "name": "root", "allocated_capacity": 170 * TB, "used_capacity": 110 * TB},
    {"uid": "org-0002", "name": "ml-team", "allocated_capacity": 80 * TB, "used_capacity": 45 * TB},
]

USERS_DATA: list[dict[str, Any]] = [
    {
        "uid": "usr-0001",
        "username": "admin",
        "role": "ClusterAdmin",
        "org": "root",
        "source": "Internal",
        "created": _DAY_AGO,
    },
    {
        "uid": "usr-0002",
        "username": "sre-bot",
        "role": "ClusterAdmin",
        "org": "root",
        "source": "Internal",
        "created": _DAY_AGO,
    },
    {
        "uid": "usr-0003",
        "username": "ml-pipeline",
        "role": "OrgAdmin",
        "org": "ml-team",
        "source": "Internal",
        "created": _DAY_AGO,
    },
]

TASKS_DATA: list[dict[str, Any]] = [
    {
        "uid": "task-0001",
        "type": "Rebuild",
        "status": "COMPLETED",
        "progress": 100,
        "description": "Rebuild stripe group 42",
        "start_time": _DAY_AGO,
        "end_time": _HOUR_AGO,
    },
    {
        "uid": "task-0002",
        "type": "Snapshot",
        "status": "IN_PROGRESS",
        "progress": 65,
        "description": "Creating snapshot daily-2025-12-15",
        "start_time": _HOUR_AGO,
        "end_time": None,
    },
]

LICENSE_DATA: dict[str, Any] = {
    "uid": "lic-0001",
    "mode": "PayGo",
    "status": "VALID",
    "expiry_date": "2026-12-15T00:00:00Z",
    "licensed_capacity_bytes": 200 * TB,
    "used_capacity_bytes": 120 * TB,
    "cluster_guid": CLUSTER_GUID,
}

TLS_DATA: dict[str, Any] = {
    "status": "ENABLED",
    "certificate_expiry": "2026-06-15T00:00:00Z",
    "certificate_issuer": "Let's Encrypt",
    "tls_version": "1.3",
}

STATS_DATA: dict[str, Any] = {
    "timestamp": _NOW,
    "read_iops": 125000,
    "write_iops": 45000,
    "read_bytes_per_sec": 12 * GB,
    "write_bytes_per_sec": 4 * GB,
    "read_latency_us": 250,
    "write_latency_us": 320,
    "cpu_usage_percent": 42.5,
    "memory_usage_percent": 68.3,
    "num_ops": 170000,
}

REALTIME_STATS: dict[str, Any] = {
    "timestamp": _NOW,
    "interval_ms": 1000,
    "read_iops": 127500,
    "write_iops": 44800,
    "read_bytes_per_sec": 12.2 * GB,
    "write_bytes_per_sec": 3.9 * GB,
    "read_latency_us": 245,
    "write_latency_us": 315,
}


# ---------------------------------------------------------------------------
# Weka wraps list responses in {"data": [...]} — helper to do the same
# ---------------------------------------------------------------------------


def _wrap(data: Any) -> dict[str, Any]:
    if isinstance(data, list):
        return {"data": data}
    if isinstance(data, dict) and "data" not in data:
        return {"data": [data]}
    return data


# ===================================================================
# Login (no auth required)
# ===================================================================


@app.post("/api/v2/login")
async def login(request: Request) -> JSONResponse:
    body = await request.json()
    username = body.get("username", "")
    password = body.get("password", "")
    if not username or not password:
        raise HTTPException(status_code=400, detail="username and password required")
    return JSONResponse(content=_wrap(_make_tokens()))


@app.post("/api/v2/login/refresh")
async def login_refresh(request: Request) -> JSONResponse:
    body = await request.json()
    if not body.get("refresh_token"):
        raise HTTPException(status_code=400, detail="refresh_token required")
    return JSONResponse(content=_wrap(_make_tokens()))


# ===================================================================
# Cluster & Health
# ===================================================================


@app.get("/api/v2/cluster")
async def get_cluster(_token: str = Depends(_require_auth)) -> JSONResponse:
    return JSONResponse(content=_wrap(CLUSTER_DATA))


@app.get("/api/v2/healthcheck")
async def healthcheck() -> JSONResponse:
    return JSONResponse(content={"status": "ok"})


# ===================================================================
# Alerts & Events
# ===================================================================


@app.get("/api/v2/alerts")
async def list_alerts(
    severity: str | None = Query(None),
    _token: str = Depends(_require_auth),
) -> JSONResponse:
    data = ALERTS_DATA
    if severity:
        data = [a for a in data if a["severity"].upper() == severity.upper()]
    return JSONResponse(content=_wrap(data))


@app.get("/api/v2/alerts/description")
async def alert_descriptions(_token: str = Depends(_require_auth)) -> JSONResponse:
    return JSONResponse(content=_wrap(ALERT_DESCRIPTIONS))


@app.put("/api/v2/alerts/{alert_type}/mute")
async def mute_alert(alert_type: str, _token: str = Depends(_require_auth)) -> JSONResponse:
    return JSONResponse(content={"status": "muted", "type": alert_type})


@app.put("/api/v2/alerts/{alert_type}/unmute")
async def unmute_alert(alert_type: str, _token: str = Depends(_require_auth)) -> JSONResponse:
    return JSONResponse(content={"status": "unmuted", "type": alert_type})


@app.get("/api/v2/events")
async def list_events(
    severity: str | None = Query(None),
    num_results: int | None = Query(None),
    _token: str = Depends(_require_auth),
) -> JSONResponse:
    data = EVENTS_DATA
    if severity:
        data = [e for e in data if e["severity"].upper() == severity.upper()]
    if num_results and num_results > 0:
        data = data[:num_results]
    return JSONResponse(content=_wrap(data))


# ===================================================================
# Infrastructure — Containers
# ===================================================================


@app.get("/api/v2/containers")
async def list_containers(_token: str = Depends(_require_auth)) -> JSONResponse:
    return JSONResponse(content=_wrap(_CONTAINER_TEMPLATE))


@app.get("/api/v2/containers/{uid}")
async def get_container(uid: str, _token: str = Depends(_require_auth)) -> JSONResponse:
    for c in _CONTAINER_TEMPLATE:
        if c["uid"] == uid:
            return JSONResponse(content=_wrap(c))
    raise HTTPException(status_code=404, detail=f"Container {uid} not found")


# ===================================================================
# Infrastructure — Servers
# ===================================================================


@app.get("/api/v2/servers")
async def list_servers(_token: str = Depends(_require_auth)) -> JSONResponse:
    return JSONResponse(content=_wrap(SERVERS_DATA))


# ===================================================================
# Infrastructure — Drives
# ===================================================================


@app.get("/api/v2/drives")
async def list_drives(_token: str = Depends(_require_auth)) -> JSONResponse:
    return JSONResponse(content=_wrap(DRIVES_DATA))


@app.get("/api/v2/drives/{uid}")
async def get_drive(uid: str, _token: str = Depends(_require_auth)) -> JSONResponse:
    for d in DRIVES_DATA:
        if d["uid"] == uid:
            return JSONResponse(content=_wrap(d))
    raise HTTPException(status_code=404, detail=f"Drive {uid} not found")


# ===================================================================
# Infrastructure — Processes & Failure Domains
# ===================================================================


@app.get("/api/v2/processes")
async def list_processes(_token: str = Depends(_require_auth)) -> JSONResponse:
    return JSONResponse(content=_wrap(PROCESSES_DATA))


@app.get("/api/v2/failureDomains")
async def list_failure_domains(_token: str = Depends(_require_auth)) -> JSONResponse:
    return JSONResponse(content=_wrap(FAILURE_DOMAINS))


# ===================================================================
# Filesystems
# ===================================================================


@app.get("/api/v2/fileSystems")
async def list_filesystems(_token: str = Depends(_require_auth)) -> JSONResponse:
    return JSONResponse(content=_wrap(FILESYSTEMS_DATA))


@app.get("/api/v2/fileSystems/{uid}")
async def get_filesystem(uid: str, _token: str = Depends(_require_auth)) -> JSONResponse:
    for fs in FILESYSTEMS_DATA:
        if fs["uid"] == uid:
            return JSONResponse(content=_wrap(fs))
    raise HTTPException(status_code=404, detail=f"Filesystem {uid} not found")


@app.post("/api/v2/fileSystems")
async def create_filesystem(
    request: Request,
    _token: str = Depends(_require_auth),
) -> JSONResponse:
    body = await request.json()
    new_fs = {
        "uid": f"fs-{uuid.uuid4().hex[:8]}",
        "name": body.get("name", "unnamed"),
        "group_name": body.get("group_name", "default"),
        "status": "CREATING",
        "is_creating": True,
        "is_removing": False,
        "total_budget": body.get("capacity", 0),
        "used_total": 0,
        "available_total": body.get("capacity", 0),
        "ssd_budget": 0,
        "used_ssd": 0,
        "auth_required": False,
        "data_reduction_ratio": 1.0,
    }
    return JSONResponse(status_code=201, content=_wrap(new_fs))


@app.delete("/api/v2/fileSystems/{uid}")
async def delete_filesystem(uid: str, _token: str = Depends(_require_auth)) -> JSONResponse:
    for fs in FILESYSTEMS_DATA:
        if fs["uid"] == uid:
            return JSONResponse(content={"status": "deleted", "uid": uid})
    raise HTTPException(status_code=404, detail=f"Filesystem {uid} not found")


@app.get("/api/v2/fileSystemGroups")
async def list_fs_groups(_token: str = Depends(_require_auth)) -> JSONResponse:
    return JSONResponse(content=_wrap(FS_GROUPS_DATA))


@app.get("/api/v2/fileSystems/{uid}/quota")
async def get_fs_quotas(uid: str, _token: str = Depends(_require_auth)) -> JSONResponse:
    quotas = QUOTAS_DATA.get(uid, [])
    return JSONResponse(content=_wrap(quotas))


# ===================================================================
# Snapshots
# ===================================================================


@app.get("/api/v2/snapshots")
async def list_snapshots(
    filesystem_uid: str | None = Query(None),
    _token: str = Depends(_require_auth),
) -> JSONResponse:
    data = SNAPSHOTS_DATA
    if filesystem_uid:
        data = [s for s in data if s["filesystem_uid"] == filesystem_uid]
    return JSONResponse(content=_wrap(data))


@app.post("/api/v2/snapshots")
async def create_snapshot(
    request: Request,
    _token: str = Depends(_require_auth),
) -> JSONResponse:
    body = await request.json()
    new_snap = {
        "uid": f"snap-{uuid.uuid4().hex[:8]}",
        "name": body.get("name", "unnamed-snapshot"),
        "filesystem_uid": body.get("filesystem_uid", ""),
        "filesystem_name": body.get("filesystem_name", ""),
        "creation_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "is_writable": body.get("is_writable", False),
        "status": "ACTIVE",
        "used_bytes": 0,
    }
    return JSONResponse(status_code=201, content=_wrap(new_snap))


@app.delete("/api/v2/snapshots/{uid}")
async def delete_snapshot(uid: str, _token: str = Depends(_require_auth)) -> JSONResponse:
    for s in SNAPSHOTS_DATA:
        if s["uid"] == uid:
            return JSONResponse(content={"status": "deleted", "uid": uid})
    raise HTTPException(status_code=404, detail=f"Snapshot {uid} not found")


@app.post("/api/v2/snapshots/{uid}/upload")
async def upload_snapshot(uid: str, _token: str = Depends(_require_auth)) -> JSONResponse:
    return JSONResponse(content={"status": "uploading", "uid": uid})


@app.get("/api/v2/snapshotPolicy")
async def list_snapshot_policies(_token: str = Depends(_require_auth)) -> JSONResponse:
    return JSONResponse(content=_wrap(SNAPSHOT_POLICIES))


# ===================================================================
# Protocols — S3
# ===================================================================


@app.get("/api/v2/s3")
async def get_s3_cluster(_token: str = Depends(_require_auth)) -> JSONResponse:
    return JSONResponse(content=_wrap(S3_CLUSTER_STATUS))


@app.get("/api/v2/s3/buckets")
async def list_s3_buckets(_token: str = Depends(_require_auth)) -> JSONResponse:
    return JSONResponse(content=_wrap(S3_BUCKETS))


@app.post("/api/v2/s3")
async def create_s3_cluster(
    request: Request,
    _token: str = Depends(_require_auth),
) -> JSONResponse:
    body = await request.json()
    result = {**S3_CLUSTER_STATUS, "status": "CREATING"}
    result.update(body)
    return JSONResponse(status_code=201, content=_wrap(result))


@app.put("/api/v2/s3")
async def update_s3_cluster(
    request: Request,
    _token: str = Depends(_require_auth),
) -> JSONResponse:
    body = await request.json()
    result = {**S3_CLUSTER_STATUS}
    result.update(body)
    return JSONResponse(content=_wrap(result))


@app.delete("/api/v2/s3")
async def delete_s3_cluster(_token: str = Depends(_require_auth)) -> JSONResponse:
    return JSONResponse(content={"status": "deleted"})


# ===================================================================
# Protocols — NFS / Interface Groups
# ===================================================================


@app.get("/api/v2/interfaceGroups")
async def list_interface_groups(_token: str = Depends(_require_auth)) -> JSONResponse:
    return JSONResponse(content=_wrap(INTERFACE_GROUPS))


# ===================================================================
# Protocols — SMB
# ===================================================================


@app.get("/api/v2/smb")
async def get_smb_config(_token: str = Depends(_require_auth)) -> JSONResponse:
    return JSONResponse(content=_wrap(SMB_CONFIG))


@app.get("/api/v2/smb/shares")
async def list_smb_shares(_token: str = Depends(_require_auth)) -> JSONResponse:
    return JSONResponse(content=_wrap(SMB_SHARES))


# ===================================================================
# Admin — Organizations, Users, Tasks
# ===================================================================


@app.get("/api/v2/organizations")
async def list_organizations(_token: str = Depends(_require_auth)) -> JSONResponse:
    return JSONResponse(content=_wrap(ORGANIZATIONS_DATA))


@app.get("/api/v2/organizations/{uid}")
async def get_organization(uid: str, _token: str = Depends(_require_auth)) -> JSONResponse:
    for org in ORGANIZATIONS_DATA:
        if org["uid"] == uid:
            return JSONResponse(content=_wrap(org))
    raise HTTPException(status_code=404, detail=f"Organization {uid} not found")


@app.post("/api/v2/organizations")
async def create_organization(
    request: Request,
    _token: str = Depends(_require_auth),
) -> JSONResponse:
    body = await request.json()
    new_org = {
        "uid": f"org-{uuid.uuid4().hex[:8]}",
        "name": body.get("name", "unnamed"),
        "allocated_capacity": body.get("ssd_quota", 0),
        "used_capacity": 0,
    }
    return JSONResponse(status_code=201, content=_wrap(new_org))


@app.delete("/api/v2/organizations/{uid}")
async def delete_organization(uid: str, _token: str = Depends(_require_auth)) -> JSONResponse:
    for org in ORGANIZATIONS_DATA:
        if org["uid"] == uid:
            return JSONResponse(content={"status": "deleted", "uid": uid})
    raise HTTPException(status_code=404, detail=f"Organization {uid} not found")


@app.get("/api/v2/users")
async def list_users(_token: str = Depends(_require_auth)) -> JSONResponse:
    return JSONResponse(content=_wrap(USERS_DATA))


@app.get("/api/v2/users/whoami")
async def whoami(_token: str = Depends(_require_auth)) -> JSONResponse:
    return JSONResponse(content=_wrap(USERS_DATA[0]))


@app.get("/api/v2/tasks")
async def list_tasks(_token: str = Depends(_require_auth)) -> JSONResponse:
    return JSONResponse(content=_wrap(TASKS_DATA))


# ===================================================================
# Admin — License, TLS, Stats
# ===================================================================


@app.get("/api/v2/license")
async def get_license(_token: str = Depends(_require_auth)) -> JSONResponse:
    return JSONResponse(content=_wrap(LICENSE_DATA))


@app.get("/api/v2/tls")
async def get_tls(_token: str = Depends(_require_auth)) -> JSONResponse:
    return JSONResponse(content=_wrap(TLS_DATA))


@app.get("/api/v2/stats")
async def get_stats(_token: str = Depends(_require_auth)) -> JSONResponse:
    return JSONResponse(content=_wrap(STATS_DATA))


@app.get("/api/v2/stats/realtime")
async def get_realtime_stats(_token: str = Depends(_require_auth)) -> JSONResponse:
    return JSONResponse(content=_wrap(REALTIME_STATS))
