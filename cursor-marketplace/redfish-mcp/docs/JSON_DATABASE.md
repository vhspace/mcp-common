# JSON-Based Hardware Database

**Version:** 1.2.0  
**Date:** 2026-02-03  
**Status:** ✅ Production Ready

## Overview

Hardware database is now **versioned JSON files** instead of hardcoded Python dictionaries. This makes it:
- ✅ **Easy to add hardware** without code changes
- ✅ **Git-friendly** for version control
- ✅ **Collaborative** - anyone can contribute hardware definitions
- ✅ **Validated** with JSON schema
- ✅ **Documented** with examples and templates

## Architecture Change

### Before (v1.1.0)
```python
# Hardcoded in hardware_docs.py
HARDWARE_DATABASE = {
    "PIO-8125GS-TNHR-NODE": {
        "vendor": "Supermicro",
        ...
    }
}
```

### After (v1.2.0)
```
hardware_db/
├── schema.json                      # Validation
├── template.json                    # Template
├── README.md                        # Guide
└── supermicro/
    ├── _generic.json                # v1.0.0
    └── PIO-8125GS-TNHR-NODE.json   # v1.0.0
```

## Directory Structure

```
hardware_db/
├── schema.json              JSON Schema for validation
├── template.json            Template for creating new entries
├── README.md                How to add hardware
│
├── supermicro/              Vendor directory
│   ├── _generic.json        Fallback (any Supermicro)
│   └── PIO-8125GS-TNHR-NODE.json  Specific model
│
├── dell/                    (Future)
│   ├── _generic.json
│   └── PowerEdge-R750xa.json
│
└── hpe/                     (Future)
    ├── _generic.json
    └── ProLiant-DL385-Gen11.json
```

## JSON File Format

### Minimal Example
```json
{
  "version": "1.0.0",
  "last_updated": "2026-02-03",
  "hardware": {
    "vendor": "Supermicro",
    "model": "PIO-8125GS-TNHR-NODE",
    "description": "8-GPU server for AI training"
  }
}
```

### Full Example (Your Board)
```json
{
  "$schema": "../schema.json",
  "version": "1.0.0",
  "last_updated": "2026-02-03",
  "hardware": {
    "vendor": "Supermicro",
    "model": "PIO-8125GS-TNHR-NODE",
    "socket": "AMD EPYC 9004 series",
    "gpu_slots": 8,
    "pcie_lanes": 128,
    "max_memory": "4TB DDR5"
  },
  "bios_versions": {
    "3.7a": {
      "release_date": "2025-09-20",
      "status": "latest",
      "changes": [...],
      "recommended_settings": {...}
    }
  },
  "documentation": {...},
  "gpu_optimization": {...},
  "known_configurations": [...]
}
```

See `hardware_db/supermicro/PIO-8125GS-TNHR-NODE.json` for complete example.

## Adding New Hardware

### Step 1: Copy Template
```bash
cd hardware_db
mkdir -p dell
cp template.json dell/PowerEdge-R750xa.json
```

### Step 2: Edit JSON File
- Set `version` (start with 1.0.0)
- Set `last_updated` (YYYY-MM-DD)
- Fill in `hardware` section
- Add `bios_versions` you know about
- Add `documentation` URLs
- Add `gpu_optimization` if relevant

### Step 3: Test
```bash
cd /workspaces/together/redfish-mcp
uv run python -c "
from redfish_mcp.hardware_docs import load_hardware_database
db = load_hardware_database()
print(list(db.keys()))
"
```

### Step 4: Verify
Test against real hardware:
```bash
uv run python << 'EOF'
import asyncio
from redfish_mcp.mcp_server import create_mcp_app

async def test():
    _, tools = create_mcp_app()
    result = await tools["redfish_get_hardware_docs"](
        host="YOUR.IP.HERE",
        user="admin",
        password="password"
    )
    print(result)

asyncio.run(test())
EOF
```

## Versioning Hardware Entries

Each hardware JSON file has its own version:

```json
{
  "version": "1.0.0",    // Version of THIS hardware entry
  "last_updated": "2026-02-03"
}
```

**Version updates:**
- Patch (1.0.0 → 1.0.1): Fix typos, minor corrections
- Minor (1.0.0 → 1.1.0): Add BIOS versions, documentation URLs
- Major (1.0.0 → 2.0.0): Breaking format changes

## Matching Logic

The system tries to match hardware in this order:

