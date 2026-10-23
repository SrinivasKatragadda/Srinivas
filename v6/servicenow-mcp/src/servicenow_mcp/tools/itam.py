"""
IT Asset Management (ITAM) tools.
Hardware / consumable assets and lifecycle, models, stockrooms and transfers, software licenses and
allocations, contracts and renewals, procurement and receiving, asset-CI reconciliation.
The logic lives in the framework-independent `snow_itam` package (shared with the Snow Agent).
"""

from typing import Any, Dict, Optional
from mcp.server.mcpserver import MCPServer
from servicenow_mcp.client import default_client, ServiceNowClient
from servicenow_mcp.errors import ServiceNowError
from snow_itam import ITAMError, ITAMService, McpClientBackend, TOOLS, make_function


def register_itam_tools(server: MCPServer, client: Optional[ServiceNowClient] = None):
    active_client = client or default_client

    def run(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return ITAMService(McpClientBackend(active_client)).call(name, arguments)
        except ITAMError as e:
            # str(e) = message + what to do (e.g. which plugin to install): MCP clients only see the message
            raise ServiceNowError(str(e), e.code, e.detail, e.status)

    for spec in TOOLS:
        server.tool(name=spec.name, description=spec.description)(make_function(spec, run))
