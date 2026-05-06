# Hardware Database - Quick Reference

## Finding Hardware Variant Information

### Quick Lookup by Model

```bash
# List available hardware
ls hardware_db/supermicro/

# Read specific model
cat hardware_db/supermicro/AS-8125GS-TNHR.json | jq '.model_variant_info'
```

### Common Questions

#### "What does the model prefix mean?"

**Location:** `model_variant_info.prefix_meaning`

**Examples:**
- **AS** = A+ Server (standalone system)
- **PIO** = Plug-In Module (multi-node component)
- **SYS** = Complete system
- **MBD** = Motherboard only

#### "What does the suffix mean?"

**Location:** `model_variant_info.suffix_meaning`

**Examples:**
- **-NODE** = Individual node module for multi-node chassis
- **-1-OI021** = Custom configuration (OI021 = Ori Industries order code)
- **-TNHR** = Thermal/configuration designation

#### "How do I find documentation for this hardware?"

**Location:** `research_notes.search_keywords` and `research_notes.vendor_naming_guide`

**Example:**
```json
{
  "search_keywords": [
    "Supermicro AS-8125GS specifications",
    "8125GS H100 server"
  ],
  "vendor_naming_guide": "https://www.supermicro.com/products/Product_Naming_Convention/Naming_AS_AMD.cfm"
}
```

#### "What's the difference between two similar models?"

**Location:** `model_variant_info.comparison_table` and `model_variant_info.related_variants`

**Example:**
```json
{
  "related_variants": [
    {
      "model": "AS-8125GS-TNHR",
      "difference": "Standalone server vs plug-in module"
    }
  ]
}
```

## Model Number Decoder

### Supermicro AMD (AS/PIO) Format

**Pattern:** `PREFIX-[form][drive][socket][gen][type]-SUFFIX`

**Example:** `AS-8125GS-TNHR-1-OI021`

| Part | Meaning | Example |
|------|---------|---------|
| `AS` | A+ Server (standalone) | `AS` |
| `8` | Form factor | `8` = 8U |
| `1` | Drive type | `1` = 2.5" drives |
| `2` | Socket count | `2` = Dual socket |
| `5` | CPU generation | `5` = 5th gen (EPYC 9004) |
| `GS` | Platform type | `GS` = GPU server |
| `TNHR` | Configuration | Thermal/power design |
| `-1-OI021` | Custom suffix | Customer config code |

**Reference:** Check `research_notes.notes` for model breakdown

## Quick Comparisons

### AS vs PIO (Standalone vs Multi-Node)

| Aspect | AS (Standalone) | PIO (Multi-Node) |
|--------|----------------|------------------|
| **Deployment** | Independent 8U unit | Module in shared chassis |
| **Power** | 6x integrated PSUs | Shared chassis power |
| **Management** | Independent BMC | Shared management |
| **Density** | Lower | Higher |
| **Flexibility** | Easy to move | Requires chassis |
| **Cost/Node** | Higher | Lower |

**Source:** `model_variant_info.comparison_table`

## BIOS Version Differences

### Attribute Naming Quirks

Different BIOS versions use different attribute names:

| BIOS Version | Naming Style | Example |
|--------------|-------------|---------|
| 1.6 (older) | With suffix | `SMTControl_0037` |
| 3.7 (newer) | Simplified | `SMTControl` |

**Location:** `bios_versions[version].known_issues`

**Note:** Use smart diff matching when comparing systems with different BIOS versions.

## Search Strategy

### Finding Docs for Unknown Hardware

1. **Check similar model** in database
   ```bash
   ls hardware_db/supermicro/*8125*.json
   ```

2. **Use search keywords** from similar model
   ```bash
   cat hardware_db/supermicro/AS-8125GS-TNHR.json | jq '.research_notes.search_keywords[]'
   ```

3. **Follow naming guide** to decode model
   ```bash
   cat hardware_db/supermicro/AS-8125GS-TNHR.json | jq '.research_notes.vendor_naming_guide'
   ```

4. **Apply pattern** to new model
   - If you know AS-8125GS, you can decode AS-9125GS
   - Change: 8U → 9U form factor

## Common Hardware Types

### Deployment Categories

**Location:** `model_variant_info.hardware_type`

| Type | Description | Examples |
|------|-------------|----------|
| `standalone` | Complete system with chassis | AS-8125GS-TNHR |
| `multi-node-module` | Node for shared chassis | PIO-8125GS-TNHR-NODE |
| `blade` | Blade server for blade chassis | Coming soon |
| `rackmount` | Standard rackmount server | Most common |

## AI Agent Tips

### Comparing Two Models

```python
# Load models
model_a = load_hardware_db("AS-8125GS-TNHR")
model_b = load_hardware_db("PIO-8125GS-TNHR-NODE")

# Get key differences
type_a = model_a["model_variant_info"]["hardware_type"]
type_b = model_b["model_variant_info"]["hardware_type"]

# Structured comparison
comparison = model_b["model_variant_info"]["comparison_table"]
```

### Understanding Naming

```python
# Get prefix meaning
prefix = model["model_variant_info"]["prefix_meaning"]
# → "AS = A+ Server (standalone)"

# Get suffix meaning
suffix = model["model_variant_info"]["suffix_meaning"]
# → "-NODE = node module"

# Get deployment notes
notes = model["model_variant_info"]["deployment_notes"]
# → ["Shares infrastructure", "Higher density", ...]
```

### Finding Related Hardware

```python
# Get related variants
variants = model["model_variant_info"]["related_variants"]
# → [{"model": "AS-8125GS", "difference": "Standalone vs module"}]

# For each variant, load its data
for variant in variants:
    other = load_hardware_db(variant["model"])
    # Compare specs...
```

## Data Freshness

Check when information was last verified:

```bash
# Last database update
cat hardware_db/supermicro/AS-8125GS-TNHR.json | jq '.last_updated'

# Last documentation verification
cat hardware_db/supermicro/AS-8125GS-TNHR.json | jq '.research_notes.last_verified'
```

## Contributing

When adding new hardware:

1. ✅ Copy template from existing model
2. ✅ Fill in `model_variant_info` with:
   - Prefix/suffix meanings
   - Hardware type
   - Deployment notes
   - Related variants
3. ✅ Add `research_notes` with:
   - Search keywords that worked
   - Documentation discovery method
   - Model number breakdown
   - Last verification date
4. ✅ Test JSON schema validation
5. ✅ Update this quick reference if needed

## File Locations

```
hardware_db/
├── schema.json                      # JSON schema
├── README.md                        # Full documentation
├── QUICK_REFERENCE.md              # This file
└── supermicro/
    ├── AS-8125GS-TNHR.json        # Standalone variant
    ├── PIO-8125GS-TNHR-NODE.json  # Multi-node variant
    └── _generic.json               # Fallback
```

## Need Help?

- **Schema:** See `hardware_db/schema.json`
- **Examples:** See `supermicro/*.json` files
- **Full docs:** See `hardware_db/README.md`
- **Enhancement details:** See `HARDWARE_DB_VARIANT_ENHANCEMENT.md`
