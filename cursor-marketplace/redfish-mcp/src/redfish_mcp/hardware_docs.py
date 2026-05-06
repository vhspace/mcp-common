"""Hardware documentation and firmware update information with caching."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .firmware_checker import compare_versions

# Hardware database - loaded from JSON files
_HARDWARE_DATABASE: dict[str, dict[str, Any]] | None = None


def load_hardware_database() -> dict[str, dict[str, Any]]:
    """Load hardware database from JSON files."""
    global _HARDWARE_DATABASE

    if _HARDWARE_DATABASE is not None:
        return _HARDWARE_DATABASE

    database: dict[str, dict[str, Any]] = {}

    # Find hardware_db directory (relative to this file or in project root)
    module_dir = Path(__file__).parent
    possible_db_dirs = [
        module_dir.parent.parent / "hardware_db",  # Project root
        module_dir / "hardware_db",  # Next to module
        Path.cwd() / "hardware_db",  # Current directory
    ]

    db_dir = None
    for d in possible_db_dirs:
        if d.exists() and d.is_dir():
            db_dir = d
            break

    if not db_dir:
        # Return empty database if not found
        _HARDWARE_DATABASE = {}
        return _HARDWARE_DATABASE

    # Load all JSON files from vendor directories
    for vendor_dir in db_dir.iterdir():
        if not vendor_dir.is_dir() or vendor_dir.name.startswith("."):
            continue

        for json_file in vendor_dir.glob("*.json"):
            try:
                with json_file.open("r") as f:
                    data = json.load(f)

                # Extract hardware info
                hardware = data.get("hardware", {})
                model = hardware.get("model")

                # Store with model as key
                if model:
                    database[model] = data

                # Also store generic fallback with vendor name
                if json_file.name == "_generic.json":
                    vendor = hardware.get("vendor")
                    if vendor:
                        database[vendor] = data

            except Exception:
                # Skip malformed files
                continue

    _HARDWARE_DATABASE = database
    return database


def get_hardware_database() -> dict[str, dict[str, Any]]:
    """Get the hardware database (lazy-loaded)."""
    return load_hardware_database()


@dataclass
class DocsCacheEntry:
    """Cached documentation entry."""

    key: str
    data: dict[str, Any]
    timestamp: float
    ttl_seconds: int = 3600  # 1 hour default

    def is_expired(self) -> bool:
        return time.time() - self.timestamp > self.ttl_seconds

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class HardwareDocsCache:
    """In-memory and file-based cache for hardware documentation."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir or Path.home() / ".cache" / "redfish-mcp" / "docs"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._memory_cache: dict[str, DocsCacheEntry] = {}

    def _cache_key(self, model: str, doc_type: str) -> str:
        """Generate cache key."""
        return hashlib.sha256(f"{model}:{doc_type}".encode()).hexdigest()[:16]

    def _file_path(self, cache_key: str) -> Path:
        """Get cache file path."""
        return self.cache_dir / f"{cache_key}.json"

    def get(self, model: str, doc_type: str) -> dict[str, Any] | None:
        """Get from cache if not expired."""
        cache_key = self._cache_key(model, doc_type)

        # Check memory cache first
        if cache_key in self._memory_cache:
            entry = self._memory_cache[cache_key]
            if not entry.is_expired():
                return entry.data
            # Expired, remove from memory
            del self._memory_cache[cache_key]

        # Check file cache
        file_path = self._file_path(cache_key)
        if file_path.exists():
            try:
                with file_path.open("r") as f:
                    data = json.load(f)
                entry = DocsCacheEntry(**data)
                if not entry.is_expired():
                    # Load into memory cache
                    self._memory_cache[cache_key] = entry
                    return entry.data
                # Expired, delete file
                file_path.unlink()
            except Exception:
                pass

        return None

    def set(self, model: str, doc_type: str, data: dict[str, Any], ttl_seconds: int = 3600) -> None:
        """Store in cache."""
        cache_key = self._cache_key(model, doc_type)
        entry = DocsCacheEntry(
            key=cache_key, data=data, timestamp=time.time(), ttl_seconds=ttl_seconds
        )

        # Store in memory
        self._memory_cache[cache_key] = entry

        # Store in file
        try:
            file_path = self._file_path(cache_key)
            with file_path.open("w") as f:
                json.dump(entry.to_dict(), f, indent=2)
        except Exception:
            pass  # Best effort file caching


