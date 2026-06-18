"""Constants for the dc-support-mcp server.

Host URLs declared here are **built-in defaults**.  Each is overridable at
runtime via its matching environment variable (a literal *or* an ``op://``
reference) through the resolver functions at the bottom of this module — e.g.
``rtb_base_url()`` reads ``$RTB_BASE_URL`` and falls back to
:data:`RTB_BASE_URL`.  Read the *effective* value via those functions / the
vendor handlers' ``BASE_URL`` property; the bare constants are only the
defaults.  The common case sets no env var and needs no 1Password access.
"""

from datetime import timedelta

from .secrets import host_url

# Shared Atlassian Service Desk endpoint (same across all Atlassian portals)
ATLASSIAN_API_ENDPOINT = "/rest/servicedesk/1/customer/models"

# Ori Portal Configuration
ORI_BASE_URL = "https://oriindustries.atlassian.net"  # default; override via $ORI_BASE_URL
ORI_PORTAL_ID = 3
ORI_API_ENDPOINT = ATLASSIAN_API_ENDPOINT  # kept for backwards compat

# Hypertec (5C) Portal Configuration
HYPERTEC_BASE_URL = (
    "https://hypertec-cloud.atlassian.net"  # default; override via $HYPERTEC_BASE_URL
)
HYPERTEC_PORTAL_ID = 4

# IREN Portal Configuration
IREN_BASE_URL = "https://support.iren.com"  # default; override via $IREN_BASE_URL
IREN_FRESHDESK_URL = "https://iren.freshdesk.com"  # default; override via $IREN_FRESHDESK_URL

# Repair Ticket Bridge (RTB) -- internal GPU triage API
RTB_BASE_URL = (
    "https://rtb.together.ai"  # default; override via $RTB_BASE_URL  # pragma: allowlist secret
)

# NetBox (triage-status fallback patching); aligns with netbox-mcp's NETBOX_URL
NETBOX_URL = (
    "https://i.together.ai"  # default; override via $NETBOX_URL  # pragma: allowlist secret
)

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
ATLASSIAN_SESSION_COOKIE_NAMES: frozenset[str] = frozenset(
    {
        "cloud.session.token",
        "tenant.session.token",
        "_session_id",
    }
)

# HTTP Status Codes
HTTP_OK = 200
HTTP_CREATED = 201
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403

# Ticket ID Pattern (ORI legacy -- validation now lives in handler instances)
TICKET_ID_PATTERN = r"^SUPP-\d+$"

# Grafana Alertmanager proxy (for alert silencing).
# NOTE: this default is UNCERTAIN -- the monitoring-admin host is believed to
# now 404 and the endpoint has likely moved (e.g. under grafana.together.xyz).
# The current value is kept as the default to preserve behavior; it is
# overridable via $GRAFANA_AM_PROXY_BASE (resolve via grafana_am_proxy_base())
# and should be smoke-tested live against the real silence path before the
# default is changed.
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


# ── Host-URL resolvers: default-but-overridable (env literal or op://) ───────
#
# Each returns the *effective* host URL: the matching env var when set (a
# literal or an ``op://`` ref, resolved via the mcp-common credential chain),
# otherwise the built-in default above.  Resolution is lazy (call the function
# at use time), so importing this module never touches 1Password / keyctl.


def iren_base_url() -> str:
    """Effective IREN support-portal base URL ($IREN_BASE_URL or default)."""
    return host_url("IREN_BASE_URL", IREN_BASE_URL)


def iren_freshdesk_url() -> str:
    """Effective IREN Freshdesk API base URL ($IREN_FRESHDESK_URL or default)."""
    return host_url("IREN_FRESHDESK_URL", IREN_FRESHDESK_URL)


def rtb_base_url() -> str:
    """Effective Repair Ticket Bridge base URL ($RTB_BASE_URL or default)."""
    return host_url("RTB_BASE_URL", RTB_BASE_URL)


def netbox_url() -> str:
    """Effective NetBox base URL ($NETBOX_URL or default)."""
    return host_url("NETBOX_URL", NETBOX_URL)


def grafana_am_proxy_base() -> str:
    """Effective Grafana Alertmanager proxy base ($GRAFANA_AM_PROXY_BASE or default)."""
    return host_url("GRAFANA_AM_PROXY_BASE", GRAFANA_AM_PROXY_BASE)
