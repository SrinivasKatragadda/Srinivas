"""
ServiceNow MCP Tools Registry.
Registers all domain tool modules with the MCPServer instance.
"""

from mcp.server.mcpserver import MCPServer
from servicenow_mcp.client import default_client, ServiceNowClient
from servicenow_mcp.tools.core import register_core_tools
from servicenow_mcp.tools.incident import register_incident_tools
from servicenow_mcp.tools.change import register_change_tools
from servicenow_mcp.tools.problem import register_problem_tools
from servicenow_mcp.tools.task import register_task_tools
from servicenow_mcp.tools.cmdb import register_cmdb_tools
from servicenow_mcp.tools.itam import register_itam_tools
from servicenow_mcp.tools.catalog import register_catalog_tools
from servicenow_mcp.tools.knowledge import register_knowledge_tools
from servicenow_mcp.tools.user import register_user_tools
from servicenow_mcp.tools.scripting import register_scripting_tools
from servicenow_mcp.tools.script_records import register_script_record_tools
from servicenow_mcp.tools.atf import register_atf_tools
from servicenow_mcp.tools.properties import register_properties_tools
from servicenow_mcp.tools.updateset import register_updateset_tools
from servicenow_mcp.tools.itom import register_itom_tools
from servicenow_mcp.tools.health import register_health_tools


def register_all_tools(server: MCPServer, client: ServiceNowClient = default_client):
    """Register all ServiceNow MCP tools with the server."""
    register_core_tools(server, client)
    register_incident_tools(server, client)
    register_change_tools(server, client)
    register_problem_tools(server, client)
    register_task_tools(server, client)
    register_cmdb_tools(server, client)
    register_itam_tools(server, client)
    register_catalog_tools(server, client)
    register_knowledge_tools(server, client)
    register_user_tools(server, client)
    register_scripting_tools(server, client)
    register_script_record_tools(server, client)
    register_atf_tools(server, client)
    register_properties_tools(server, client)
    register_updateset_tools(server, client)
    register_itom_tools(server, client)
    register_health_tools(server, client)
