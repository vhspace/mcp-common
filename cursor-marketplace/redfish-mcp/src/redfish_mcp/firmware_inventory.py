"""Comprehensive firmware inventory from Redfish UpdateService."""

from __future__ import annotations

from typing import Any

from .redfish import (
    RedfishClient,
    RedfishEndpoint,
    batch_get_json,
    to_abs,
)


def collect_firmware_inventory(c: RedfishClient, ep: RedfishEndpoint) -> dict[str, Any]:
    """Collect all firmware versions from Redfish UpdateService/FirmwareInventory.

    Returns comprehensive firmware inventory including:
    - BIOS, BMC, storage controllers, NICs, PSUs, CPLDs, etc.
    - Version, updateable status, release date
    """
    result: dict[str, Any] = {
        "firmware_components": [],
        "component_count": 0,
        "sources": [],
        "errors": [],
        "by_category": {},
    }

    # Try UpdateService/FirmwareInventory (Redfish 1.0+)
    inventory_url = f"{c.base_url}/redfish/v1/UpdateService/FirmwareInventory"
    inv_coll, inv_err = c.get_json_maybe(inventory_url)
    result["sources"].append({"url": inventory_url, "ok": inv_err is None, "error": inv_err})

    if inv_err or not inv_coll:
        result["errors"].append(f"Could not access FirmwareInventory: {inv_err}")
        return result

    # Process each firmware component
    members = inv_coll.get("Members", [])
    comp_urls = [
        to_abs(c.base_url, m["@odata.id"])
        for m in members
        if isinstance(m, dict) and "@odata.id" in m
    ]

    fetched = batch_get_json(c, comp_urls)

    for comp_url, comp, comp_err in fetched:
        if comp_err or not comp:
            result["errors"].append(f"Failed to get {comp_url}: {comp_err}")
            continue

        component = {
            "id": comp.get("Id"),
            "name": comp.get("Name"),
            "description": comp.get("Description"),
            "version": comp.get("Version"),
            "updateable": comp.get("Updateable"),
            "status": comp.get("Status"),
            "release_date": comp.get("ReleaseDate"),
            "manufacturer": comp.get("Manufacturer"),
            "url": comp_url,
        }

        # Categorize component
        name_lower = (comp.get("Name") or "").lower()
        comp_id_lower = (comp.get("Id") or "").lower()

        category = "other"
        if any(x in name_lower or x in comp_id_lower for x in ["bios", "uefi"]):
            category = "bios"
        elif any(x in name_lower or x in comp_id_lower for x in ["bmc", "ipmi", "baseboard"]):
            category = "bmc"
        elif any(x in name_lower or x in comp_id_lower for x in ["nic", "ethernet", "network"]):
            category = "network"
        elif any(x in name_lower or x in comp_id_lower for x in ["storage", "raid", "hba", "sas"]):
            category = "storage"
        elif any(x in name_lower or x in comp_id_lower for x in ["psu", "power"]):
            category = "power"
        elif any(x in name_lower or x in comp_id_lower for x in ["cpld", "fpga"]):
            category = "programmable_logic"
        elif any(x in name_lower or x in comp_id_lower for x in ["pcie", "switch", "retimer"]):
            category = "pcie"
        elif any(x in name_lower or x in comp_id_lower for x in ["gpu", "accelerator"]):
            category = "gpu"

        component["category"] = category

        result["firmware_components"].append(component)

        # Add to category grouping
        if category not in result["by_category"]:
            result["by_category"][category] = []
        result["by_category"][category].append(component)

    result["component_count"] = len(result["firmware_components"])

    # Sort by category, then name
    result["firmware_components"].sort(key=lambda x: (x.get("category", ""), x.get("name", "")))

    return result