_NON_INFORMATIVE_MODELS = frozenset({"NA", "N/A", "Unknown", "None", "Default string", ""})


def match_hardware(manufacturer: str | None, model: str | None) -> dict[str, Any] | None:
    """Match hardware to database entry."""
    database = get_hardware_database()

    if not model:
        return None

    # Try exact model match first
    if model in database:
        return database[model]

    # Try partial model match
    for _db_model, db_data in database.items():
        db_hw_model = db_data.get("hardware", {}).get("model", "")
        if db_hw_model and (
            db_hw_model.lower() in model.lower() or model.lower() in db_hw_model.lower()
        ):
            return db_data

    # Match via redfish_identification.system_model — handles cases where the
    # Redfish model string (e.g. "G893-SD1-AAX5-000") differs from the
    # hardware_db key (e.g. "B200-180GB-SXM").
    for _db_model, db_data in database.items():
        rid = db_data.get("redfish_identification", {})
        system_model = rid.get("system_model")
        if not system_model:
            continue
        if isinstance(system_model, str) and system_model == model:
            return db_data
        if isinstance(system_model, list) and model in system_model:
            return db_data

    # When the Redfish model is non-informative (e.g. "NA" on NVIDIA HGX BMCs),
    # try matching by manufacturer against redfish_identification entries.
    if model in _NON_INFORMATIVE_MODELS and manufacturer:
        for _db_model, db_data in database.items():
            rid = db_data.get("redfish_identification", {})
            if rid.get("system_manufacturer", "").upper() == manufacturer.upper():
                accepted = rid.get("system_model", [])
                if isinstance(accepted, list) and model in accepted:
                    return db_data

    # Try vendor match as fallback
    if manufacturer and manufacturer in database:
        return database[manufacturer]

    return None


def get_bios_info(hardware_data: dict[str, Any], current_bios: str | None) -> dict[str, Any]:
    """Get BIOS version information and update status."""
    result = {
        "current_version": current_bios,
        "known_versions": [],
        "is_latest": None,
        "recommended_version": None,
        "changelog": [],
    }

    # Support both old (known_bios_versions) and new (bios_versions) keys
    known_versions = hardware_data.get("bios_versions") or hardware_data.get(
        "known_bios_versions", {}
    )
    if not known_versions or not current_bios:
        return result

    # Extract version from current BIOS string (e.g., "BIOS Date: 09/20/2025 Ver 3.7a")
    current_ver = None
    if "Ver " in current_bios:
        current_ver = current_bios.split("Ver ")[-1].strip()
    elif current_bios in known_versions:
        current_ver = current_bios

    result["known_versions"] = list(known_versions.keys())

    if current_ver and current_ver in known_versions:
        version_data = known_versions[current_ver]
        result["is_latest"] = version_data.get("status") == "latest"
        result["changelog"] = version_data.get("changes", [])
        result["known_issues"] = version_data.get("known_issues", [])
        result["recommended_settings"] = version_data.get("recommended_settings", {})

    # Find latest version
    for ver, data in known_versions.items():
        if data.get("status") == "latest":
            result["recommended_version"] = ver
            result["latest_changelog"] = data.get("changes", [])

    return result


