"""
IT Operations Management (ITOM) tools.
Capabilities, MID Servers, Discovery status / schedules, Event Management alerts, CI classes, service topology,
CI impact, unmapped CIs, application service creation, flow / workflow executions.
The logic lives in the framework-independent `snow_itom` package (shared with the Snow Agent).
"""

from typing import Any, Dict, Optional
from mcp.server.mcpserver import MCPServer
from servicenow_mcp.client import default_client, ServiceNowClient
from servicenow_mcp.errors import ServiceNowError
from snow_itam import ITAMError, make_function
from snow_itom import ITOMService, ItomMcpBackend, TOOLS


def register_itom_tools(server: MCPServer, client: Optional[ServiceNowClient] = None):
    active_client = client or default_client

    def run(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return ITOMService(ItomMcpBackend(active_client)).call(name, arguments)
        except ITAMError as e:   # ITOMError and backend errors
            # str(e) = message + what to do (e.g. which plugin to install): MCP clients only see the message
            raise ServiceNowError(str(e), e.code, e.detail, e.status)

    for spec in TOOLS:
        server.tool(name=spec.name, description=spec.description)(make_function(spec, run))
