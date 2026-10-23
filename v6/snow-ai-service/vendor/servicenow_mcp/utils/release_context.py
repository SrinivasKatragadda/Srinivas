"""
Release-context helpers for ServiceNow instance detection.

This module provides best-effort build and release detection with multiple
fallbacks so tools can adapt behavior safely across ServiceNow families. It is
used to infer instance family, module-specific behavior, and release-aware
tooling hints from the current environment.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import requests

from servicenow_mcp.utils.config import ServerConfig

if TYPE_CHECKING:
    from servicenow_mcp.auth.auth_manager import AuthManager

RELEASE_FAMILY_DISPLAY_NAMES = {
    "aspen": "Aspen",
    "berlin": "Berlin",
    "calgary": "Calgary",
    "dublin": "Dublin",
    "eureka": "Eureka",
    "fuji": "Fuji",
    "geneva": "Geneva",
    "helsinki": "Helsinki",
    "istanbul": "Istanbul",
    "jakarta": "Jakarta",
    "kingston": "Kingston",
    "london": "London",
    "madrid": "Madrid",
    "newyork": "New York",
    "orlando": "Orlando",
    "paris": "Paris",
    "quebec": "Quebec",
    "rome": "Rome",
    "sandiego": "San Diego",
    "tokyo": "Tokyo",
    "utah": "Utah",
    "vancouver": "Vancouver",
    "washingtondc": "Washington DC",
    "xanadu": "Xanadu",
    "yokohama": "Yokohama",
    "zurich": "Zurich",
    "australia": "Australia",
}

RELEASE_FAMILY_ORDER = list(RELEASE_FAMILY_DISPLAY_NAMES.keys())
RELEASE_FAMILY_ALIASES = {
    "new york": "newyork",
    "new_york": "newyork",
    "new-york": "newyork",
    "san diego": "sandiego",
    "san_diego": "sandiego",
    "san-diego": "sandiego",
    "washington dc": "washingtondc",
    "washington_dc": "washingtondc",
    "washington-dc": "washingtondc",
}
RELEASE_PROPERTY_NAMES = [
    "glide.buildname",
    "glide.war",
    "glide.product.family",
    "glide.builddate",
    "glide.buildtag",
    "glide.security.diag_txns_acl",
]
BUILD_CACHE_TTL_SECONDS = 300

_BUILD_CACHE: Dict[str, Dict[str, Any]] = {}


def _normalize_key(value: Optional[str]) -> Optional[str]:
    """Normalize string to lowercase alphanumeric for comparison."""
    if value is None:
        return None
    normalized = re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())
    return normalized or None


def _normalize_family(value: Optional[str]) -> Optional[str]:
    """Normalize release family name to the canonical release-family key."""
    if value is None:
        return None

    raw = str(value).strip().lower()
    if not raw:
        return None

    if raw in RELEASE_FAMILY_ALIASES:
        return RELEASE_FAMILY_ALIASES[raw]

    collapsed = _normalize_key(raw)
    if collapsed in RELEASE_FAMILY_DISPLAY_NAMES:
        return collapsed

    for alias, family_key in RELEASE_FAMILY_ALIASES.items():
        if _normalize_key(alias) == collapsed:
            return family_key

    return collapsed


def _family_display_name(family_key: Optional[str]) -> Optional[str]:
    if family_key is None:
        return None
    return RELEASE_FAMILY_DISPLAY_NAMES.get(
        family_key,
        str(family_key).replace("_", " ").replace("-", " ").title(),
    )


def _family_index(family_key: Optional[str]) -> Optional[int]:
    if family_key is None or family_key not in RELEASE_FAMILY_ORDER:
        return None
    return RELEASE_FAMILY_ORDER.index(family_key)


def family_at_least(family_key: Optional[str], minimum_family: str) -> Optional[bool]:
    """Check if detected release family meets minimum release requirement.

    Args:
        family_key: Detected release family (e.g., 'london', 'madrid')
        minimum_family: Minimum required family name

    Returns:
        True if detected >= minimum, False if detected < minimum, None if indeterminate
    """
    detected_index = _family_index(family_key)
    minimum_index = _family_index(_normalize_family(minimum_family))
    if detected_index is None or minimum_index is None:
        return None
    return detected_index >= minimum_index


def infer_module_name_from_tool(tool_name: str) -> str:
    """Infer ServiceNow module name from MCP tool name.

    Maps tool_name keywords to ServiceNow modules (incident, change, request, catalog, etc.)

    Args:
        tool_name: MCP tool name (e.g., 'incident_tools', 'create_change_request')

    Returns:
        Module name (incident, problem, change, request, catalog, sla, knowledge, etc.)
    """
    normalized_tool_name = _normalize_key(tool_name) or ""
    if "incident" in normalized_tool_name:
        return "incident"
    if "problem" in normalized_tool_name:
        return "problem"
    if "change" in normalized_tool_name:
        return "change"
    if "request" in normalized_tool_name:
        return "request"
    if "catalog" in normalized_tool_name or "clientscript" in normalized_tool_name:
        return "catalog"
    if "sla" in normalized_tool_name:
        return "sla"
    if "knowledge" in normalized_tool_name or "article" in normalized_tool_name:
        return "knowledge"
    if "businessrule" in normalized_tool_name:
        return "businessrule"
    if "uipolicy" in normalized_tool_name:
        return "uipolicy"
    if "uiaction" in normalized_tool_name:
        return "uiaction"
    if "datapolicy" in normalized_tool_name:
        return "datapolicy"
    if "updateset" in normalized_tool_name or "changeset" in normalized_tool_name:
        return "updateset"
    if "workflow" in normalized_tool_name or "flow" in normalized_tool_name:
        return "workflow"
    if "scriptinclude" in normalized_tool_name:
        return "scriptinclude"
    if "notification" in normalized_tool_name:
        return "notification"
    if "emailtemplate" in normalized_tool_name:
        return "emailtemplate"
    if "eventdefinition" in normalized_tool_name or "queueevent" in normalized_tool_name:
        return "eventdefinition"
    if "scriptaction" in normalized_tool_name:
        return "scriptaction"
    if "story" in normalized_tool_name:
        return "story"
    if "epic" in normalized_tool_name:
        return "epic"
    if "scrumtask" in normalized_tool_name:
        return "scrumtask"
    if "project" in normalized_tool_name:
        return "project"
    if "security" in normalized_tool_name or "grc" in normalized_tool_name:
        return "securityadmin"
    if "user" in normalized_tool_name:
        return "user"
    if "build" in normalized_tool_name or "release" in normalized_tool_name:
        return "release"
    return normalized_tool_name


def infer_servicenow_module(module_name: str) -> str:
    """Handle infer servicenow module.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    module_key = infer_module_name_from_tool(module_name)

    if module_key in {
        "incident",
        "problem",
        "change",
        "request",
        "catalog",
        "catalogclientscript",
        "clientscript",
        "sla",
    }:
        return "ITSM"
    if module_key in {"knowledgebase", "knowledge", "customer", "case"}:
        return "CSM"
    if module_key in {"securityadmin", "grc", "risk", "compliance", "security"}:
        return "GRC"
    if module_key in {"event", "cmdb", "discovery", "monitoring", "itom"}:
        return "ITOM"
    if module_key in {"story", "epic", "scrumtask", "project"}:
        return "SPM"
    return "Platform"


