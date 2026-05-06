# Vendor Portal MCP - User Guide

## 🚀 Quick Start (Docker - Recommended)

### Step 1: Add to Cursor MCP Settings

Edit `~/.cursor/mcp_settings.json` or your Cursor settings:

```json
{
  "mcpServers": {
    "vendor-portals": {
      "command": "docker",
      "args": [
        "run",
        "--rm",
        "-i",
        "-e", "ORI_PORTAL_USERNAME=your@email.com",
        "-e", "ORI_PORTAL_PASSWORD=yourpassword",
        "ghcr.io/together/vendor-portal-mcp:latest"
      ]
    }
  }
}
```

### Step 2: Restart Cursor

That's it! The MCP server is now available.

---

## 📋 Available Tools

### 1. Get Vendor Ticket

Fetch detailed information about a specific ticket.

**Example prompts**:
- "What's the status of ORI ticket SUPP-1556?"
- "Show me details for SUPP-1234"
- "Get the latest comments on SUPP-1556"

**Returns**:
- Ticket summary and status
- Reporter and assignee
- Full comment history (31 comments for SUPP-1556!)
- Created/updated dates
- Direct link to ticket

### 2. List Vendor Tickets

List all your tickets with filtering.

**Example prompts**:
- "Show me all open ORI tickets"
- "List my closed tickets from ORI"
- "What tickets do I have with ORI?"

**Filters**:
- Status: open, closed, all
- Reporter: me, all
- Pagination support

### 3. Create Vendor Ticket

Create a new support ticket.

**Example prompts**:
- "Create an ORI ticket for slow network performance"
- "Open a support ticket about GPU issues"

**Important Limitations**:
- ⚠️ Attachments must be ZIP format only
- ⚠️ Maximum attachment size: 20 MiB
- ⚠️ Manual creation may be easier for complex tickets

**Manual creation**: https://oriindustries.atlassian.net/servicedesk/customer/portal/3/create/299

---

## 🔧 Configuration

### Environment Variables

```bash
# Required
ORI_PORTAL_USERNAME=your@email.com
ORI_PORTAL_PASSWORD=yourpassword

# Optional
COOKIE_CACHE_DIR=~/.cache/vendor_portals  # Cookie storage location
COOKIE_MAX_AGE_HOURS=2                     # Cookie refresh interval
```

### Credential Security

**Docker**: Pass via environment variables (not stored in image)

```bash
# Option 1: Direct in MCP config
"env": {
  "ORI_PORTAL_USERNAME": "your@email.com",
  "ORI_PORTAL_PASSWORD": "yourpassword"
}

# Option 2: From host environment
docker run --rm -i \
  -e ORI_PORTAL_USERNAME \
  -e ORI_PORTAL_PASSWORD \
  vendor-portal-mcp:latest
```

**Local**: Use environment variables or config file

```bash
# ~/.vendor_portal_env
export ORI_PORTAL_USERNAME="your@email.com"
export ORI_PORTAL_PASSWORD="yourpassword"

# Source before running
source ~/.vendor_portal_env
```

---

## 📊 Performance

### First Request (Cold Start)
- **~17 seconds**: Browser automation + authentication
- Happens once per session or when cookies expire

### Subsequent Requests (Cached)
- **~1.3 seconds**: Direct API call with cached cookies
- **13x faster** than repeated browser automation
- Cookies valid for ~2 hours

### Cookie Refresh
- Automatic when cookies expire (401/403 response)
- Transparent to user
- Takes ~17s to re-authenticate

---

## 🎯 Use Cases

### 1. Quick Status Check
```
You: "What's the status of SUPP-1556?"

MCP: 
Ticket: SUPP-1556
Summary: Slow connectivity to AS40475
Status: Awaiting Customer
Assignee: Joey Halliday

Latest comment (27/Jan/26):
"No problem. I will leave it open for now."
```

### 2. Bulk Ticket Review
```
You: "Show me all my open ORI tickets"

MCP:
Found 5 open tickets:
- SUPP-1556: Slow connectivity to AS40475
- SUPP-1543: GPU node offline
- SUPP-1520: Network configuration
...
```

### 3. Comment History
```
You: "Show me the full conversation on SUPP-1556"

MCP: [Returns all 31 comments with dates and authors]
```

