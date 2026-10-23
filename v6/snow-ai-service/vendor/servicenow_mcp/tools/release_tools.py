"""
Release-aware tools for ServiceNow MCP.

These tools expose build detection and release-aware behavior hints so the MCP
can adapt to ServiceNow instance family differences more safely. The module is
used for build/version discovery and for translating instance release context
into module-specific behavior guidance.
"""

import os
from typing import Any, Dict, Optional

import yaml
from pydantic import BaseModel, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.constants import MCP_TOOL_PACKAGE
from servicenow_mcp.utils.config import ServerConfig
from servicenow_mcp.utils.release_context import (
    get_instance_release_context,
    get_module_release_behavior,
    infer_module_name_from_tool,
    infer_servicenow_module,
)


class GetInstanceBuildInfoParams(BaseModel):
    """Parameters for retrieving ServiceNow build information."""

    force_refresh: bool = Field(False, description="Ignore the local cache and fetch again")


class GetReleaseBehaviorParams(BaseModel):
    """Parameters for release-aware module behavior hints."""

    module_name: str = Field(..., description="Module, feature, or tool family name")
    force_refresh: bool = Field(False, description="Ignore cached build information")


class GetEnabledModuleReleaseBehaviorsParams(BaseModel):
    """Parameters for retrieving release-aware details for all enabled modules."""

    package_name: Optional[str] = Field(
        None,
        description=(
            "Tool package name to inspect; defaults to the currently configured MCP package"
        ),
    )
    force_refresh: bool = Field(False, description="Ignore cached build information")
    include_tools: bool = Field(True, description="Include enabled tool names for each module")


def _tool_package_config_path() -> str:
    config_path = os.getenv("TOOL_PACKAGE_CONFIG_PATH", "config/tool_packages.yaml")
    if not os.path.isabs(config_path):
        config_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", config_path)
        )
    return config_path


def _load_package_definitions() -> Dict[str, Any]:
    config_path = _tool_package_config_path()
    with open(config_path, "r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"Invalid tool package config format in {config_path}")
    return loaded


def _resolve_package_name(
    package_definitions: Dict[str, Any], requested_package: Optional[str]
) -> str:
    package_name = (
        requested_package or os.getenv("MCP_TOOL_PACKAGE", MCP_TOOL_PACKAGE) or ""
    ).strip()
    if not package_name:
        package_name = "full"
    if package_name not in package_definitions:
        raise ValueError(
            f"Unknown tool package '{package_name}'. Valid packages: "
            f"{sorted(package_definitions.keys())}"
        )
    return package_name


def get_instance_build_info(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetInstanceBuildInfoParams,
) -> Dict[str, Any]:
    """Retrieve build and release context for the connected ServiceNow instance."""
    return get_instance_release_context(config, auth_manager, force_refresh=params.force_refresh)


def get_release_behavior(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetReleaseBehaviorParams,
) -> Dict[str, Any]:
    """Return release-aware behavior hints for a module or feature area."""
    build_info = get_instance_release_context(
        config,
        auth_manager,
        force_refresh=params.force_refresh,
    )
    module_key = infer_module_name_from_tool(params.module_name)
    profile = get_module_release_behavior(module_key, build_info)

    return {
        "success": build_info.get("success", False),
        "message": (
            f"Resolved release-aware behavior profile for {params.module_name}"
            if build_info.get("success")
            else (
                f"Resolved fallback behavior profile for {params.module_name}; "
                "build detection is incomplete"
            )
        ),
        "module_name": params.module_name,
        "module_key": module_key,
        "servicenow_module": infer_servicenow_module(module_key),
        "build_info": build_info,
        "behavior_profile": profile,
    }


def get_enabled_module_release_behaviors(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetEnabledModuleReleaseBehaviorsParams,
) -> Dict[str, Any]:
    """Return release-aware details for every module represented by the enabled tools."""
    build_info = get_instance_release_context(
        config,
        auth_manager,
        force_refresh=params.force_refresh,
    )

    package_definitions = _load_package_definitions()
    package_name = _resolve_package_name(package_definitions, params.package_name)
    enabled_tools = package_definitions.get(package_name, []) or []

    modules: Dict[str, Dict[str, Any]] = {}
    for tool_name in enabled_tools:
        module_key = infer_module_name_from_tool(tool_name)
        servicenow_module = infer_servicenow_module(module_key)
        if servicenow_module not in modules:
            modules[servicenow_module] = {
                "servicenow_module": servicenow_module,
                "tool_count": 0,
                "technical_modules": [],
            }
            if params.include_tools:
                modules[servicenow_module]["tools"] = []

        modules[servicenow_module]["tool_count"] += 1
        if params.include_tools:
            modules[servicenow_module]["tools"].append(tool_name)
        technical_modules = modules[servicenow_module]["technical_modules"]
        if not any(item["module_key"] == module_key for item in technical_modules):
            technical_modules.append(
                {
                    "module_key": module_key,
                    "behavior_profile": get_module_release_behavior(module_key, build_info),
                }
            )

    module_details = [modules[key] for key in sorted(modules.keys())]

    return {
        "success": build_info.get("success", False),
        "message": (
            f"Resolved release-aware details for {len(module_details)} ServiceNow modules in "
            f"package '{package_name}'"
            if build_info.get("success")
            else (
                f"Resolved fallback release-aware details for {len(module_details)} ServiceNow "
                f"modules "
                f"in package '{package_name}'; build detection is incomplete"
            )
        ),
        "package_name": package_name,
        "enabled_tool_count": len(enabled_tools),
        "enabled_module_count": len(module_details),
        "build_info": build_info,
        "modules": module_details,
    }
