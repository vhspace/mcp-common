# Hardware Database

**Version:** 1.0.0
**Format:** Versioned JSON files

## Directory Structure

```
hardware_db/
├── schema.json                          # JSON schema for validation
├── README.md                            # This file
├── supermicro/
│   ├── _generic.json                    # Fallback for unknown Supermicro
│   └── PIO-8125GS-TNHR-NODE.json       # Specific board
├── dell/
│   ├── _generic.json
│   └── PowerEdge-R750xa.json
└── hpe/
    ├── _generic.json
    └── ProLiant-DL385-Gen11.json
```

## Naming Convention

**Files:**
- `_generic.json` - Vendor fallback (used when specific model not found)
- `{MODEL}.json` - Specific board (exact model match)

**Directories:**
- Lowercase vendor name (e.g., `supermicro/`, `dell/`, `hpe/`)

## JSON File Format

### Required Fields

```json
{
  "version": "1.0.0",          // Semver for this hardware entry
  "hardware": {
    "vendor": "Supermicro",    // Manufacturer name
    "model": "MODEL-NAME"       // Full model number
  }
}
```

### Full Example

See `supermicro/PIO-8125GS-TNHR-NODE.json` or `supermicro/AS-8125GS-TNHR.json` for complete examples with:
- Hardware specifications
- **Model variant information** (NEW: explains naming, hardware type, deployment)
- BIOS version history
- Documentation URLs
- **Research notes** (NEW: how to find docs, search keywords, verification dates)
- GPU optimization recommendations
- Known configurations

### Schema Validation

Validate your JSON against `schema.json`:
```bash
# Using jsonschema CLI (if installed)
jsonschema -i supermicro/PIO-8125GS-TNHR-NODE.json schema.json
```

## Adding New Hardware

### Step 1: Create JSON File

1. Copy an existing file as template
2. Name it with exact model number (use hyphens, not spaces)
3. Place in appropriate vendor directory

### Step 2: Fill in Data

**Minimum required:**
```json
{
  "version": "1.0.0",
  "last_updated": "2026-02-03",
  "hardware": {
    "vendor": "Supermicro",
    "model": "YOUR-MODEL",
    "description": "Your description"
  }
}
```

**Recommended additions:**
- `bios_versions` - BIOS changelog and settings
- `documentation` - URLs to manuals and support
- `gpu_optimization` - GPU-specific recommendations
- `known_configurations` - Validated setups

### Step 3: Test

```bash
cd /workspaces/together/redfish-mcp
uv run python -c "
from redfish_mcp.hardware_docs import load_hardware_database
db = load_hardware_database()
print(db.get('YOUR-MODEL'))
"
```

## New Sections for Hardware Variants (v1.1.0+)

### Model Variant Information (`model_variant_info`)

Explains what model prefixes/suffixes mean and how hardware variants differ. Essential for AI agents to understand hardware comparisons.

**Fields:**
```json
{
  "model_variant_info": {
    "prefix_meaning": "What the prefix indicates (e.g., AS=standalone, PIO=plug-in module)",
    "suffix_meaning": "What suffixes mean (e.g., -NODE=node module, custom codes)",
    "hardware_type": "standalone | multi-node-module | blade | rackmount",
    "deployment_notes": [
      "How this hardware is typically deployed",
      "Infrastructure requirements",
      "Use cases"
    ],
    "related_variants": [
      {
        "model": "Related-Model-Name",
        "difference": "How it differs from this model"
      }
    ],
    "comparison_table": {
      "deployment": "Deployment type",
      "power_supplies": "Power infrastructure",
      "management": "Management interface type",
      "density": "Space efficiency",
      "flexibility": "Portability/reconfigurability",
      "cost_per_node": "Relative cost"
    }
  }
}
```

**Example Use Case:**
When comparing `AS-8125GS-TNHR` vs `PIO-8125GS-TNHR-NODE`, AI agents can read the `model_variant_info` to understand:
- AS = Standalone server with integrated chassis
- PIO = Plug-in module for multi-node chassis
- Differences in deployment, cost, and flexibility

