"""Constants for the dc-support-mcp server."""

from datetime import timedelta

# Shared Atlassian Service Desk endpoint (same across all Atlassian portals)
ATLASSIAN_API_ENDPOINT = "/rest/servicedesk/1/customer/models"

# Ori Portal Configuration
ORI_BASE_URL = "https://oriindustries.atlassian.net"
ORI_PORTAL_ID = 3
ORI_API_ENDPOINT = ATLASSIAN_API_ENDPOINT  # kept for backwards compat

# Hypertec (5C) Portal Configuration
HYPERTEC_BASE_URL = "https://hypertec-cloud.atlassian.net"
HYPERTEC_PORTAL_ID = 4

# Cookie Settings
COOKIE_MAX_AGE = timedelta(hours=8)

# Session Management
AUTH_COOLDOWN = timedelta(minutes=5)
SESSION_PROBE_TIMEOUT = 5  # seconds

# Timeouts (seconds)
API_TIMEOUT = 10
BROWSER_NAVIGATION_TIMEOUT = 15000  # milliseconds
BROWSER_WAIT_TIMEOUT = 10000  # milliseconds
BROWSER_LOGIN_STEP_TIMEOUT = 30000  # ms — Atlassian SSO identity check
BROWSER_POST_LOGIN_WAIT = 2000  # milliseconds
BROWSER_COOKIE_BANNER_TIMEOUT = 3000  # ms — wait for cookie consent banner
BROWSER_LOGIN_ERROR_TIMEOUT = 2000  # ms — wait for login error messages

# Session cookie names that indicate a real authenticated session
# (as opposed to anonymous/tracking cookies)
ATLASSIAN_SESSION_COOKIE_NAMES: frozenset[str] = frozenset({
    "cloud.session.token",
    "tenant.session.token",
    "_session_id",
})

# HTTP Status Codes
HTTP_OK = 200
HTTP_CREATED = 201
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403

# Ticket ID Pattern (ORI legacy -- validation now lives in handler instances)
TICKET_ID_PATTERN = r"^SUPP-\d+$"

# Grafana Alertmanager proxy (for alert silencing)
GRAFANA_AM_PROXY_BASE = "https://monitoring-admin.internal.together.ai/grafana/api/alertmanager"
GRAFANA_AM_DATASOURCE_UID = "am-infra0001"
DEFAULT_SILENCE_HOURS = 168  # 7 days

# Valid RTB GPU outage types (from TriageIssueType enum in RTB API)
RTB_OUTAGE_TYPES: tuple[str, ...] = (
    "Node Down",
    "Node Not in Cluster",
    "Memory Error",
    "GPU - ECC errors",
    "GPU - Missing",
    "GPU - Thermal",
    "GPU - Misconfiguration",
    "GPU - Baseboard",
    "GPU - Replaced",
    "GPU - NIC replaced",
    "GPU - NVSwitch",
    "Network - Optics Cleaning",
    "Network - Unspecified",
    "Network - Cable/Fiber",
    "Network - Transceiver",
    "Network - Inband",
    "Network - Config",
    "Filesystem",
    "Storage",
    "SSD",
    "NCCL Error",
    "Reboot only",
    "BIOS/BMC/PLX/Retimer Firmware",
    "Other",
)
