"""Output schemas for structured tool responses."""

from typing import Any

DEVICE_LOOKUP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "count": {"type": "integer", "description": "Number of matching devices"},
        "results": {
            "type": "array",
            "items": {"type": "object"},
            "description": "Device objects with enriched IP fields",
        },
        "query": {"type": "string", "description": "Original hostname query"},
    },
    "required": ["count", "results", "query"],
}

PAGINATED_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "count": {"type": "integer", "description": "Total matching objects"},
        "next": {
            "type": ["string", "null"],
            "description": "URL for next page, or null",
        },
        "previous": {
            "type": ["string", "null"],
            "description": "URL for previous page, or null",
        },
        "results": {
            "type": "array",
            "items": {"type": "object"},
            "description": "Objects for this page",
        },
    },
    "required": ["count", "results"],
}

DEVICE_UPDATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "device": {
            "type": "object",
            "description": "The updated device record",
        },
        "changes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of field changes applied (old → new)",
        },
    },
    "required": ["device", "changes"],
}

SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Dictionary keyed by object type, each value a list of matching objects",
    "additionalProperties": {
        "type": "array",
        "items": {"type": "object"},
    },
}