### Research Notes (`research_notes`)

Documents how to find and verify hardware information. Helps future researchers (human or AI) locate documentation efficiently.

**Fields:**
```json
{
  "research_notes": {
    "vendor_naming_guide": "URL to vendor's product naming convention guide",
    "search_keywords": [
      "Effective search terms",
      "Alternative model names",
      "Common abbreviations"
    ],
    "doc_discovery_method": "How docs were found (web search, vendor portal, etc.)",
    "last_verified": "2026-02-03",
    "notes": [
      "Model number breakdown and meaning",
      "Documentation quirks or gotchas",
      "Relationship to other products",
      "Special considerations"
    ]
  }
}
```

**Example Use Case:**
When researching a new Supermicro model, check `research_notes` to:
1. Learn the search keywords that work well
2. Understand model number structure (e.g., 8=8U, 2=dual socket)
3. Find the vendor naming guide for decoding similar models
4. See when information was last verified

## Version History

### Version 1.1.0 (2026-02-03)
- **Added:** `model_variant_info` section for hardware variant explanations
- **Added:** `research_notes` section for documentation discovery
- **Added:** AS-8125GS-TNHR entry (standalone variant)
- **Enhanced:** PIO-8125GS-TNHR-NODE with new sections
- **Updated:** Schema to support variant descriptions

### Version 1.0.0 (2026-02-03)
- Initial hardware database format
- Supermicro PIO-8125GS-TNHR-NODE entry
- Generic Supermicro fallback
- JSON schema definition

## BIOS Version Status

| Status | Meaning |
|--------|---------|
| `latest` | Most recent, recommended version |
| `stable` | Production-ready, older release |
| `beta` | Testing, not for production |
| `deprecated` | No longer supported |

## GPU Workload Settings

### AI Training (ai_training)
Optimized for maximum throughput:
- IOMMU: Disabled (lower overhead)
- SR-IOV: Disabled (not needed)
- Resizable BAR: Enabled (better performance)

### GPU Passthrough (gpu_passthrough)
Optimized for virtualization:
- IOMMU: Enabled (required)
- SR-IOV: Enabled (if supported)
- ACS: Enabled (isolation)

## Contributing

To add your hardware:
1. Create JSON file following the schema
2. Test with real hardware
3. Submit PR or add directly if you have access
4. Include BIOS version you tested with

## Notes

- JSON files are loaded at server startup
- Changes require server restart to take effect
- Cache TTL is 24 hours for fetched documentation
- Generic files (`_generic.json`) are fallbacks only
- Model matching is case-insensitive

## Current Coverage

**Supermicro:**
- ✅ PIO-8125GS-TNHR-NODE (8x GPU, AMD EPYC 9004, multi-node module)
- ✅ AS-8125GS-TNHR (8x GPU, AMD EPYC 9004, standalone server)
- ✅ Generic Supermicro fallback

**Dell:**
- ⏳ Coming soon

**HPE:**
- ⏳ Coming soon

**Add your hardware!** Contributions welcome.

## AI Agent Tips

### Finding Hardware Differences

When comparing models (e.g., "What's the difference between AS-8125GS and PIO-8125GS?"):
1. Load both JSON files from the database
2. Compare `model_variant_info.prefix_meaning` fields
3. Check `model_variant_info.comparison_table` for structured comparison
4. Review `related_variants` for explicit differences

### Researching New Hardware

When encountering unknown hardware:
1. Check if similar model exists in database
2. Use `research_notes.search_keywords` from similar models
3. Follow `research_notes.vendor_naming_guide` to decode model numbers
4. Use `research_notes.doc_discovery_method` patterns for new searches
5. Add findings back to database for future reference

### Understanding BIOS Quirks

When comparing BIOS settings between systems:
1. Check `bios_versions` to see firmware differences
2. Look for `known_issues` mentioning attribute naming
3. Example: BIOS 1.6 uses `SMTControl_0037`, BIOS 3.7 uses `SMTControl`
4. Use smart diff matching when BIOS versions differ
