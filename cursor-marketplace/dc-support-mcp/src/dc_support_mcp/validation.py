"""Input validation utilities."""

from .constants import RTB_OUTAGE_TYPES


class ValidationError(ValueError):
    """Custom exception for validation errors."""

    pass


def validate_gpu_outage_type(value: str) -> str:
    """Validate that *value* is a recognised RTB outage type.

    Comparison is case-insensitive; the canonical label is returned on
    match so callers always send the exact string the API expects.

    Raises ``ValidationError`` with the list of valid types on mismatch.
    """
    lookup = {t.lower(): t for t in RTB_OUTAGE_TYPES}
    canonical = lookup.get(value.lower())
    if canonical is not None:
        return canonical
    valid = ", ".join(RTB_OUTAGE_TYPES)
    raise ValidationError(
        f"Invalid gpu_outage_type '{value}'. "
        f"Valid types: {valid}. "
        f"Use --list-outage-types to see all options."
    )
