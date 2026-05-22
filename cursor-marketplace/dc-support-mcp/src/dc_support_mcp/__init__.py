"""DC Support MCP - Datacenter Vendor Support Portal MCP Server"""

from mcp_common.version import get_version

__version__ = get_version("dc-support-mcp")

from .validation import ValidationError
from .vendor_handler import VendorHandler

__all__ = [
    "ValidationError",
    "VendorHandler",
    "__version__",
]
