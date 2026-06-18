# Vendor Architecture

## Overview

The dc-support-mcp server now supports multiple vendor support portals through a flexible, extensible architecture. This design allows adding new vendors without modifying core server code.

## Architecture Components

### 1. Vendor Registry (`vendors/vendor_registry.py`)

The `VendorRegistry` class is the central hub for managing vendor handlers:

- **Registration**: Vendors are registered at startup with `registry.register(vendor_name, HandlerClass)`
- **Lazy Initialization**: Handler instances are created on-demand when first accessed
- **Credential Management**: Automatically loads credentials from environment variables
- **Caching**: Handler instances are cached to avoid repeated initialization

### 2. Vendor Handler Base Class (`vendor_handler.py`)

The abstract `VendorHandler` class defines the interface all vendor handlers must implement:

```python
class VendorHandler(ABC):
    @abstractmethod
    def authenticate(self) -> bool:
        """Authenticate with the vendor portal."""
        pass

    @abstractmethod
    def get_ticket(self, ticket_id: str) -> dict | None:
        """Fetch a ticket by ID."""
        pass

    @abstractmethod
    def list_tickets(self, status: str | None = None, limit: int = 10) -> list[dict]:
        """List tickets with optional filtering."""
        pass
```

### 3. Vendor-Specific Handlers

Each vendor has its own handler class that inherits from `VendorHandler`:

#### ORI Handler (`vendors/ori.py`)

- **Backend**: Atlassian Service Desk
- **Authentication**: Playwright browser automation to extract httpOnly cookies
- **Operations**: Uses Atlassian REST API for fast ticket operations
- **Cookie Caching**: Saves session cookies to avoid repeated browser launches

#### IREN Handler (`vendors/iren.py`)

- **Backend**: Custom portal (no documented API)
- **Authentication**: Playwright browser automation with cookie persistence
- **Operations**: Web scraping using Playwright for all operations
- **Selectors**: Customizable CSS selectors for parsing portal structure

## Adding a New Vendor

To add support for a new vendor portal:

### Step 1: Create Vendor Handler

Create a new file `src/dc_support_mcp/vendors/yourvendor.py`:

```python
from ..vendor_handler import VendorHandler
from ..types import TicketData

class YourVendorHandler(VendorHandler):
    VENDOR_NAME = "yourvendor"
    BASE_URL = "https://support.yourvendor.com"
    COOKIE_FILE_NAME = ".yourvendor_session_cookies.pkl"

    def __init__(self, email: str, password: str, verbose: bool = True):
        self.email = email
        self.password = password
        self.verbose = verbose
        # Initialize your vendor-specific connection

    def authenticate(self) -> bool:
        # Implement authentication logic
        pass

    def get_ticket(self, ticket_id: str) -> Optional[TicketData]:
        # Implement ticket fetching logic
        pass

    def list_tickets(self, status: Optional[str] = None, limit: int = 10) -> List[Dict]:
        # Implement ticket listing logic
        pass
```

### Step 2: Register the Handler

Update `src/dc_support_mcp/vendors/__init__.py`:

```python
from .yourvendor import YourVendorHandler

__all__ = [..., "YourVendorHandler"]
```

Update `src/dc_support_mcp/mcp_server.py` in the `main()` function:

```python
registry.register("yourvendor", YourVendorHandler)
```

### Step 3: Update Constants

Add vendor-specific constants to `src/dc_support_mcp/constants.py`:

```python
# YourVendor Configuration
YOURVENDOR_BASE_URL = "https://support.yourvendor.com"

# Update supported vendors list
SUPPORTED_VENDORS = ["ori", "iren", "yourvendor"]
```

### Step 4: Update Tool Definitions

Update `src/dc_support_mcp/mcp_helpers.py` to include your vendor in the enum:

```python
"vendor": {
    "type": "string",
    "description": "Vendor name",
    "enum": ["ori", "iren", "yourvendor"],
    "default": "ori",
},
```

