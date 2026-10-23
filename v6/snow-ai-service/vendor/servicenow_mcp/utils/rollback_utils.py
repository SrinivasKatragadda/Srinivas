"""Local rollback metadata store for auditable ServiceNow edits.

This module persists rollback metadata to a local JSON file so successful
mutations can be reversed later. It is intentionally simple and file-based so
rollback history remains available without relying on additional ServiceNow
records or external storage.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


def _store_path() -> Path:
    """Get the path to the rollback metadata store JSON file."""
    root = Path(os.getenv("SERVICENOW_MCP_ROLLBACK_DIR", ".servicenow_mcp_rollbacks"))
    return root / "atf_edit_rollbacks.json"


def load_rollbacks() -> List[Dict[str, Any]]:
    """
    Load all saved rollback actions from the local store.
    
    Returns:
        List of rollback action dicts, empty list if store doesn't exist.
    """
    path = _store_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data if isinstance(data, list) else []


def save_rollback(action: Dict[str, Any]) -> Dict[str, Any]:
    """
    Save a rollback action to the local store for future recovery.
    
    Creates the store directory if it doesn't exist.
    
    Args:
        action: Rollback metadata to save
        
    Returns:
        The saved action dict.
    """
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    records = load_rollbacks()
    records.append(action)
    path.write_text(json.dumps(records, indent=2, sort_keys=True), encoding="utf-8")
    return action


def get_rollback(rollback_id: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve a specific rollback action by its rollback_id.
    
    Args:
        rollback_id: The rollback ID to look up
        
    Returns:
        Rollback action dict if found, None otherwise.
    """
    return next((item for item in load_rollbacks() if item.get("rollback_id") == rollback_id), None)


def find_by_idempotency_key(idempotency_key: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Find the most recent rollback action for an idempotency key.
    
    Used to detect and recover from duplicate update attempts.
    
    Args:
        idempotency_key: The idempotency key to search for
        
    Returns:
        Most recent rollback with matching idempotency_key, None if not found.
    """
    if not idempotency_key:
        return None
    return next(
        (
            item
            for item in reversed(load_rollbacks())
            if item.get("idempotency_key") == idempotency_key
            and item.get("operation") == "update_atf_step_input"
        ),
        None,
    )