def get_vendor_errata_urls(manufacturer: str | None) -> dict[str, Any]:
    """Get vendor-specific errata and security bulletin URLs.

    Returns URLs for security advisories, bulletins, and errata pages.
    """
    result = {"vendor": manufacturer, "errata_urls": [], "security_bulletin_url": None, "notes": []}

    if not manufacturer:
        return result

    vendor_lower = manufacturer.lower()

    # Supermicro
    if "supermicro" in vendor_lower or "smc" in vendor_lower:
        result["security_bulletin_url"] = "https://www.supermicro.com/en/support/security_center"
        result["errata_urls"] = [
            {
                "type": "BMC Security Advisories",
                "url_pattern": "https://www.supermicro.com/en/support/security_BMC_IPMI_{Month}_{Year}",
                "description": "Monthly BMC/IPMI security bulletins",
                "examples": [
                    "https://www.supermicro.com/en/support/security_BMC_IPMI_Nov_2025",
                    "https://www.supermicro.com/en/support/security_BMC_IPMI_Oct_2025",
                ],
            },
            {
                "type": "General Security Center",
                "url": "https://www.supermicro.com/en/support/security_center",
                "description": "All Supermicro security advisories and CVEs",
            },
        ]
        result["notes"].append("Supermicro posts monthly BMC security bulletins")
        result["notes"].append("Check security_center for all CVEs affecting your motherboard")

    # Dell
    elif "dell" in vendor_lower:
        result["security_bulletin_url"] = "https://www.dell.com/support/security/en-us/security"
        result["errata_urls"] = [
            {
                "type": "Dell Security Advisories",
                "url": "https://www.dell.com/support/security/en-us/security",
                "description": "Dell DSA (Dell Security Advisory) bulletins",
                "format": "DSA-YYYY-NNN",
            },
            {
                "type": "Machine-Readable API",
                "url": "https://www.dell.com/support/security/en-us/security",
                "description": "Dell provides machine-readable security advisory API",
            },
        ]
        result["notes"].append("Dell uses DSA-YYYY-NNN format for security advisories")
        result["notes"].append("Machine-readable API available for automated checking")

    # HPE
    elif "hpe" in vendor_lower or "hewlett" in vendor_lower:
        result["security_bulletin_url"] = "https://support.hpe.com/connect/s/product?language=en_US"
        result["errata_urls"] = [
            {
                "type": "HPE Security Bulletins",
                "url": "https://support.hpe.com/connect/s/search?language=en_US#t=Security%20Bulletins",
                "description": "HPE security bulletins and advisories",
            },
            {
                "type": "Firmware Updates",
                "url": "https://support.hpe.com/connect/s/softwaredetails?language=en_US",
                "description": "HPE firmware and driver downloads",
            },
        ]
        result["notes"].append("HPE provides integrated support portal")

    # Lenovo
    elif "lenovo" in vendor_lower:
        result["security_bulletin_url"] = (
            "https://support.lenovo.com/ca/en/product_security/ps500001-lenovo-product-security-advisories"
        )
        result["errata_urls"] = [
            {
                "type": "Lenovo Security Advisories",
                "url": "https://support.lenovo.com/ca/en/product_security/ps500001-lenovo-product-security-advisories",
                "description": "Complete list of Lenovo security advisories with CVEs",
                "format": "LEN-NNNNNN",
            }
        ]
        result["notes"].append("Lenovo uses LEN-NNNNNN format for security advisories")

    # Giga Computing / Gigabyte (AMI MegaRAC BMC)
    elif "giga" in vendor_lower or "gigabyte" in vendor_lower:
        result["security_bulletin_url"] = (
            "https://www.gigabyte.com/Support/Security"
        )
        result["errata_urls"] = [
            {
                "type": "Gigabyte Security Updates",
                "url": "https://www.gigabyte.com/Support/Security",
                "description": "Gigabyte / Giga Computing security advisories",
            },
            {
                "type": "AMI MegaRAC BMC Advisories",
                "url": "https://www.ami.com/security-center/",
                "description": "AMI security advisories for MegaRAC BMC firmware (affects all AMI-based BMCs)",
            },
            {
                "type": "Enterprise Server Support",
                "url": "https://www.gigabyte.com/Enterprise/Server",
                "description": "Gigabyte enterprise server firmware and BIOS downloads",
            },
        ]
        result["notes"].append(
            "Giga Computing servers use AMI MegaRAC BMC — check both Gigabyte and AMI advisories"
        )
        result["notes"].append(
            "AMI security center covers CVEs affecting MegaRAC firmware across all vendors"
        )

    else:
        result["notes"].append(f"No errata URL mapping for vendor: {manufacturer}")
        result["notes"].append("Check vendor's support website manually")

    return result
