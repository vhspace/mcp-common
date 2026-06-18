# IREN Portal Customization Guide

## Overview

The IREN vendor handler (`src/dc_support_mcp/vendors/iren.py`) uses web scraping with Playwright since IREN doesn't provide a documented API. The implementation includes placeholder CSS selectors that **must be customized** based on the actual IREN portal structure.

## Setup

### 1. Set Credentials

Add IREN credentials to your shell configuration (e.g., `~/.oh-my-zsh/custom/together.zsh`):

```bash
export IREN_PORTAL_USERNAME="your-email@example.com"
export IREN_PORTAL_PASSWORD="your-password"
```

### 2. Install Playwright

```bash
uv pip install playwright
playwright install chromium
```

## Customization Required

The following sections in `vendors/iren.py` contain placeholder selectors that need updating:

### 1. Login Form Selectors

**Location**: `_authenticate_with_browser()` method

**Current placeholders**:
```python
# Find username/email field
self._page.fill('input[type="email"], input[name="username"], input[name="email"]', self.email)

# Find password field
self._page.fill('input[type="password"], input[name="password"]', self.password)

# Find login button
self._page.click('button[type="submit"], input[type="submit"], button:has-text("Log in")')
```

**Steps to customize**:
1. Open https://support.iren.com/support/tickets in a browser
2. Inspect the login form elements
3. Update selectors to match actual field names/IDs
4. Example: If username field has `id="user_email"`, use `#user_email`

### 2. Login Verification Selector

**Location**: `_is_logged_in()` method

**Current placeholders**:
```python
return (
    self._page.locator('a:has-text("Logout"), a:has-text("Sign out")').count() > 0
    or self._page.locator('.user-profile, .user-menu').count() > 0
)
```

**Steps to customize**:
1. After successful login, inspect the page
2. Find a unique element that only appears when logged in
3. Update selector (e.g., user avatar, account menu, logout button)
4. Example: `self._page.locator('.user-avatar, #user-menu').count() > 0`

### 3. Ticket Page Selectors

**Location**: `_parse_ticket_from_page()` method

**Current placeholders**:
```python
summary = self._page.locator('h1, .ticket-title, .subject').first.inner_text()
status = self._page.locator('.status, .ticket-status').first.inner_text()

# Comment parsing
comment_elements = self._page.locator('.comment, .message, .ticket-comment').all()
author = comment_elem.locator('.author, .user-name').first.inner_text()
date = comment_elem.locator('.date, .timestamp').first.inner_text()
text = comment_elem.locator('.comment-body, .message-text').first.inner_text()
```