### 4. Create New Ticket
```
You: "Create an ORI ticket about slow storage performance in cluster X"

MCP: [Guides you through ticket creation or provides manual link]
```

---

## 🐛 Troubleshooting

### "Session expired" or 401 errors

**Solution**: Cookies expired (normal after 2 hours)
- MCP will automatically re-authenticate
- Takes ~17s for one request, then fast again

### "Docker not found"

**Solution**: Install Docker
- Mac: https://docs.docker.com/desktop/install/mac-install/
- Linux: `sudo apt install docker.io`
- Windows: https://docs.docker.com/desktop/install/windows-install/

### "Failed to authenticate"

**Check**:
1. Credentials correct? (ORI_PORTAL_USERNAME, ORI_PORTAL_PASSWORD)
2. Account has portal access?
3. Network connectivity to oriindustries.atlassian.net?

**Debug**:
```bash
# Test credentials manually
docker run --rm -i \
  -e ORI_PORTAL_USERNAME=your@email \
  -e ORI_PORTAL_PASSWORD=yourpass \
  vendor-portal-mcp:latest \
  python ori_session_manager.py SUPP-1556
```

### "Attachment too large"

**Limitation**: Ori portal has 20 MiB limit for attachments

**Solutions**:
1. Compress files more
2. Split into multiple attachments
3. Use external file sharing (Google Drive, etc.) and link in ticket
4. Create ticket manually via web portal

### "Only ZIP files accepted"

**Limitation**: Ori portal only accepts ZIP attachments

**Solutions**:
```bash
# Create ZIP from files
zip attachment.zip file1.log file2.txt

# Or compress directory
zip -r logs.zip /path/to/logs/
```

---

## 🔐 Security Best Practices

### 1. Credential Storage

**❌ Don't**:
```json
{
  "env": {
    "ORI_PORTAL_PASSWORD": "plaintext_password_here"
  }
}
```

**✅ Do**:
```bash
# Store in environment
export ORI_PORTAL_PASSWORD="..."

# Or use secrets manager
export ORI_PORTAL_PASSWORD=$(aws secretsmanager get-secret-value --secret-id ori-portal-creds --query SecretString --output text | jq -r .password)
```

### 2. Cookie Cache

Cookies are cached at `~/.ori_session_cookies.pkl`

**Set proper permissions**:
```bash
chmod 600 ~/.ori_session_cookies.pkl
```

### 3. Docker Security

**Use read-only filesystem**:
```bash
docker run --rm -i --read-only \
  -v /tmp/cache:/root/.cache/vendor_portals \
  -e ORI_PORTAL_USERNAME \
  -e ORI_PORTAL_PASSWORD \
  vendor-portal-mcp:latest
```

---

## 📚 Advanced Usage

### Custom Cookie Cache Location

```bash
docker run --rm -i \
  -v ~/.my_cache:/root/.cache/vendor_portals \
  -e ORI_PORTAL_USERNAME \
  -e ORI_PORTAL_PASSWORD \
  vendor-portal-mcp:latest
```

### Multiple Vendor Configs

```json
{
  "mcpServers": {
    "ori-portal": {
      "command": "docker",
      "args": ["run", "--rm", "-i",
               "-e", "ORI_PORTAL_USERNAME=...",
               "-e", "ORI_PORTAL_PASSWORD=...",
               "vendor-portal-mcp:latest"]
    },
    "evocative-portal": {
      "command": "docker",
      "args": ["run", "--rm", "-i",
               "-e", "EVOCATIVE_API_KEY=...",
               "vendor-portal-mcp:latest",
               "--vendor", "evocative"]
    }
  }
}
```

### Debugging

**Enable verbose logging**:
```bash
docker run --rm -i \
  -e ORI_PORTAL_USERNAME \
  -e ORI_PORTAL_PASSWORD \
  -e DEBUG=1 \
  vendor-portal-mcp:latest
```

**Save browser screenshots**:
```bash
docker run --rm -i \
  -v /tmp/screenshots:/tmp \
  -e ORI_PORTAL_USERNAME \
  -e ORI_PORTAL_PASSWORD \
  -e SAVE_SCREENSHOTS=1 \
  vendor-portal-mcp:latest
```

---

## 🌐 Supported Vendors

### Currently Implemented