def get_hardware_docs(
    manufacturer: str | None,
    model: str | None,
    bios_version: str | None = None,
    serial_number: str | None = None,
    cache: HardwareDocsCache | None = None,
) -> dict[str, Any]:
    """Get comprehensive hardware documentation."""
    result = {
        "ok": True,
        "manufacturer": manufacturer,
        "model": model,
        "matched": False,
        "documentation": {},
        "bios_info": {},
        "gpu_optimization": {},
        "cache_hit": False,
    }

    # Check cache first
    if cache and model:
        cached = cache.get(model, "hardware_docs")
        if cached:
            result.update(cached)
            result["cache_hit"] = True
            return result

    # Try to match hardware
    hardware_data = match_hardware(manufacturer, model)
    if not hardware_data:
        result["ok"] = False
        result["error"] = (
            f"No documentation found for {manufacturer} {model}. Hardware not in database."
        )
        result["note"] = (
            "This hardware can be added to the database. File an issue or PR with hardware details."
        )
        return result

    result["matched"] = True

    # Extract hardware info from nested structure
    hw_info = hardware_data.get("hardware", {})
    result["hardware_info"] = {
        "vendor": hw_info.get("vendor"),
        "family": hw_info.get("family"),
        "model": hw_info.get("model"),
        "description": hw_info.get("description"),
        "socket": hw_info.get("socket"),
        "gpu_slots": hw_info.get("gpu_slots"),
        "max_memory": hw_info.get("max_memory"),
        "form_factor": hw_info.get("form_factor"),
        "pcie_lanes": hw_info.get("pcie_lanes"),
    }

    # Support both old (documentation_urls) and new (documentation) keys
    result["documentation"] = hardware_data.get("documentation") or hardware_data.get(
        "documentation_urls", {}
    )

    # BIOS information
    if bios_version:
        result["bios_info"] = get_bios_info(hardware_data, bios_version)

    # GPU optimization info
    result["gpu_optimization"] = hardware_data.get("gpu_optimization", {})

    # Cache the result
    if cache and model:
        cache_data = dict(result)
        cache_data.pop("cache_hit", None)
        cache.set(model, "hardware_docs", cache_data, ttl_seconds=86400)  # 24 hour cache

    return result


def get_firmware_update_info(
    manufacturer: str | None,
    model: str | None,
    current_bios: str | None,
    online_check_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Check for firmware updates.

    Args:
        manufacturer: Hardware manufacturer
        model: Hardware model
        current_bios: Current BIOS version string
        online_check_result: Optional result from online BIOS check (from firmware_checker)
    """
    result = {
        "updates_available": False,
        "check_url": None,
        "recommendations": [],
        "source": "database",
    }

    hardware_data = match_hardware(manufacturer, model)
    if not hardware_data:
        result["note"] = "Hardware not in database. Check vendor website manually."
        return result

    # Get documentation URLs (support both old and new keys)
    docs = hardware_data.get("documentation") or hardware_data.get("documentation_urls", {})
    if "firmware" in docs:
        result["check_url"] = docs["firmware"]

    # If we have online check results, use those
    if (
        online_check_result
        and online_check_result.get("ok")
        and online_check_result.get("latest_version")
    ):
        result["source"] = "online"
        latest_online = online_check_result["latest_version"]
        result["latest_version"] = latest_online
        result["online_check_url"] = online_check_result.get("download_url")

        # Extract current version
        current_ver = None
        if current_bios and "Ver " in current_bios:
            current_ver = current_bios.split("Ver ")[-1].strip()

        if current_ver:
            comparison = compare_versions(current_ver, latest_online)
            if comparison == "older":
                result["updates_available"] = True
                result["recommendations"].append(
                    f"⚠️ BIOS update available: {current_ver} → {latest_online}"
                )
            elif comparison == "same":
                result["recommendations"].append(f"✅ BIOS is up to date ({latest_online})")
            elif comparison == "newer":
                result["recommendations"].append(
                    f"INFO: You have a newer BIOS than online ({current_ver} > {latest_online})"
                )
            else:
                result["recommendations"].append(
                    f"INFO: Current: {current_ver}, Online: {latest_online} (comparison unclear)"
                )
        else:
            result["recommendations"].append(f"Latest online version: {latest_online}")

        if online_check_result.get("release_notes_url"):
            result["release_notes_url"] = online_check_result["release_notes_url"]

        return result

    # Fallback to database check
    if current_bios:
        bios_info = get_bios_info(hardware_data, current_bios)
        if bios_info.get("is_latest") is True:
            result["recommendations"].append("✅ BIOS is up to date (per database)")
        elif bios_info.get("is_latest") is False:
            result["updates_available"] = True
            latest = bios_info.get("recommended_version")
            if latest:
                result["recommendations"].append(
                    f"⚠️ BIOS update available: {latest} (per database)"
                )
                result["latest_version"] = latest

    if docs.get("firmware"):
        result["recommendations"].append(f"Check {docs['firmware']} for latest firmware")

    return result
