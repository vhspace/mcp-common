# MCP Deployment Strategies for Multi-User Environments

## The Challenge

Playwright requires:
- Browser installation (~180MB Chromium)
- System dependencies (20+ packages)
- Not ideal for every user's local environment

## Solution: Multiple Deployment Options

### ⭐ Option 1: Docker Container (Recommended)

**Best for**: Production use, team deployment, CI/CD

Package everything in a Docker container with Playwright pre-installed.

```dockerfile
FROM python:3.12-slim

# Install Playwright system dependencies
RUN apt-get update && apt-get install -y \
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers
RUN playwright install chromium

# Copy MCP server code
COPY vendor_portal_mcp/ /app/
WORKDIR /app

# Run MCP server
CMD ["python", "mcp_server.py"]
```

**User Experience**:
```json
{
  "mcpServers": {
    "vendor-portals": {
      "command": "docker",
      "args": [
        "run",
        "--rm",
        "-e", "ORI_PORTAL_USERNAME",
        "-e", "ORI_PORTAL_PASSWORD",
        "vendor-portal-mcp:latest"
      ]
    }
  }
}
```

**Pros**:
- ✅ Zero setup for users
- ✅ Consistent environment
- ✅ Works everywhere (Mac, Linux, Windows)
- ✅ Isolated dependencies

**Cons**:
- Requires Docker installed
- ~200MB image size

---

### Option 2: Pre-Auth Service (Separate Auth Server)

**Best for**: Large teams, shared infrastructure

Run ONE authentication service that all users connect to.

```
┌─────────────────────────────────────────────┐
│          Auth Service (Server)              │
│  - Runs Playwright                          │
│  - Handles auth for all users               │
│  - Returns session cookies via API          │
└─────────────────────────────────────────────┘
              ↓ HTTP API
┌─────────────────────────────────────────────┐
│    Lightweight MCP Server (Local)           │
│  - No Playwright needed                     │
│  - Just requests library                    │
│  - Calls auth service for cookies           │
└─────────────────────────────────────────────┘
```

**Auth Service API**:
```python
# POST /auth/ori
{
  "username": "email@company.com",
  "password": "secret"
}

# Response:
{
  "cookies": [...],
  "expires_at": "2026-02-03T20:00:00Z"
}
```

**MCP Server** (no Playwright):
```python
def get_session():
    # Call auth service instead of Playwright
    response = requests.post(
        "https://auth.company.internal/auth/ori",
        json={"username": user, "password": pwd}
    )
    return response.json()["cookies"]
```

**Pros**:
- ✅ Lightweight local MCP server
- ✅ Centralized auth management
- ✅ Easy to monitor/debug
- ✅ Can cache for all users

**Cons**:
- Requires running separate service
- Network dependency
- Security: credentials sent to service

---

### Option 3: Manual Cookie Export (No Automation)

**Best for**: Simple deployments, low-tech users

Users manually export cookies from their browser.

**Setup Script**:
```bash
#!/bin/bash
# export_cookies.sh

cat << 'EOF'
1. Open Chrome/Firefox
2. Log into https://oriindustries.atlassian.net
3. Press F12 (DevTools)
4. Go to Console tab
5. Paste this and press Enter:

copy(JSON.stringify(document.cookie.split(';').map(c => {
  const [name, value] = c.trim().split('=');
  return {name, value};
})))

6. Cookie data copied to clipboard!
7. Paste into: ~/.vendor_portal_cookies.json
EOF
```

Or use a **browser extension**:
- EditThisCookie
- Cookie-Editor
- Export cookies as JSON

**MCP Server**:
```python
def load_cookies():
    # Just read from file
    with open(os.path.expanduser("~/.vendor_portal_cookies.json")) as f:
        return json.load(f)
```

**Pros**:
- ✅ No dependencies
- ✅ Simple to understand
- ✅ User controls auth

**Cons**:
- ❌ Manual cookie refresh every 2 hours
- ❌ User friction

---

### Option 4: Hybrid Approach (Best UX)

**Combine all approaches** with automatic fallback:

```python
class VendorAuth:
    def get_cookies(self):
        # Try methods in order:
        
        # 1. Load from cache (fast)
        if self._load_cached_cookies():
            return self.cookies
        
        # 2. Try auth service (if configured)
        if os.getenv("AUTH_SERVICE_URL"):
            return self._get_from_auth_service()
        
        # 3. Try Playwright (if available)
        if self._has_playwright():
            return self._playwright_auth()
        
        # 4. Ask user to export manually
        raise NeedManualCookiesError(
            "Please export cookies manually. See: "
            "https://docs.company.com/vendor-portal-mcp#manual-setup"
        )
```

