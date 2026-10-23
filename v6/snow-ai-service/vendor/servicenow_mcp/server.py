"""ServiceNow MCP (Model Context Protocol) Server implementation.

This module provides the main server entry point implementing the Model Context Protocol (MCP),
enabling Large Language Models (LLMs) and AI agents to interact with ServiceNow data and APIs.

Architecture Overview:
1. ServiceNowMCP class: Main server implementing MCP handlers (list_tools, call_tool)
2. Authentication: Multi-strategy authentication via AuthManager (Basic/OAuth/API Key)
3. Tool Registration: Dynamic tool loading from tool_packages.yaml config
4. Release Context: Caching instance metadata (build, version) for version-aware behavior
5. Output Handling: Automatic JSON compaction and artifact storage for large results

Key Components:
- Tool Discovery: Loads ServiceNow tables, endpoints, and operations from instance
- MCP Handlers: Implements list_tools (discovery) and call_tool (execution)
- Tool Routing: Routes CMDB mutations through IRE (Identify, Reconcile, Enrich) API only
- Governance: Enforces CSDM rules, stable identifiers, and relationship constraints
- Compaction: Reduces oversized tool output with preview + artifact fallback

Configuration:
- TOOL_PACKAGE_CONFIG_PATH: YAML file specifying enabled tool packages
- MCP_TOOL_PACKAGE: Environment variable for package filtering
- SERVICENOW_MCP_OUTPUT_DIR: Directory for tool output artifacts (large JSON results)
- SERVICENOW_MCP_MAX_INLINE_OUTPUT_BYTES: Threshold for result compaction (default 20KB)
- SERVICENOW_MCP_PRETTY_OUTPUT: Format tool output with indentation for readability

Tool Output Management:
- Inline Output: Compact JSON sent directly in MCP response (limited size)
- Artifacts: Large results stored as timestamped JSON files with SHA256 hash
- Compaction: String/list/dict preview with truncation limits and key selection
- Audit Trail: Each artifact logs tool name, timestamp, payload hash, and full result

Dependencies:
- mcp.server: Low-level MCP server for handler registration
- AuthManager: Multi-strategy authentication management
- ServerConfig: Configuration structure for instance URL and auth
- CMDB Tools: Internal governance for CI ingestion (cmdb_ire_tools)
"""

import json
import logging
import os
import re
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, List, Union

import mcp.types as types
import yaml
from mcp.server.lowlevel import Server
from pydantic import ValidationError

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.constants import MCP_TOOL_PACKAGE
from servicenow_mcp.internal_tools.cmdb_ire_tools import (
    TABLE_API_BLOCK_MESSAGE,
    has_explicit_cmdb_mutation_target,
    is_cmdb_table_api_mutation,
    route_cmdb_ci_request,
)
from servicenow_mcp.tools.knowledge_base import (
    create_category as create_kb_category_tool,
)
from servicenow_mcp.tools.knowledge_base import (
    list_categories as list_kb_categories_tool,
)
from servicenow_mcp.utils.config import ServerConfig
from servicenow_mcp.utils.release_context import infer_module_name_from_tool
from servicenow_mcp.utils.tool_utils import get_tool_definitions

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Define path for the configuration file
TOOL_PACKAGE_CONFIG_PATH = os.getenv("TOOL_PACKAGE_CONFIG_PATH", "config/tool_packages.yaml")
DEFAULT_MAX_INLINE_TOOL_OUTPUT_BYTES = 20_000
TOOL_OUTPUT_ARTIFACT_DIR_ENV = "SERVICENOW_MCP_OUTPUT_DIR"
PRETTY_TOOL_OUTPUT_ENV = "SERVICENOW_MCP_PRETTY_OUTPUT"
MAX_COMPACT_STRING_CHARS = 500
MAX_COMPACT_LIST_ITEMS = 8
MAX_COMPACT_DICT_KEYS = 24
COMPACT_PREFERRED_KEYS = (
    "success",
    "ok",
    "status",
    "message",
    "msg",
    "error",
    "errors",
    "warnings",
    "valid",
    "error_count",
    "warning_count",
    "responseMode",
    "fullRequestOmitted",
    "deploymentResult",
    "validation",
    "result",
    "summary",
    "request",
    "actionCatalog",
    "flow",
    "flowName",
    "flowSysId",
    "subflowName",
    "subflowSysId",
    "sys_id",
    "sysId",
    "verified",
    "flowDesignerUrl",
)


