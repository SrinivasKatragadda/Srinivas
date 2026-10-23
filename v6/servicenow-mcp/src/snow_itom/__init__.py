"""
snow_itom — IT Operations Management for ServiceNow, independent of any MCP framework.

    from snow_itom import ITOMService, ItomMcpBackend
    svc = ITOMService(ItomMcpBackend(servicenow_client))
    svc.call("get_ci_impact", {"ci": "lnux100"})

Generic plumbing (backends, tool specs, reference resolution, role checks) comes from snow_itam; the ITOM tables,
tools and rules are defined here.
"""

from snow_itom.backends import ItomMcpBackend, ItomSNBackend
from snow_itom.errors import ITOMError
from snow_itom.service import ITOMService
from snow_itom.tools import ALL_TOOLS, CMDB_TOOLS, TOOLS, TOOLS_BY_NAME

__all__ = ["ItomMcpBackend", "ItomSNBackend", "ITOMError", "ITOMService", "TOOLS", "CMDB_TOOLS", "ALL_TOOLS",
           "TOOLS_BY_NAME"]
