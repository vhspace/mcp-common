"""DC Support MCP - Datacenter Vendor Support Portal MCP Server"""

__version__ = "1.12.2"

from .validation import ValidationError
from .vendor_handler import VendorHandler

__all__ = [
    "ValidationError",
    "VendorHandler",
    "__version__",
]
