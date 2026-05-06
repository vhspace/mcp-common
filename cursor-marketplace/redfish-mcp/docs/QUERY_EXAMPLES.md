# Redfish Query Tool Examples

The `redfish_query` tool provides flexible, targeted queries for specific settings without fetching entire objects.

## Real Hardware Examples (192.168.196.54 - Supermicro)

### Check SMT (Hyper-Threading) Status

```python
redfish_query(
  query_type="bios_attribute",
  key="SMTControl"
)
```

**Result:**
```json
{
  "ok": true,
  "found": true,
  "current_value": "Auto",
  "setter_info": {
    "tool": "redfish_set_bios_attributes",
    "writable": true,
    "example": {
      "tool": "redfish_set_bios_attributes",
      "arguments": {
        "host": "192.168.196.54",
        "attributes": {"SMTControl": "Enabled"},
        "allow_write": true
      }
    }
  }
}
```

---

### Check PXE Network Boot Settings

```python
redfish_query(
  query_type="list_bios_attributes",
  key="PXE"
)
```

**Result:**
```json
{
  "ok": true,
  "count": 3,
  "attributes": {
    "IPv4PXESupport": "Enabled",
    "IPv6PXESupport": "Disabled",
    "PXEBootWaitTime": "0"
  }
}
```

---

### Check Boot Target

```python
redfish_query(
  query_type="boot_setting",
  key="target"
)
```

**Result:**
```json
{
  "ok": true,
  "current_value": "Pxe",
  "setter_info": {
    "tool": "redfish_set_nextboot",
    "allowable_values": [
      "None", "Pxe", "Floppy", "Cd", "Usb",
      "Hdd", "BiosSetup", "UsbCd", "Diags", "UefiBootNext"
    ]
  }
}
```

---

### Check System Power State

```python
redfish_query(
  query_type="power_state"
)
```

**Result:**
```json
{
  "ok": true,
  "found": true,
  "current_value": "Off"
}
```

---

### List Available Network Interfaces

```python
redfish_query(
  query_type="list_nics"
)
```

**Result:**
```json
{
  "ok": true,
  "count": 11,
  "nics": [
    {"Id": "1", "Name": "Network Device View", ...},
    {"Id": "2", "Name": "Network Device View", ...}
  ]
}
```

---

### Find PCIe-Related Settings

```python
redfish_query(
  query_type="list_bios_attributes",
  key="PCIe"
)
```

**Result:**
```json
{
  "ok": true,
  "count": 31,
  "attributes": {
    "CPU1PCIePackageGroupG0": "Auto",
    "CPU1PCIePackageGroupG1": "Auto",
    "CPU1PCIePackageGroupG2": "Auto"
  }
}
```

---

### Find Network Stack Settings

```python
redfish_query(
  query_type="list_bios_attributes",
  key="Network"
)
```

**Result (13 matches):**
```json
{
  "attributes": {
    "NetworkStack": "Enabled",
    "UEFINETWORKBootOption_1": "(B48/D0/F0) UEFI PXE IPv4 Nvidia Network Adapter",
    "UEFINETWORKBootOption_10": "Disabled"
  }
}
```

---

## Query Type Reference

| Query Type | Description | Requires Key? | Writable? |
|------------|-------------|---------------|-----------|
| `bios_attribute` | Check specific BIOS setting | ✅ Yes | Maybe* |
| `boot_setting` | Check boot override | ⚠️ Optional | ✅ Yes |
| `power_state` | Check power on/off | ❌ No | ⚠️ Future |
| `health` | Check system health | ❌ No | ❌ No |
| `list_nics` | List network interfaces | ❌ No | Varies |
| `nic_pxe` | Check NIC PXE status | ✅ Yes (NIC ID) | Varies |
| `list_bios_attributes` | List/filter BIOS attributes | ⚠️ Optional filter | Maybe* |

\* BIOS writability depends on firmware support

---

## Common Use Cases

### Pre-Flight Checks
```python
# Before deploying a GPU workload
queries = [
  ("Above4GDecoding", "bios_attribute"),
  ("PCIeACS", "bios_attribute"),
  ("NetworkStack", "bios_attribute"),
]

for key, qtype in queries:
  redfish_query(query_type=qtype, key=key)
```

### Network Configuration Audit
```python
# Check all network-related settings
redfish_query(query_type="list_bios_attributes", key="Network")
redfish_query(query_type="list_bios_attributes", key="PXE")
redfish_query(query_type="list_nics")
```

### Find Unknown Setting Names
```python
# Search for memory/MMIO related settings
redfish_query(query_type="list_bios_attributes", key="MMIO")
redfish_query(query_type="list_bios_attributes", key="Memory")
redfish_query(query_type="list_bios_attributes", key="BAR")
```

---

## Tips

1. **Use filters**: `list_bios_attributes` with `key` parameter to narrow results
2. **Include setter info**: Set `include_setter_info=true` to see how to modify settings
3. **Check writability**: Setter info shows if the setting can be modified via Redfish
4. **Preview changes**: Use `execution_mode="render_curl"` to see the HTTP calls

---

## Performance

- Individual queries: ~0.5-2 seconds
- Much faster than fetching entire BIOS object (247 attributes)
- Efficient for automation and health checks
