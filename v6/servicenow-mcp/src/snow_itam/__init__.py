"""
snow_itam — IT Asset Management operations for ServiceNow, independent of any MCP framework.

    from snow_itam import ITAMService, McpClientBackend, TOOLS
    svc = ITAMService(McpClientBackend(servicenow_client))
    svc.call("search_assets", {"asset_class": "hardware", "state": "In stock"})
"""

from snow_itam.backends import Backend, McpClientBackend, SNClientBackend
from snow_itam.errors import ITAMError
from snow_itam.service import ITAMService
from snow_itam.tools import TOOLS, TOOLS_BY_NAME, ToolSpec, make_function

__all__ = ["Backend", "McpClientBackend", "SNClientBackend", "ITAMError", "ITAMService", "TOOLS",
           "TOOLS_BY_NAME", "ToolSpec", "make_function"]