### Step 5: Set Environment Variables

Set credentials for your vendor:

```bash
export YOURVENDOR_PORTAL_USERNAME="user@example.com"
export YOURVENDOR_PORTAL_PASSWORD="password"
```

The naming convention is: `{VENDOR_NAME}_PORTAL_USERNAME` and `{VENDOR_NAME}_PORTAL_PASSWORD` (uppercase).

## Credential Management

Credentials are loaded from environment variables using a consistent pattern:

- Username: `{VENDOR}_PORTAL_USERNAME`
- Password: `{VENDOR}_PORTAL_PASSWORD`

For example:
- ORI: `ORI_PORTAL_USERNAME`, `ORI_PORTAL_PASSWORD`
- IREN: `IREN_PORTAL_USERNAME`, `IREN_PORTAL_PASSWORD`

Credentials are typically stored in `~/.oh-my-zsh/custom/together.zsh` or similar shell configuration files.

## Implementation Patterns

### Pattern 1: API-Based (Like ORI)

Use when the vendor provides a documented or discoverable API:

1. Use Playwright for initial authentication to capture cookies
2. Extract httpOnly cookies and save them
3. Use `requests` library for fast API operations
4. Re-authenticate automatically when session expires

### Pattern 2: Web Scraping (Like IREN)

Use when the vendor has no documented API:

1. Use Playwright for authentication
2. Keep browser context alive for the session
3. Use Playwright for all operations (navigation, parsing, interaction)
4. Use CSS selectors to extract data from HTML
5. Cache cookies between server restarts

### Pattern 3: Hybrid Approach

Use a combination of both patterns when applicable:

1. Use API for some operations
2. Fall back to web scraping for operations without API support

## Design Principles

### 1. Separation of Concerns

- **Core Server** (`mcp_server.py`): Handles MCP protocol, routes requests
- **Vendor Registry** (`vendor_registry.py`): Manages vendor handlers
- **Vendor Handlers** (`vendors/*.py`): Implement vendor-specific logic

### 2. Extensibility

- New vendors can be added without modifying existing code
- Each vendor encapsulates its own quirks and implementation details
- Common interfaces ensure consistent behavior

### 3. Lazy Loading

- Vendor handlers are initialized only when needed
- Credentials are loaded on-demand from environment
- Browser contexts are created only when required

### 4. Error Handling

- Each vendor handler manages its own errors
- Validation errors are caught and reported consistently
- Authentication failures trigger automatic retry

## Testing New Vendors

When adding a new vendor, test these scenarios:

1. **Initial Authentication**: First-time login with no cached cookies
2. **Cached Authentication**: Second run with cached cookies
3. **Session Expiry**: Behavior when session expires mid-operation
4. **Error Cases**: Invalid credentials, network errors, malformed responses
5. **Multiple Operations**: Sequential tool calls using the same handler instance

## Current Vendor Support

### ORI (Atlassian Service Desk)

- ✅ Get ticket by ID
- ✅ List tickets by status
- ✅ Add comments
- ✅ Cookie caching
- ✅ Automatic re-authentication

### IREN (Custom Portal)

- ✅ Get ticket by ID
- ✅ List tickets
- ⏳ Add comments (not yet implemented)
- ✅ Cookie caching
- ✅ Automatic re-authentication

**Note**: IREN implementation uses placeholder CSS selectors that need to be customized based on the actual portal structure. Inspect the IREN portal HTML to determine correct selectors.

## Future Enhancements

Potential improvements to the vendor architecture:

1. **Async Support**: Convert handlers to async for better performance
2. **Connection Pooling**: Reuse browser contexts across requests
3. **Retry Strategies**: Configurable retry logic per vendor
4. **Rate Limiting**: Vendor-specific rate limiting
5. **Metrics**: Track performance and success rates per vendor
6. **Configuration Files**: Support for vendor-specific config files
