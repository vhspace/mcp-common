"""
Hypertec (5C) vendor handler - Atlassian Service Desk integration.

Thin subclass of AtlassianServiceDeskHandler with Hypertec-specific
portal configuration. Supports get_ticket, list_tickets, and add_comment.
Ticket creation is not yet implemented (requires form discovery).
"""

from ..constants import HYPERTEC_BASE_URL, HYPERTEC_PORTAL_ID
from .atlassian_base import AtlassianServiceDeskHandler


class HypertecVendorHandler(AtlassianServiceDeskHandler):
    """Hypertec (5C) vendor handler.

    Inherits all Atlassian Service Desk operations (get/list/comment)
    from the base class. Ticket creation via the Hypertec portal form
    is not yet supported.
    """

    VENDOR_NAME = "hypertec"
    BASE_URL = HYPERTEC_BASE_URL
    PORTAL_ID = HYPERTEC_PORTAL_ID
    TICKET_ID_PREFIX = "HTCSR"
    COOKIE_FILE_NAME = ".hypertec_session_cookies.pkl"
    HELP_CENTER_ARI = None
