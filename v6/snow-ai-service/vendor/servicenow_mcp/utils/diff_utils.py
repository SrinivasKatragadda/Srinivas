"""Small diff helpers for ServiceNow edit tools.

This module builds compact before/after field change structures for edit and
rollback workflows. The helpers standardize diff payloads so preview, validate,
and rollback responses all describe changes in the same shape.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def build_field_diff(
    table: str,
    sys_id: str,
    field: str,
    previous_value: Any,
    new_value: Any,
    previous_display_value: Optional[str] = None,
    new_display_value: Optional[str] = None,
    value_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build a field diff representing a change from previous to new value.
    
    Args:
        table: Table name containing the field
        sys_id: Record sys_id
        field: Field name
        previous_value: Previous (raw/sys_id) value
        new_value: New (raw/sys_id) value
        previous_display_value: Previous display value (for references)
        new_display_value: New display value (for references)
        value_path: Optional path for nested values
        
    Returns:
        Dict with before/after values and metadata.
    """
    diff = {
        "table": table,
        "sys_id": sys_id,
        "field": field,
        "from": previous_value,
        "to": new_value,
        "from_display_value": previous_display_value,
        "to_display_value": new_display_value,
    }
    if value_path:
        diff["value_path"] = value_path
    return diff


def comparable_key(value: str) -> str:
    """
    Convert a string to a normalized comparable key (lowercase, alphanumeric only).
    
    Useful for case-insensitive matching of input names, labels, etc.
    
    Args:
        value: String to normalize
        
    Returns:
        Normalized key with only alphanumeric characters, lowercase.
    """
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())