**Configuration**:
```yaml
# ~/.vendor_portal_config.yml
auth_strategy: auto  # auto, docker, service, manual, playwright

auth_service:
  url: https://auth.company.internal  # optional

playwright:
  enabled: true  # optional, defaults to false
  headless: true
```

**Pros**:
- ✅ Works for everyone
- ✅ Graceful degradation
- ✅ Flexible deployment

---

## Recommended: Docker + Manual Fallback

### For Distribution

**Primary**: Docker image
```bash
docker pull ghcr.io/your-org/vendor-portal-mcp:latest
```

**Fallback**: Manual cookie mode
```bash
pip install vendor-portal-mcp
# Then manually export cookies
```

### Implementation

```python
# mcp_server.py
import os
from pathlib import Path

class VendorPortalMCP:
    def __init__(self):
        # Detect environment
        self.in_docker = Path("/.dockerenv").exists()
        self.has_playwright = self._check_playwright()
        
        if self.in_docker or self.has_playwright:
            # Use automated auth
            from .ori_session_manager import OriSessionManager
            self.ori = OriSessionManager(user, pwd)
        else:
            # Use manual cookies
            from .ori_manual_session import OriManualSession
            self.ori = OriManualSession()
            print("⚠️  Manual cookie mode. See docs for setup.")
    
    def _check_playwright(self):
        try:
            import playwright
            return True
        except ImportError:
            return False
```

### User Documentation

```markdown
# Installation

## Option 1: Docker (Recommended)

Add to your MCP config:

```json
{
  "mcpServers": {
    "vendor-portals": {
      "command": "docker",
      "args": ["run", "--rm", 
               "-e", "ORI_PORTAL_USERNAME=your@email.com",
               "-e", "ORI_PORTAL_PASSWORD=yourpassword",
               "vendor-portal-mcp:latest"]
    }
  }
}
```

## Option 2: Local (Manual Cookies)

1. Install:
   ```bash
   pip install vendor-portal-mcp
   ```

2. Export cookies (one time):
   ```bash
   vendor-portal-mcp export-cookies ori
   ```
   
3. Follow browser prompts to export cookies

4. Add to MCP config:
   ```json
   {
     "mcpServers": {
       "vendor-portals": {
         "command": "python",
         "args": ["-m", "vendor_portal_mcp"]
       }
     }
   }
   ```

Note: Cookies expire after 2 hours. Re-run `export-cookies` when needed.
```

---

## Development Workflow

### For Contributors

```bash
# Clone repo
git clone ...
cd vendor-portal-mcp

# Option A: Use Docker for testing
docker-compose up

# Option B: Local with Playwright
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
playwright install chromium
python -m playwright install-deps

# Option C: Local manual mode
uv pip install -e .
# Export cookies manually
```

### CI/CD

```yaml
# .github/workflows/test.yml
- name: Test with Playwright
  run: |
    playwright install chromium
    playwright install-deps
    pytest tests/

- name: Build Docker image
  run: docker build -t vendor-portal-mcp .

- name: Test Docker image
  run: |
    docker run --rm \
      -e ORI_PORTAL_USERNAME=${{ secrets.ORI_USER }} \
      -e ORI_PORTAL_PASSWORD=${{ secrets.ORI_PASS }} \
      vendor-portal-mcp test
```

---

## Security Considerations

### Docker
- ✅ Credentials via environment variables
- ✅ No credentials in image
- ✅ Can use secrets management

### Auth Service
- ⚠️ TLS required
- ⚠️ Credentials in transit
- ✅ Centralized audit logs
- ✅ Can revoke access

### Manual Cookies
- ⚠️ User stores cookies locally
- ✅ No automation = no credential storage
- ⚠️ Cookie files should be 600 permissions

---

## Recommendation

**For your team**: Use **Docker** as primary distribution

```
1. Build once: docker build -t vendor-portal-mcp .
2. Push to registry: docker push ghcr.io/together/vendor-portal-mcp
3. Users add one line to MCP config
4. Done! No Playwright setup needed for users
```

**Implementation priority**:
1. ✅ Get Playwright version working (DONE)
2. Create Dockerfile
3. Add manual cookie fallback
4. (Optional) Build auth service later if needed

This gives you maximum flexibility with minimum user friction!