def _compact_json_dumps(value: Any) -> str:
    """Serialize JSON without whitespace for size checks and stable artifact hashes."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _tool_output_json_kwargs() -> Dict[str, Any]:
    if os.getenv(PRETTY_TOOL_OUTPUT_ENV, "").lower() in {"1", "true", "yes", "on"}:
        return {"indent": 2, "ensure_ascii": False}
    return {"separators": (",", ":"), "ensure_ascii": False}


def _max_inline_tool_output_bytes() -> int:
    configured = os.getenv("SERVICENOW_MCP_MAX_INLINE_OUTPUT_BYTES")
    if not configured:
        return DEFAULT_MAX_INLINE_TOOL_OUTPUT_BYTES
    try:
        return max(1_000, int(configured))
    except ValueError:
        logger.warning(
            "Invalid SERVICENOW_MCP_MAX_INLINE_OUTPUT_BYTES=%r; using default %s",
            configured,
            DEFAULT_MAX_INLINE_TOOL_OUTPUT_BYTES,
        )
        return DEFAULT_MAX_INLINE_TOOL_OUTPUT_BYTES


def _tool_output_artifact_dir() -> Path:
    configured = os.getenv(TOOL_OUTPUT_ARTIFACT_DIR_ENV)
    return Path(configured) if configured else Path(".servicenow_mcp_tool_outputs")


def _safe_tool_name(tool_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", tool_name).strip("._") or "tool"


def _string_preview(value: str, limit: int = MAX_COMPACT_STRING_CHARS) -> Any:
    if len(value) <= limit:
        return value
    digest = sha256(value.encode("utf-8")).hexdigest()
    return {
        "preview": value[:limit],
        "omitted_chars": len(value) - limit,
        "chars": len(value),
        "sha256": digest,
    }


def _compact_value_preview(value: Any, depth: int = 0) -> Any:
    """Return a bounded preview. The full value is preserved in the artifact."""
    if isinstance(value, str):
        return _string_preview(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, list):
        preview = [
            _compact_value_preview(item, depth + 1) for item in value[:MAX_COMPACT_LIST_ITEMS]
        ]
        compact: Dict[str, Any] = {
            "type": "list",
            "count": len(value),
            "items": preview,
        }
        if len(value) > MAX_COMPACT_LIST_ITEMS:
            compact["omitted_count"] = len(value) - MAX_COMPACT_LIST_ITEMS
        return compact
    if isinstance(value, dict):
        keys = list(value.keys())
        if depth >= 3:
            return {
                "type": "dict",
                "key_count": len(keys),
                "keys": [str(key) for key in keys[:MAX_COMPACT_DICT_KEYS]],
                **(
                    {"omitted_key_count": len(keys) - MAX_COMPACT_DICT_KEYS}
                    if len(keys) > MAX_COMPACT_DICT_KEYS
                    else {}
                ),
            }

        preferred = [key for key in COMPACT_PREFERRED_KEYS if key in value]
        remaining = [key for key in keys if key not in preferred]
        selected = (preferred + remaining)[:MAX_COMPACT_DICT_KEYS]
        compact_dict = {str(key): _compact_value_preview(value[key], depth + 1) for key in selected}
        if len(keys) > len(selected):
            compact_dict["_omitted_key_count"] = len(keys) - len(selected)
            compact_dict["_omitted_keys"] = [str(key) for key in keys if key not in selected][
                :MAX_COMPACT_DICT_KEYS
            ]
        return compact_dict
    return _string_preview(str(value))


def _write_tool_output_artifact(tool_name: str, result: Any, payload_json: str) -> Dict[str, Any]:
    digest = sha256(payload_json.encode("utf-8")).hexdigest()
    created_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    output_dir = _tool_output_artifact_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{created_at}_{_safe_tool_name(tool_name)}_{digest[:12]}.json"
    path = output_dir / filename
    artifact = {
        "tool": tool_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "payload_sha256": digest,
        "payload_json_bytes": len(payload_json.encode("utf-8")),
        "payload": result,
    }
    path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "path": str(path.resolve()),
        "payload_sha256": digest,
        "payload_json_bytes": artifact["payload_json_bytes"],
    }


def _compact_string_output_for_transport(result: str, tool_name: str) -> Any:
    max_inline_bytes = _max_inline_tool_output_bytes()
    result_bytes = len(result.encode("utf-8"))
    if result_bytes <= max_inline_bytes:
        return result

    payload_json = _compact_json_dumps(result)
    try:
        artifact = _write_tool_output_artifact(tool_name, result, payload_json)
    except OSError as exc:
        logger.warning(
            "Could not write compacted string output artifact for '%s': %s",
            tool_name,
            exc,
            exc_info=True,
        )
        return result

    compact = _string_preview(result)
    if not isinstance(compact, dict):
        compact = {"preview": compact}
    compact["_mcp_output"] = {
        "compacted": True,
        "lossless": True,
        "reason": (
            f"Tool string output was {result_bytes} bytes, above inline limit {max_inline_bytes}."
        ),
        "artifact": artifact,
        "restore": "Read the artifact JSON file and use its 'payload' field.",
        "original_type": "str",
    }
    return compact


def compact_tool_output_for_transport(result: Any, tool_name: str) -> Any:
    """Store oversized tool results and return a compact, lossless manifest."""
    if isinstance(result, str):
        return _compact_string_output_for_transport(result, tool_name)

    try:
        payload_json = _compact_json_dumps(result)
    except (TypeError, ValueError):
        return result

    max_inline_bytes = _max_inline_tool_output_bytes()
    payload_bytes = len(payload_json.encode("utf-8"))
    if payload_bytes <= max_inline_bytes:
        return result

    try:
        artifact = _write_tool_output_artifact(tool_name, result, payload_json)
    except OSError as exc:
        logger.warning(
            "Could not write compacted tool output artifact for '%s': %s",
            tool_name,
            exc,
            exc_info=True,
        )
        return result

    compact = _compact_value_preview(result)
    if not isinstance(compact, dict):
        compact = {"preview": compact}
    compact["_mcp_output"] = {
        "compacted": True,
        "lossless": True,
        "reason": (
            f"Tool output was {payload_bytes} JSON bytes, above inline limit {max_inline_bytes}."
        ),
        "artifact": artifact,
        "restore": "Read the artifact JSON file and use its 'payload' field.",
        "original_type": type(result).__name__,
    }
    if isinstance(result, dict):
        compact["_mcp_output"]["top_level_keys"] = [str(key) for key in result.keys()]
    return compact


def serialize_tool_output(result: Any, tool_name: str) -> str:
    """Serializes tool output to a string, preferably compact JSON."""
    json_kwargs = _tool_output_json_kwargs()
    try:
        if isinstance(result, str):
            # If it's already a string, assume it's intended as such
            # Try to parse/re-dump JSON for consistent formatting if it looks like JSON.
            try:
                parsed = json.loads(result)
                return json.dumps(parsed, **json_kwargs)
            except json.JSONDecodeError:
                return result  # Return as is if not valid JSON
        elif isinstance(result, dict):
            # Dump dicts to JSON
            return json.dumps(result, **json_kwargs)
        elif hasattr(result, "model_dump_json"):  # Pydantic v2
            # Prefer Pydantic v2 model_dump_json
            # The indent argument might not be supported by all versions/models,
            # so we might need to dump to dict first if indent is crucial.
            try:
                if json_kwargs.get("indent"):
                    return result.model_dump_json(indent=json_kwargs["indent"])
                return _compact_json_dumps(result.model_dump())
            except TypeError:  # Handle case where indent is not supported
                return json.dumps(result.model_dump(), **json_kwargs)
        elif hasattr(result, "model_dump"):  # Pydantic v2 fallback
            # Fallback to Pydantic v2 model_dump -> dict -> json
            return json.dumps(result.model_dump(), **json_kwargs)
        elif hasattr(result, "dict"):  # Pydantic v1
            # Fallback to Pydantic v1 dict -> json
            return json.dumps(result.dict(), **json_kwargs)
        else:
            # Absolute fallback: convert to string
            logger.warning(
                "Could not serialize result for tool '%s' to JSON, falling back to str(). Type: %s",
                tool_name,
                type(result),
            )
            return str(result)
    except Exception as e:
        logger.error(f"Error during serialization for tool '{tool_name}': {e}", exc_info=True)
        # Return an error message string formatted as JSON
        return json.dumps(
            {"error": f"Serialization failed for tool {tool_name}", "details": str(e)},
            **json_kwargs,
        )


class ServiceNowMCP:
    """ServiceNow MCP Server implementing Model Context Protocol for LLM integration.
    
    This server bridges LLMs and AI agents to ServiceNow by implementing the MCP protocol.
    Supports discovery of available tools and execution of ServiceNow operations.
    
    Main Responsibilities:
    1. Tool Discovery (list_tools): Dynamically enumerate available ServiceNow tools/tables
    2. Tool Execution (call_tool): Execute ServiceNow operations with governance enforcement
    3. Authentication: Manage multi-strategy auth (Basic/OAuth/API Key) lifecycle
    4. Release Awareness: Cache instance metadata and provide version-specific behavior
    5. Output Management: Compact large JSON responses and store artifacts
    
    Tool Package System:
    - Loads enabled tools from config/tool_packages.yaml
    - Supports filtering via MCP_TOOL_PACKAGE environment variable
    - Each package groups related ServiceNow operations (e.g., incident_tools, flow_tools)
    
    Governance Enforcement:
    - CMDB Mutations: All CI mutations routed through IRE API (direct Table API blocked)
    - CSDM Validation: Enforces approved CI classes and relationships
    - Stable Identifiers: Validates each CI has required identifier field
    - Audit Trail: Tracks all mutations with transaction ID, timestamp, payload hash
    
    Tool Output Handling:
    - Inline (< 20KB): Compact JSON sent in MCP response
    - Artifacts (> 20KB): Large results stored as timestamped JSON files
    - Preview: String truncation, list/dict key selection for readability
    - Hash Tracking: SHA256 digest of full payload for result validation
    """

    def __init__(self, config: Union[Dict, ServerConfig]):
        """Initialize ServiceNow MCP server with configuration and authentication.
        
        Initialization Flow:
        1. Parse config: Convert dict to ServerConfig if needed
        2. Setup auth: Create AuthManager with configured strategy
        3. Initialize MCP: Create low-level MCP server instance
        4. Load tools: Parse tool_packages.yaml for available tools
        5. Register handlers: Bind list_tools and call_tool implementations
        6. Fetch release context: Cache instance build/version (non-blocking on failure)
        
        Args:
            config: Server configuration as dict or ServerConfig instance
                - instance_url: ServiceNow instance URL (e.g., https://dev12345.service-now.com)
                - auth: AuthConfig specifying authentication strategy and credentials
                - other: Additional config for specific tool packages
        
        Attributes Set:
            self.config: Parsed ServerConfig
            self.auth_manager: AuthManager for authentication lifecycle
            self.mcp_server: Low-level MCP Server instance
            self.package_definitions: Dict of enabled tool packages
            self.enabled_tool_names: List of tools available in this instance
            self.tool_definitions: Dict mapping tool names to their implementations
            self.release_context: Instance metadata (build, version, plugins)
        
        Side Effects:
            - Loads config/tool_packages.yaml
            - Calls instance release context endpoint (logged on failure)
            - Registers MCP handlers
        
        Raises:
            FileNotFoundError: If tool_packages.yaml not found
            ValidationError: If config dict doesn't match ServerConfig schema
        """
        if isinstance(config, dict):
            self.config = ServerConfig(**config)
        else:
            self.config = config

        self.auth_manager = AuthManager(self.config.auth, self.config.instance_url)
        self.mcp_server = Server("ServiceNow")  # Use low-level Server
        self.name = "ServiceNow"

        self.package_definitions: Dict[str, List[str]] = {}
        self.enabled_tool_names: List[str] = []
        self.current_package_name: str = "none"
        self.release_context: Dict[str, Any] = {}
        self._load_package_config()
        self._determine_enabled_tools()

        # Get tool definitions, passing the aliased KB tool functions if needed
        self.tool_definitions = get_tool_definitions(
            create_kb_category_tool, list_kb_categories_tool
        )

        try:
            self.release_context = self.auth_manager.get_release_context(self.config)
            logger.info(
                "Release context initialized: build=%s family=%s source=%s",
                self.release_context.get("build_name"),
                self.release_context.get("family"),
                self.release_context.get("detection_source"),
            )
        except Exception as exc:
            logger.warning("Unable to initialize release context: %s", exc)
            self.release_context = {}

        self._register_handlers()

    def _register_handlers(self):
        """Register MCP protocol handlers for tool discovery and execution.
        
        Binds two core MCP request types to their implementations:
        
        1. list_tools: Responds to MCP discovery requests with available tools
           - Client uses this to discover what operations are available
           - Handler: _list_tools_impl
           - Response: List of Tool objects with name, description, input schema
        
        2. call_tool: Responds to MCP execution requests with tool results
           - Client uses this to execute ServiceNow operations
           - Handler: _call_tool_impl
           - Response: Tool result with status and content
        
        Returns:
            None (registers decorators with self.mcp_server)
        
        Note:
            Called during __init__ after MCP server creation. These handlers
            enable the bidirectional LLM-ServiceNow communication loop.
        """
        self.mcp_server.list_tools()(self._list_tools_impl)
        self.mcp_server.call_tool()(self._call_tool_impl)
        logger.info("Registered list_tools and call_tool handlers.")

    def _load_package_config(self):
        """Load tool package definitions from YAML configuration file.
        
        Parses config/tool_packages.yaml to discover which tool packages are
        available for this MCP server. Each package groups related ServiceNow
        operations (e.g., incident_tools, flow_tools, cmdb_tools).
        
        Config Format (YAML):
            package_name:
              - tool_name_1
              - tool_name_2
              ...
        
        Example:
            incident_tools:
              - create_incident
              - update_incident
              - list_incidents
            flow_tools:
              - list_flows
              - run_flow
        
        Path Resolution:
        - Tries TOOL_PACKAGE_CONFIG_PATH env variable first
        - Falls back to config/tool_packages.yaml relative to src/servicenow_mcp
        - Converts to absolute path for reliable file access
        
        Side Effects:
        - Sets self.package_definitions to loaded YAML dict
        - Logs success/error at INFO/ERROR level
        - Sets empty dict {} on parse errors
        
        Error Handling:
        - FileNotFoundError: Logs error, uses empty dict
        - YAMLError: Logs error, uses empty dict
        - Type error: Logs error if not dict, uses empty dict
        """
        config_path = TOOL_PACKAGE_CONFIG_PATH
        if not os.path.isabs(config_path):
            config_path = os.path.join(os.path.dirname(__file__), "..", "..", config_path)
            config_path = os.path.abspath(config_path)

        try:
            with open(config_path, "r") as f:
                loaded_config = yaml.safe_load(f)
                if isinstance(loaded_config, dict):
                    self.package_definitions = loaded_config
                    logger.info(f"Successfully loaded tool package config from {config_path}")
                else:
                    logger.error(
                        "Invalid format in %s: Expected a dictionary, got %s. No packages loaded.",
                        config_path,
                        type(loaded_config),
                    )
                    self.package_definitions = {}
        except FileNotFoundError:
            logger.error(
                f"Tool package config file not found at {config_path}. No packages loaded."
            )
            self.package_definitions = {}
        except yaml.YAMLError as e:
            logger.error(
                f"Error parsing tool package config file {config_path}: {e}. No packages loaded."
            )
            self.package_definitions = {}
        except Exception as e:
            logger.error(f"Unexpected error loading tool package config {config_path}: {e}")
            self.package_definitions = {}

    def _determine_enabled_tools(self):
        """Determine which tool packages to enable based on MCP_TOOL_PACKAGE env var.
        
        Selection Priority:
        1. MCP_TOOL_PACKAGE environment variable if set to valid package name
        2. MCP_TOOL_PACKAGE constant from servicenow_mcp.constants (default fallback)
        3. "none" if requested package not found in config (disables all tools)
        4. "full" if no package variable set (enables all packages in config)
        
        Package Filtering Use Cases:
        - "full": All tools from all packages (default, most complete)
        - "cmdb": Only CMDB-related tools (governance-focused)
        - "atf": Only ATF test framework tools
        - "none": No tools enabled (minimal server for auth testing)
        - Custom package: Any package name in config/tool_packages.yaml
        
        Side Effects:
        - Sets self.current_package_name: String name of enabled package
        - Sets self.enabled_tool_names: List of tool names in package
        - Deduplicates tool names from package definitions
        - Logs package selection at INFO/WARNING level
        
        Error Handling:
        - Invalid package: Logs warning, disables all tools (current_package_name="none")
        - Empty config: Sets enabled_tool_names to empty list
        
        Returns:
            None (updates self.current_package_name and self.enabled_tool_names)
        """
        requested_package = os.getenv("MCP_TOOL_PACKAGE", MCP_TOOL_PACKAGE).strip()

        if not requested_package:
            self.current_package_name = "full"
            logger.info("MCP_TOOL_PACKAGE is empty, defaulting to 'full' package.")
        elif requested_package in self.package_definitions:
            self.current_package_name = requested_package
            logger.info(f"MCP_TOOL_PACKAGE set to '{self.current_package_name}'.")
        else:
            self.current_package_name = "none"
            logger.warning(
                f"MCP_TOOL_PACKAGE '{requested_package}' is not a valid package name. "
                f"Valid packages: {list(self.package_definitions.keys())}. Loading 'none' package."
            )

        if self.package_definitions:
            package_tools = self.package_definitions.get(self.current_package_name, [])
            self.enabled_tool_names = self._dedupe_tool_names(package_tools)
        else:
            self.enabled_tool_names = []

        logger.info(
            "Loading package '%s' with %s tools.",
            self.current_package_name,
            len(self.enabled_tool_names),
        )

    def _dedupe_tool_names(self, tool_names: List[str]) -> List[str]:
        """Return tool names in package order while removing duplicate entries.

        Preserves first occurrence order to keep package-defined tool precedence,
        and logs duplicate names so configuration issues are visible.

        Args:
            tool_names: Tool names declared for a selected package.

        Returns:
            List[str]: Stable-order deduplicated tool names.
        """
        seen = set()
        deduped = []
        duplicate_counts = Counter(tool_names)
        duplicates = sorted(name for name, count in duplicate_counts.items() if count > 1)

        for tool_name in tool_names:
            if tool_name in seen:
                continue
            seen.add(tool_name)
            deduped.append(tool_name)

        if duplicates:
            logger.warning(
                "Package '%s' contains duplicate tool names; using first occurrence only: %s",
                self.current_package_name,
                ", ".join(duplicates),
            )

        return deduped

    async def _list_tools_impl(self) -> List[types.Tool]:
        """Implement MCP list_tools handler to discover available ServiceNow tools.
        
        Called by MCP clients to enumerate tools available on this server.
        Returns tool metadata (name, description, JSON input schema) for each
        enabled tool in the currently loaded package.
        
        Tool Discovery Process:
        1. Adds introspection tool (list_tool_packages) unless package is "none"
        2. Iterates through self.tool_definitions (all loaded tools)
        3. Filters to only tools in self.enabled_tool_names (current package)
        4. Converts Pydantic model input schema to JSON Schema format
        5. Returns list of types.Tool objects with metadata
        
        Tool Metadata:
        - name: Tool identifier (e.g., "create_incident")
        - description: Human-readable description from docstring
        - inputSchema: JSON Schema for tool parameters (from Pydantic model)
        
        Schema Generation:
        - Calls params_model.model_json_schema() for each tool
        - Handles schema generation errors (logs at ERROR level, skips tool)
        
        Returns:
            List[types.Tool] with enabled tools and their schemas
            - Empty list if package is "none"
            - Includes list_tool_packages introspection tool if not "none"
        
        Note:
            Async handler called by MCP framework. Errors in schema generation
            are caught and logged; affected tools are omitted from response.
        """
        tool_list: List[types.Tool] = []

        # Add the introspection tool if not 'none' package
        if self.current_package_name != "none":
            tool_list.append(
                types.Tool(
                    name="list_tool_packages",
                    description="Lists available tool packages and the currently loaded one.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "random_string": {
                                "type": "string",
                                "description": "Dummy parameter for no-parameter tools",
                            }
                        },
                        "required": ["random_string"],
                    },
                )
            )

        # Iterate through defined tools and add enabled ones
        for tool_name, definition in self.tool_definitions.items():
            if tool_name in self.enabled_tool_names:
                _impl_func, params_model, _return_annotation, description, _serialization = (
                    definition
                )
                try:
                    schema = params_model.model_json_schema()
                    tool_list.append(
                        types.Tool(name=tool_name, description=description, inputSchema=schema)
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to generate schema for tool '{tool_name}': {e}", exc_info=True
                    )

        logger.debug(f"Listing {len(tool_list)} tools for package '{self.current_package_name}'.")
        return tool_list

    async def _call_tool_impl(
        self, name: str, arguments: dict
    ) -> list[types.TextContent] | types.CallToolResult:
        """Implement MCP call_tool handler to execute ServiceNow operations.
        
        Main execution flow for tool requests from MCP clients.
        
        Execution Pipeline:
        1. Log tool call metadata (name, package, argument keys)
        2. Validate tool is enabled and defined
        3. Parse arguments from dict into Pydantic model
        4. Call tool implementation with parsed arguments
        5. Serialize result to string
        6. Compact output if exceeds size threshold
        7. Return MCP TextContent response
        
        Introspection Tool:
        - list_tool_packages: Special tool for discovering available packages
        - Returns package definitions and current package name
        
        Tool Execution Sequence:
        - Look up tool_name in self.tool_definitions
        - Verify in self.enabled_tool_names (security check)
        - Validate arguments against params_model Pydantic schema
        - Call implementation function with validated arguments
        - Apply result serialization (string conversion)
        - Check size and apply compaction if needed
        
        Error Handling:
        - Tool not found: Raises ValueError
        - Tool disabled: Raises ValueError
        - Invalid arguments: Raises ValidationError with details
        - Execution error: Logs at ERROR, returns error message
        - Large output: Compacts with preview + artifact storage
        
        Args:
            name: Tool name (must be in self.enabled_tool_names)
            arguments: Dict of arguments matching tool's Pydantic schema
        
        Returns:
            list[types.TextContent]: Single TextContent with serialized result
            - Text content: Result as JSON string (compacted if large)
            - Metadata: Tool name, package, argument keys (logged)
        
        Raises:
            ValueError: If tool unknown or disabled
            ValidationError: If arguments don't match Pydantic schema
        
        Note:
            Async handler called by MCP framework. Governance enforcement
            (CMDB routing, authentication) happens in tool implementation.
        """
        logger.info(
            "MCP_TOOL_CALL_START transport=lowlevel tool=%s package=%s arg_keys=%s",
            name,
            self.current_package_name,
            sorted((arguments or {}).keys()),
        )
        # Handle the introspection tool separately
        if name == "list_tool_packages":
            if self.current_package_name == "none":
                raise ValueError(
                    "Tool 'list_tool_packages' is not available in the 'none' package."
                )
            result_dict = self._list_tool_packages_impl()
            serialized_string = json.dumps(result_dict, indent=2)
            # Return a list with a TextContent object
            return [types.TextContent(type="text", text=serialized_string)]

        # Check if the tool exists and is enabled
        if name not in self.tool_definitions:
            raise ValueError(f"Unknown tool: {name}")
        if name not in self.enabled_tool_names:
            raise ValueError(
                f"Tool '{name}' is not enabled in the current package "
                f"'{self.current_package_name}'."
            )
        if self._should_block_cmdb_table_api_path(name, arguments):
            result = {
                "success": False,
                "ok": False,
                "message": TABLE_API_BLOCK_MESSAGE,
                "recommended_tool_namespace": "servicenow_internal_cmdb",
            }
            serialized_string = json.dumps(result, indent=2)
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=serialized_string)],
                structuredContent=result,
                isError=True,
            )

        # Get tool definition (we don't need the serialization hint anymore)
        definition = self.tool_definitions[name]
        impl_func, params_model, _return_annotation, _description, _serialization = definition

        # Validate and parse arguments using the Pydantic model
        try:
            params = params_model(**arguments)
            logger.debug(
                "MCP_TOOL_CALL_ARGS_PARSED tool=%s params_type=%s",
                name,
                type(params).__name__,
            )
        except ValidationError as e:
            logger.error("MCP_TOOL_CALL_INVALID_ARGS tool=%s error=%s", name, e, exc_info=True)
            raise ValueError(f"Invalid arguments for tool '{name}': {e}") from e
        except Exception as e:
            logger.error(
                "MCP_TOOL_CALL_ARG_PARSE_ERROR tool=%s error=%s",
                name,
                e,
                exc_info=True,
            )
            raise ValueError(f"Failed to parse arguments for tool '{name}': {e}") from e

        # Execute the tool implementation function
        try:
            self.release_context = self.auth_manager.get_release_context(self.config)
            module_name = infer_module_name_from_tool(name)
            self.auth_manager.get_release_behavior(self.config, module_name)
            result = impl_func(self.config, self.auth_manager, params)
            logger.info(
                "MCP_TOOL_CALL_END transport=lowlevel tool=%s module=%s result_type=%s",
                name,
                module_name,
                type(result).__name__,
            )
        except Exception as e:
            logger.error("MCP_TOOL_CALL_ERROR tool=%s error=%s", name, e, exc_info=True)
            raise RuntimeError(f"Error during execution of tool '{name}': {e}") from e

        # Serialize the result to a string (preferably JSON) using the helper.
        # Oversized results are stored losslessly as local artifacts and replaced
        # with a compact manifest before they reach MCP clients.
        result = compact_tool_output_for_transport(result, name)
        serialized_string = serialize_tool_output(result, name)
        logger.debug(f"Serialized value for tool '{name}': {serialized_string[:500]}...")

        if isinstance(result, dict):
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=serialized_string)],
                structuredContent=result,
                isError=result.get("success") is False,
            )

        # Return a list with a TextContent object
        return [types.TextContent(type="text", text=serialized_string)]

    def _should_block_cmdb_table_api_path(self, name: str, arguments: Dict[str, Any]) -> bool:
        """Determine whether a tool invocation should be blocked from Table API mutation.

        CMDB/CI mutations must be routed through internal IRE-governed tools to
        enforce CSDM validation and identifier safety. This helper blocks generic
        mutation-shaped requests when CMDB mutation intent is detected.

        Args:
            name: Requested tool name.
            arguments: Raw tool arguments supplied by the MCP client.

        Returns:
            bool: True when request should be blocked and redirected to internal
            CMDB tooling guidance.
        """
        if name.startswith("servicenow_internal_cmdb."):
            return False
        mutation_prefixes = (
            "create_",
            "update_",
            "delete_",
            "add_",
            "remove_",
            "link_",
            "assign_",
            "return_",
            "reserve_",
            "transfer_",
            "mark_",
            "perform_",
        )
        if not name.startswith(mutation_prefixes):
            return False
        if has_explicit_cmdb_mutation_target(arguments):
            return True
        if is_cmdb_table_api_mutation(arguments) and route_cmdb_ci_request("", arguments):
            return True
        return False

    def _list_tool_packages_impl(self) -> Dict[str, Any]:
        """Build package introspection payload for the list_tool_packages tool.

        Returns:
            Dict[str, Any]: Current package, available package names, and a
            message describing how to switch package selection.
        """
        available_packages = list(self.package_definitions.keys())
        return {
            "current_package": self.current_package_name,
            "available_packages": available_packages,
            "message": (
                f"Currently loaded package: '{self.current_package_name}'. "
                f"Set MCP_TOOL_PACKAGE env var to one of {available_packages} to switch."
            ),
        }

    def start(self) -> Server:
        """
        Prepares and returns the configured low-level MCP Server instance.

        The caller (e.g., cli.py) is responsible for obtaining the server
        instance from this method and running it within an appropriate
        async transport context (e.g., mcp.server.stdio.stdio_server).

        Returns:
            The configured mcp.server.lowlevel.Server instance.
        """
        logger.info(
            "ServiceNowMCP instance configured. Returning low-level server instance "
            "for external execution."
        )
        # The actual running of the server (server.run(...)) must happen
        # within an async context managed by the caller (e.g., using anyio
        # and a specific transport like stdio_server or SseServerTransport).
        return self.mcp_server