def _default_behavior_profile(module_key: str) -> Dict[str, Any]:
    return {
        "module_key": module_key,
        "resolution_strategy": [
            "detect_release_context",
            "discover_schema_when_possible",
            "prefer_instance_native_table_api",
            "fall_back_to_aliases",
        ],
        "feature_flags": {
            "prefer_schema_discovery": True,
            "prefer_display_value_normalization": True,
            "prefer_unversioned_table_api": True,
            "tolerate_reference_shape_differences": True,
        },
        "notes": [
            "Do not hard-code release assumptions when a table or field can be discovered "
            "dynamically.",
            "Normalize reference fields because ServiceNow responses can vary between raw values "
            "and display-value objects.",
        ],
    }


def get_module_release_behavior(module_name: str, build_info: Dict[str, Any]) -> Dict[str, Any]:
    """Get module release behavior.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    module_key = infer_module_name_from_tool(module_name)
    family_key = build_info.get("family_key")
    stats_admin_only = build_info.get("capabilities", {}).get("stats_endpoint_may_require_admin")

    profile = _default_behavior_profile(module_key)

    if module_key in {"build", "release", "diagnostics", "instance"}:
        profile["notes"].extend(
            [
                "Build detection should try stats.do/xmlstats.do first and then fall back to sys_"
                "properties.",
                "Cache release context for the process lifetime window to avoid repeating "
                "diagnostics calls on every tool invocation.",
            ]
        )
        profile["feature_flags"]["use_property_fallbacks"] = True
        if stats_admin_only is True:
            profile["notes"].append(
                "Treat stats.do as optional because this instance family or ACL configuration may "
                "restrict diagnostics access."
            )
    elif module_key in {"uipolicy", "uiaction", "datapolicy"}:
        profile["notes"].extend(
            [
                "Prefer sys_dictionary or live-record discovery when parent/reference field names "
                "vary.",
                "Repair related-list linkage by verifying the child record's reference field "
                "instead of assuming the create response is enough.",
            ]
        )
        profile["feature_flags"]["prefer_schema_discovery"] = True
        profile["feature_flags"]["verify_child_parent_linkage"] = True
    elif module_key in {"catalog", "catalogclientscript", "clientscript"}:
        profile["notes"].extend(
            [
                "Accept common aliases like type and script_type when the platform or callers use "
                "different naming.",
                "Normalize variable and catalog references before writing payloads.",
            ]
        )
        profile["feature_flags"]["accept_alias_inputs"] = True
    elif module_key in {"updateset", "changeset", "devops", "cicd"}:
        profile["notes"].extend(
            [
                "Resolve both local and remote update-set records so promotion flows work across "
                "source and target instances.",
                "Use build detection only for behavior hints; promotion readiness should still be "
                "validated from live records and preview problems.",
            ]
        )
        profile["feature_flags"]["resolve_remote_and_local_records"] = True
        profile["feature_flags"]["require_live_validation"] = True
    elif module_key in {"workflow", "flow", "scriptinclude"}:
        profile["notes"].append(
            "Prefer runtime schema discovery over release-specific field assumptions for metadata-"
            "heavy platform objects."
        )

    if family_at_least(family_key, "sandiego") is True:
        profile.setdefault("release_hints", []).append(
            "For San Diego and later families, diagnostic endpoints may be access-restricted; "
            "property-based build detection should remain enabled."
        )
    if family_at_least(family_key, "vancouver") is True:
        profile.setdefault("release_hints", []).append(
            "Modern families tend to return more mixed display/raw reference shapes, so "
            "normalization should stay enabled."
        )

    return profile


def _request_timeout(config: ServerConfig) -> int:
    return getattr(config, "timeout", 30)


def _base_headers(auth_manager: AuthManager) -> Dict[str, str]:
    headers = dict(auth_manager.get_headers())
    headers.setdefault("Accept", "application/json")
    return headers


def _extract_family_from_text(value: Optional[str]) -> Optional[str]:
    normalized_value = str(value or "").strip().lower()
    if not normalized_value:
        return None

    for pattern in (
        r"(?:glide-|mid buildstamp:\s*|build tag:\s*|build name:\s*)([a-z]+(?:dc)?)",
        r"\b([a-z]+(?:dc)?)\-\d{2}\-\d{2}\-\d{4}",
    ):
        match = re.search(pattern, normalized_value, re.IGNORECASE)
        if match:
            normalized_family = _normalize_family(match.group(1))
            if normalized_family in RELEASE_FAMILY_DISPLAY_NAMES:
                return normalized_family

    for candidate in RELEASE_FAMILY_ORDER:
        if re.search(rf"(?<![a-z0-9]){re.escape(candidate)}(?![a-z0-9])", normalized_value):
            return candidate

    normalized_family = _normalize_family(normalized_value)
    if normalized_family in RELEASE_FAMILY_DISPLAY_NAMES:
        return normalized_family
    return None


def _parse_stats_payload(payload: str) -> Dict[str, Any]:
    text = payload or ""
    lower_text = text.lower()

    build_name: Optional[str] = None
    build_tag: Optional[str] = None
    build_date: Optional[str] = None
    family_key: Optional[str] = None

    build_match = re.search(
        r"build(?:\s+name|\s+information)?\s*[:=]\s*([^\r\n<]+)",
        text,
        re.IGNORECASE,
    )
    if build_match:
        build_name = build_match.group(1).strip()

    date_match = re.search(
        r"build(?:\s+date)?\s*[:=]\s*([A-Za-z0-9:,_\-\/ ]+)",
        text,
        re.IGNORECASE,
    )
    if date_match:
        build_date = date_match.group(1).strip()

    tag_match = re.search(
        r"build\s+tag\s*[:=]\s*([^\r\n<]+)",
        text,
        re.IGNORECASE,
    )
    if tag_match:
        build_tag = tag_match.group(1).strip()

    mid_match = re.search(
        r"mid\s+buildstamp\s*[:=]\s*([^\r\n<]+)",
        text,
        re.IGNORECASE,
    )
    mid_buildstamp = mid_match.group(1).strip() if mid_match else None

    family_key = (
        _extract_family_from_text(build_tag)
        or _extract_family_from_text(mid_buildstamp)
        or _extract_family_from_text(build_name)
        or _extract_family_from_text(lower_text)
    )

    if build_tag is None:
        war_match = re.search(
            r"\b(?:glide-)?([a-z]+(?:dc)?)\-\d{2}\-\d{2}\-\d{4}[^\r\n<]*", lower_text
        )
        if war_match:
            build_tag = war_match.group(0)
            if family_key is None:
                family_key = _normalize_family(war_match.group(1))

    return {
        "build_name": build_name,
        "build_tag": build_tag,
        "build_date": build_date,
        "family_key": family_key,
    }


def _parse_properties(properties: Dict[str, str]) -> Dict[str, Any]:
    build_name = properties.get("glide.buildname")
    build_tag = properties.get("glide.buildtag")
    build_date = properties.get("glide.builddate")
    family_key = _normalize_family(properties.get("glide.product.family"))

    war_value = properties.get("glide.war")
    if war_value:
        family_key = family_key or _extract_family_from_text(war_value)
        if build_tag is None:
            build_tag = war_value

    if build_name is None and family_key is not None:
        build_name = _family_display_name(family_key)

    diag_acl_value = properties.get("glide.security.diag_txns_acl")

    return {
        "build_name": build_name,
        "build_tag": build_tag,
        "build_date": build_date,
        "family_key": family_key,
        "diag_txns_acl": diag_acl_value,
    }


def _get_stats_context(
    instance_url: str,
    auth_manager: AuthManager,
    config: ServerConfig,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    for endpoint in ("stats.do", "xmlstats.do"):
        try:
            response = requests.get(
                f"{instance_url.rstrip('/')}/{endpoint}",
                headers={**_base_headers(auth_manager), "Accept": "text/html, text/plain, */*"},
                timeout=_request_timeout(config),
            )
            response.raise_for_status()
            parsed = _parse_stats_payload(response.text)
            if any(
                parsed.get(key) for key in ("build_name", "family_key", "build_tag", "build_date")
            ):
                return (
                    {
                        **parsed,
                        "source": endpoint,
                        "raw_excerpt": response.text[:500],
                    },
                    None,
                )
            return None, f"{endpoint} returned a response but no build markers were detected"
        except requests.exceptions.RequestException as exc:
            return None, f"{endpoint} request failed: {str(exc)}"
    return None, "No stats endpoints returned usable build information"


def _get_property_context(
    instance_url: str,
    auth_manager: AuthManager,
    config: ServerConfig,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        response = requests.get(
            f"{instance_url.rstrip('/')}/api/now/table/sys_properties",
            headers=_base_headers(auth_manager),
            params={
                "sysparm_query": f"nameIN{','.join(RELEASE_PROPERTY_NAMES)}",
                "sysparm_fields": "name,value",
                "sysparm_limit": len(RELEASE_PROPERTY_NAMES),
                "sysparm_display_value": "false",
                "sysparm_exclude_reference_link": "true",
            },
            timeout=_request_timeout(config),
        )
        response.raise_for_status()
        result = response.json().get("result", [])
        property_map = {
            str(item.get("name")): str(item.get("value"))
            for item in result
            if isinstance(item, dict)
            and item.get("name") is not None
            and item.get("value") is not None
        }
        parsed = _parse_properties(property_map)
        if any(parsed.get(key) for key in ("build_name", "family_key", "build_tag", "build_date")):
            return (
                {
                    **parsed,
                    "source": "sys_properties",
                    "properties": property_map,
                },
                None,
            )
        return None, "sys_properties query succeeded but did not return recognizable build values"
    except requests.exceptions.RequestException as exc:
        return None, f"sys_properties request failed: {str(exc)}"


def _build_capabilities(detected: Dict[str, Any]) -> Dict[str, Any]:
    family_key = detected.get("family_key")
    diag_acl_value = detected.get("diag_txns_acl")
    stats_admin_only = None
    if diag_acl_value is not None:
        stats_admin_only = str(diag_acl_value).strip().lower() == "true"
    elif family_at_least(family_key, "sandiego") is True:
        stats_admin_only = True

    return {
        "release_aware_behavior_enabled": True,
        "family_known": family_key is not None,
        "stats_endpoint_may_require_admin": stats_admin_only,
        "prefer_schema_discovery": True,
        "prefer_unversioned_table_api": True,
        "prefer_fallback_detection": detected.get("source") != "stats.do",
    }


def get_instance_release_context(
    config: ServerConfig,
    auth_manager: AuthManager,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """Get instance release context.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    cache_key = config.instance_url.rstrip("/")
    cache_entry = _BUILD_CACHE.get(cache_key)
    if (
        not force_refresh
        and cache_entry
        and time.time() - cache_entry.get("cached_at", 0) < BUILD_CACHE_TTL_SECONDS
    ):
        cached_context = dict(cache_entry["context"])
        cached_context["cache_hit"] = True
        return cached_context

    attempts: List[Dict[str, Any]] = []
    detected: Optional[Dict[str, Any]] = None

    stats_context, stats_error = _get_stats_context(config.instance_url, auth_manager, config)
    attempts.append(
        {
            "source": "stats.do/xmlstats.do",
            "success": stats_context is not None,
            "details": None if stats_context is not None else stats_error,
        }
    )
    if stats_context is not None:
        detected = stats_context

    if detected is None:
        property_context, property_error = _get_property_context(
            config.instance_url, auth_manager, config
        )
        attempts.append(
            {
                "source": "sys_properties",
                "success": property_context is not None,
                "details": None if property_context is not None else property_error,
            }
        )
        if property_context is not None:
            detected = property_context

    if detected is None:
        context = {
            "success": False,
            "instance_url": config.instance_url,
            "build_name": None,
            "family": None,
            "family_key": None,
            "family_order_index": None,
            "build_tag": None,
            "build_date": None,
            "detection_source": None,
            "attempts": attempts,
            "capabilities": _build_capabilities({}),
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "cache_hit": False,
            "message": (
                "Unable to detect the ServiceNow build name from stats endpoints or sys_properties."
            ),
        }
        _BUILD_CACHE[cache_key] = {"cached_at": time.time(), "context": context}
        return context

    family_key = _normalize_family(detected.get("family_key") or detected.get("build_name"))
    context = {
        "success": True,
        "instance_url": config.instance_url,
        "build_name": detected.get("build_name") or _family_display_name(family_key),
        "family": _family_display_name(family_key),
        "family_key": family_key,
        "family_order_index": _family_index(family_key),
        "build_tag": detected.get("build_tag"),
        "build_date": detected.get("build_date"),
        "detection_source": detected.get("source"),
        "attempts": attempts,
        "capabilities": _build_capabilities({**detected, "family_key": family_key}),
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "cache_hit": False,
        "message": "Detected ServiceNow build information successfully.",
    }
    if "properties" in detected:
        context["properties"] = detected["properties"]

    _BUILD_CACHE[cache_key] = {"cached_at": time.time(), "context": context}
    return context