**Steps to customize**:
1. Navigate to a ticket page (e.g., https://support.iren.com/support/tickets/12345)
2. Use browser DevTools to inspect each element:
   - Ticket title/summary
   - Status indicator
   - Comment containers
   - Comment author names
   - Comment timestamps
   - Comment text/body
3. Update all selectors to match actual structure

### 4. Ticket List Selectors

**Location**: `list_tickets()` method

**Current placeholders**:
```python
ticket_elements = self._page.locator('.ticket-row, .ticket-item, tr.ticket').all()
ticket_link = ticket_elem.locator('a[href*="/tickets/"]').first
```

**Steps to customize**:
1. Navigate to https://support.iren.com/support/tickets
2. Inspect the ticket list structure
3. Identify:
   - Container element for each ticket row
   - Link element containing ticket ID
   - Ticket ID format (update regex pattern if needed)

### 5. Ticket ID Pattern

**Location**: `list_tickets()` method

**Current regex**:
```python
ticket_id_match = re.search(r'/tickets/(\d+)', href or '')
```

**Steps to customize**:
1. Examine ticket URLs in the IREN portal
2. Determine ticket ID format:
   - Numeric only? `/tickets/12345`
   - Alphanumeric? `/tickets/ABC-12345`
   - Other format? `/tickets/support/12345`
3. Update regex pattern accordingly

## Inspection Workflow

### Using Browser DevTools

1. **Open the portal**:
   ```bash
   # Open in browser
   open https://support.iren.com/support/tickets
   ```

2. **Right-click elements** and select "Inspect" to see HTML structure

3. **Use Console to test selectors**:
   ```javascript
   // Test if selector finds elements
   document.querySelectorAll('.your-selector').length
   
   // See what selector matches
   document.querySelector('.your-selector')
   ```

4. **Copy selector**:
   - Right-click element in DevTools
   - Copy → Copy selector
   - Adapt for Playwright syntax

### Using Playwright Inspector

For more accurate testing, use Playwright's inspector:

```bash
# Run in debug mode
PWDEBUG=1 python -c "
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page()
    page.goto('https://support.iren.com/support/tickets')
    page.pause()  # Interactive inspector
"
```

This opens an interactive inspector where you can test selectors in real-time.

## Testing Your Changes

### 1. Test Authentication

```bash
cd /workspaces/together/dc-support-mcp

# Enable verbose output
export VERBOSE=1

# Test via MCP
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python -m dc_support_mcp.mcp_server
```

### 2. Test Ticket Retrieval

Once authenticated, test getting a ticket:

```bash
# Replace with actual ticket ID from IREN portal
TICKET_ID="12345"

echo '{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "get_vendor_ticket",
    "arguments": {
      "vendor": "iren",
      "ticket_id": "'$TICKET_ID'"
    }
  }
}' | python -m dc_support_mcp.mcp_server
```

### 3. Test Ticket Listing

```bash
echo '{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "list_vendor_tickets",
    "arguments": {
      "vendor": "iren",
      "status": "open",
      "limit": 5
    }
  }
}' | python -m dc_support_mcp.mcp_server
```

## Common Issues

### Issue: Login Fails

**Symptoms**: Authentication returns `False`, verbose log shows interaction errors

**Solutions**:
1. Check if IREN uses CAPTCHA (may require different approach)
2. Verify credentials are correct
3. Check if login uses OAuth/SSO (may need different flow)
4. Inspect for additional form fields (security questions, etc.)
5. Try with `headless=False` to see what's happening:
   ```python
   browser = p.chromium.launch(headless=False)
   ```

### Issue: No Tickets Found

**Symptoms**: `list_tickets()` returns empty list

**Solutions**:
1. Verify you're logged in (check `_is_logged_in()`)
2. Print page content to see actual structure:
   ```python
   print(self._page.content())
   ```
3. Check if tickets require additional navigation
4. Verify ticket list URL is correct

### Issue: Incomplete Ticket Data

**Symptoms**: Ticket found but fields are "Unknown" or empty

**Solutions**:
1. Print HTML of ticket page for inspection
2. Check if data is loaded dynamically (wait for elements)
3. Add waits before parsing:
   ```python
   self._page.wait_for_selector('.ticket-title', timeout=5000)
   ```
4. Verify selectors match actual elements

## Advanced Customization

### Handling Dynamic Content

If IREN portal uses JavaScript to load content:

```python
# Wait for specific content to load
self._page.wait_for_selector('.ticket-content', state='visible')

# Wait for network to be idle
self._page.wait_for_load_state('networkidle')

# Wait for specific response
with self._page.expect_response(lambda response: 'api/tickets' in response.url):
    self._page.click('.load-tickets-button')
```

### Handling Pagination

If ticket list is paginated:

```python
def list_tickets(self, status: Optional[str] = None, limit: int = 10) -> List[Dict]:
    tickets = []
    
    while len(tickets) < limit:
        # Parse current page
        page_tickets = self._parse_current_page()
        tickets.extend(page_tickets)
        
        # Check for next page
        if not self._has_next_page():
            break
        
        # Go to next page
        self._page.click('.next-page, .pagination-next')
        self._page.wait_for_load_state('networkidle')
    
    return tickets[:limit]
```

### Adding Comment Support

To implement `add_comment()`:

```python
def add_comment(self, ticket_id: str, comment: str, public: bool = True) -> Optional[Dict]:
    # Navigate to ticket
    ticket_url = f"{self.BASE_URL}/support/tickets/{ticket_id}"
    self._page.goto(ticket_url)
    
    # Find comment form
    self._page.fill('#comment-field, textarea[name="comment"]', comment)
    
    # Set visibility if needed
    if not public:
        self._page.check('#private-comment, input[name="internal"]')
    
    # Submit
    self._page.click('button[type="submit"], .submit-comment')
    
    # Wait for confirmation
    self._page.wait_for_selector('.comment-success, .notification')
    
    return {"success": True, "comment": comment}
```

## Next Steps

1. **Inspect IREN Portal**: Use browser DevTools to understand structure
2. **Update Selectors**: Modify `vendors/iren.py` with correct selectors
3. **Test Authentication**: Verify login works
4. **Test Operations**: Verify ticket retrieval and listing work
5. **Document**: Add notes about IREN-specific quirks
6. **Iterate**: Refine selectors based on edge cases

## Getting Help

If you encounter issues:

1. Check verbose output: `export VERBOSE=1`
2. Save page HTML for inspection: `self._page.content()`
3. Take screenshots: `self._page.screenshot(path='debug.png')`
4. Share IREN portal structure (sanitized) for assistance