| Vendor | Status | Auth Method | Performance |
|--------|--------|-------------|-------------|
| Ori Industries | ✅ Working | Playwright + Cookie Cache | 1.3s (cached) |

### Planned

| Vendor | Portal Type | Estimated Effort |
|--------|-------------|------------------|
| Evocative | TBD | 1-2 days |
| Crusoe | TBD | 1-2 days |
| Applied Digital | TBD | 1-2 days |

---

## 📖 API Reference

### Python Library Usage

```python
from ori_session_manager import OriSessionManager

# Initialize (authenticates on first use)
manager = OriSessionManager(
    email="your@email.com",
    password="yourpassword"
)

# Get a ticket
ticket = manager.get_ticket("SUPP-1556")
print(f"{ticket['summary']}: {ticket['status']}")

# List requests
requests = manager.list_requests(status="open")
for req in requests['issues']:
    print(f"{req['key']}: {req['summary']}")
```

### MCP Tool Calls

```json
// Get ticket
{
  "tool": "get_vendor_ticket",
  "arguments": {
    "vendor": "ori",
    "ticket_id": "SUPP-1556",
    "include_comments": true
  }
}

// List tickets
{
  "tool": "list_vendor_tickets",
  "arguments": {
    "vendor": "ori",
    "status": "open",
    "page": 1
  }
}
```

---

## 💡 Tips & Tricks

### 1. Batch Queries
```
You: "Check status of SUPP-1556, SUPP-1543, and SUPP-1520"

MCP will fetch all three in parallel (if implemented)
```

### 2. Monitoring
```
You: "Show me all critical ORI tickets"

MCP: [Filters by priority and status]
```

### 3. Integration with Other Tools
```
You: "Get SUPP-1556 and create a Linear ticket for our team"

MCP: 
1. Fetches ORI ticket
2. Creates Linear ticket with details
3. Links them together
```

---

## 🆘 Support

### Internal Support
- **Slack**: #vendor-portal-mcp
- **Docs**: See `scripts/` directory
- **Issues**: GitHub Issues

### Vendor Portals
- **Ori Industries**: https://oriindustries.atlassian.net/servicedesk/customer/portal/3
- **Create Ticket**: https://oriindustries.atlassian.net/servicedesk/customer/portal/3/create/299
- **View Requests**: https://oriindustries.atlassian.net/servicedesk/customer/user/requests

---

## 📝 Known Limitations

### Ori Industries Portal

1. **Attachments**:
   - ⚠️ ZIP format only
   - ⚠️ 20 MiB maximum size
   - Solution: Compress before uploading

2. **Search**:
   - ⚠️ No full-text search via API (yet)
   - Workaround: List all and filter locally

3. **Bulk Operations**:
   - ⚠️ No bulk update/close
   - Must operate on tickets individually

4. **Custom Fields**:
   - Some custom fields may not be exposed via API
   - May need manual portal access for complex fields

---

## 🔄 Updates

### Check for Updates

```bash
# Pull latest image
docker pull ghcr.io/together/vendor-portal-mcp:latest

# Restart Cursor to use new version
```

### Changelog

**v1.0.0** (Current)
- ✅ Ori Industries support
- ✅ Get ticket details
- ✅ List requests (basic)
- ✅ Cookie caching
- ✅ Auto-refresh

**v1.1.0** (Planned)
- ⬜ Create tickets
- ⬜ Add comments
- ⬜ Upload attachments
- ⬜ Advanced search

**v2.0.0** (Future)
- ⬜ Evocative support
- ⬜ Crusoe support
- ⬜ Multi-vendor queries
- ⬜ Ticket analytics

---

## 🎓 Learning Resources

### Understanding the System

1. **Architecture**: See `VENDOR_PORTAL_MCP_DESIGN.md`
2. **API Details**: See `ORI_API_FINDINGS.md`
3. **Deployment**: See `DEPLOYMENT_STRATEGIES.md`

### Contributing

Want to add a new vendor?

1. Discover their API (HAR file analysis)
2. Implement handler class
3. Add to router
4. Write tests
5. Submit PR

See `VENDOR_PORTAL_MCP_DESIGN.md` for handler interface.

---

## 💬 Example Conversations

### Checking Ticket Status

```
You: What's happening with SUPP-1556?