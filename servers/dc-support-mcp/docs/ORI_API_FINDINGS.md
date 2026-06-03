# Ori Service Desk API Findings

## ✅ Successfully Discovered API Endpoint

**Endpoint**: `POST /rest/servicedesk/1/customer/models`

**Request Payload**:
```json
{
  "options": {
    "reqDetails": {
      "key": "SUPP-1556",
      "portalId": 3
    },
    "portalId": 3
  },
  "models": ["reqDetails"],
  "context": {
    "helpCenterAri": "ari:cloud:help::help-center/f3011a5f-3a2b-4f0c-8ce8-4a844ae642c2/30b91073-30af-40c2-95b8-9a7ba8bbec1e",
    "clientBasePath": "https://oriindustries.atlassian.net/servicedesk/customer"
  }
}
```

**Response Structure**:
```json
{
  "reqDetails": {
    "issue": {
      "key": "SUPP-1556",
      "summary": "Slow connectivity to AS40475",
      "status": "Awaiting Customer",
      "reporter": {"displayName": "tsparks@together.ai"},
      "assignee": {"displayName": "Joey Halliday"},
      "date": "2026-01-24T06:26:26+0000",
      "activityStream": [
        {
          "type": "requester-comment",
          "author": "tsparks@together.ai",
          "date": "2026-01-24T06:26:28+0000",
          "rawComment": "Slow connectivity to AS40475"
        },
        ...
      ]
    }
  },
  "xsrfToken": "..."
}
```

## ❌ Authentication Challenge

The API requires proper session authentication that:
1. Cannot be replicated with simple username/password POST
2. Uses httpOnly cookies that aren't captured in HAR exports
3. May require additional XSRF tokens or session initialization

**Current Status**:
- API endpoint discovered ✅
- Request format documented ✅
- Response structure mapped ✅
- Authentication method incomplete ❌

## Working Solutions

### Option 1: Browser Automation (✅ WORKING)

Use Cursor MCP Browser to automate login and data extraction:
- Already demonstrated in conversation
- Reliable and works now
- Slower than direct API

### Option 2: Manual Cookie Export

1. Log into Ori portal in your browser
2. Open DevTools Console
3. Run: `document.cookie`
4. Copy all cookies
5. Use them in Python requests:

```python
cookies = {
    'cloud.session.token': 'YOUR_TOKEN_HERE',
    # ... other cookies
}

response = requests.post(
    'https://oriindustries.atlassian.net/rest/servicedesk/1/customer/models',
    json=payload,
    cookies=cookies
)
```

**Cookies expire**, so this needs periodic renewal.

### Option 3: Browser Developer Tools

Manually use the browser:
1. Open ticket in browser
2. F12 → Console
3. Run this JavaScript:

```javascript
fetch('/rest/servicedesk/1/customer/models', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({
    options: {reqDetails: {key: 'SUPP-1556', portalId: 3}, portalId: 3},
    models: ['reqDetails'],
    context: {
      helpCenterAri: 'ari:cloud:help::help-center/f3011a5f-3a2b-4f0c-8ce8-4a844ae642c2/30b91073-30af-40c2-95b8-9a7ba8bbec1e',
      clientBasePath: 'https://oriindustries.atlassian.net/servicedesk/customer'
    }
  })
}).then(r => r.json()).then(data => console.log(JSON.stringify(data, null, 2)));
```

Copy the output for analysis.

## Sample Data from SUPP-1556

Extracted from HAR file analysis:

```json
{
  "key": "SUPP-1556",
  "summary": "Slow connectivity to AS40475",
  "status": "Awaiting Customer",
  "reporter": "tsparks@together.ai",
  "assignee": "Joey Halliday",
  "created": "24/Jan/26 6:26 AM",
  "organisations": ["TogetherAI"],
  "requestType": "Infrastructure Support",
  "comments": [
    {
      "author": "tsparks@together.ai",
      "date": "24/Jan/26 6:26 AM",
      "text": "Slow connectivity to AS40475"
    },
    {
      "author": "Joey Halliday",
      "date": "24/Jan/26 7:10 AM",
      "text": "Thanks for raising this issue.We will look into this for you."
    },
    ...
  ]
}
```

## Next Steps

To enable full automation:

1. **Request API Tokens from Ori**
   - Ask for Atlassian API token
   - Or service account credentials
   - Official API documentation

2. **Reverse Engineer Full Auth Flow**
   - Capture complete login sequence
   - Identify session initialization steps
   - Document cookie generation process

3. **Use Browser Automation**
   - Accept slower but reliable approach
   - Implement with Playwright/Selenium
   - Already working with Cursor MCP

## Files

- `ori_api_client.py` - Skeleton client (needs auth fix)
- `ORI_API_FINDINGS.md` - This file
- `/tmp/ori_api_response.json` - Sample API response from HAR
- `infra/oriindustries.atlassian.net.har` - Source HAR file

## Recommendation

For now, use **Browser Automation (MCP)** for reliable access. If you need faster/code-based access, request proper API credentials from Ori Industries.