1. **Exact model match**
   ```
   System reports: "PIO-8125GS-TNHR-NODE"
   Matches: hardware_db/supermicro/PIO-8125GS-TNHR-NODE.json
   ```

2. **Partial model match**
   ```
   System reports: "PIO-8125GS-TNHR"
   Matches: PIO-8125GS-TNHR-NODE.json (contains "PIO-8125GS-TNHR")
   ```

3. **Vendor fallback**
   ```
   System reports: Supermicro (unknown model)
   Matches: hardware_db/supermicro/_generic.json
   ```

4. **No match**
   ```
   Returns error with guidance to add hardware
   ```

## Caching

**Dual-layer caching:**
- Memory cache (per MCP server instance)
- File cache (`~/.cache/redfish-mcp/docs/`)
- TTL: 24 hours
- Survives server restarts

**Performance:**
- First call: ~2-3s (queries BMC + loads JSON)
- Cached calls: ~100ms (instant)

## Schema Validation

The `schema.json` defines the structure. You can validate your JSON:

```bash
# Install jsonschema CLI
pip install check-jsonschema

# Validate a file
check-jsonschema --schemafile hardware_db/schema.json \
  hardware_db/supermicro/PIO-8125GS-TNHR-NODE.json
```

## Current Database

### Supermicro
- ✅ **PIO-8125GS-TNHR-NODE** (v1.0.0)
  - 8x GPU slots, AMD EPYC 9004
  - BIOS 3.7a + 3.6b documented
  - GPU optimization guides
  - AI training + GPU passthrough workload presets
- ✅ **_generic** (v1.0.0)
  - Fallback for unknown Supermicro models
  - Common GPU settings
  - Support URLs

### Dell
- ⏳ **Coming soon** - Add your boards!

### HPE
- ⏳ **Coming soon** - Add your boards!

## Contributing Hardware

Want to add your hardware? It's easy:

1. **Copy template.json**
2. **Fill in your hardware details**
3. **Test with real hardware**
4. **Submit** (PR or direct commit if you have access)

No Python code changes needed!

## Benefits

### For Admins
- 📝 **Easy maintenance** - Edit JSON, not code
- 🔄 **Version control** - Track hardware data changes in git
- 📊 **Clear history** - See who added what when
- 🤝 **Collaboration** - Anyone can contribute

### For AI Agents
- 🧠 **Hardware awareness** - Know capabilities before configuring
- 🎯 **Workload-specific** - AI training vs virtualization presets
- 📚 **Documentation** - Manual links always available
- ⚡ **Fast** - Cached for instant access

### For Development
- 🚫 **No code deploys** for hardware additions
- ✅ **Schema validated** - Catch errors early
- 📖 **Self-documenting** - JSON is readable
- 🧪 **Easy testing** - Just edit JSON and reload

## Examples

### Check What's in the Database
```python
from redfish_mcp.hardware_docs import load_hardware_database

db = load_hardware_database()
print(f"Hardware entries: {len(db)}")
for model in db.keys():
    print(f"  • {model}")
```

### Get Board Details
```python
db = load_hardware_database()
board = db.get("PIO-8125GS-TNHR-NODE")
print(f"GPU Slots: {board['hardware']['gpu_slots']}")
print(f"BIOS Versions: {list(board['bios_versions'].keys())}")
```

### Check BIOS Recommendations for Workload
```python
board = db.get("PIO-8125GS-TNHR-NODE")
ai_settings = board['gpu_optimization']['recommended_for_workload']['ai_training']
print("Settings for AI training:")
for key, value in ai_settings.items():
    print(f"  {key}: {value}")
```

## Migration from v1.1.0

**No changes needed!** The API is identical. Hardware data just comes from JSON instead of Python code.

**Old installations will work** because the code supports both:
- New JSON database (preferred)
- Old hardcoded dict (fallback if JSON not found)

## Summary

**What changed:**
- Hardware data: Python dict → JSON files
- Database location: In code → `hardware_db/` directory
- Adding hardware: Code change → JSON file creation

**What didn't change:**
- API: Same `redfish_get_hardware_docs` tool
- Caching: Still 24-hour dual-layer
- Matching: Same logic (exact → partial → vendor)
- Performance: Still fast with caching

**Result:**
- ✅ Easier to maintain
- ✅ More collaborative
- ✅ Git-friendly
- ✅ Schema-validated
- ✅ Self-documenting

**v1.2.0 makes hardware management truly data-driven!** 📊
