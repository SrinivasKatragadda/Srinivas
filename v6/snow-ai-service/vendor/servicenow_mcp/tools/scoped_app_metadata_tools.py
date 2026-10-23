"""Scoped application lifecycle metadata tools.

This module exposes the supported lifecycle artifact profiles for scoped
applications. It is a lightweight metadata layer that helps callers discover
what artifact families are available without performing full record queries.
"""

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.tools.scoped_app_common import ARTIFACT_PROFILES, ArtifactType
from servicenow_mcp.utils.config import ServerConfig


class GetScopedAppLifecycleMetadataParams(BaseModel):
    """Parameters for scoped app lifecycle metadata."""

    artifact_type: Optional[ArtifactType] = Field(
        None, description="Specific artifact type to describe, or empty for all types"
    )


def get_scoped_app_lifecycle_metadata(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetScopedAppLifecycleMetadataParams,
) -> Dict[str, Any]:
    """Describe supported scoped app lifecycle artifact types and tables.

    The function returns either a single profile or the full lifecycle profile
    map. It does not need the config or auth objects today, but they are kept in
    the signature so the API matches the rest of the scoped-app tool surface.
    """
    del config, auth_manager
    if params.artifact_type:
        return {
            "success": True,
            "artifact_type": params.artifact_type,
            "profile": ARTIFACT_PROFILES[params.artifact_type],
        }
    return {
        "success": True,
        "artifact_types": ARTIFACT_PROFILES,
        "available_artifact_types": sorted(ARTIFACT_PROFILES),
    }
