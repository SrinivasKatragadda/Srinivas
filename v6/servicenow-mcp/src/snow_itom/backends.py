"""
ServiceNow access for the ITOM service: snow_itam's Table API adapters plus `request` for the scripted REST APIs
ITOM needs (Application Service API). Duck-typed like snow_itam.backends: no client package is imported here.
"""

from typing import Any, Dict, Optional

from snow_itam.backends import McpClientBackend, SNClientBackend, _wrap
from snow_itom.errors import ITOMError


class ItomMcpBackend(McpClientBackend):
    """servicenow_mcp.client.ServiceNowClient. Non-GET calls honour SERVICENOW_WRITE_ENABLED like create_record."""

    def request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None,
                json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if method.upper() != "GET":
            from servicenow_mcp.config import is_write_enabled
            if not is_write_enabled():
                raise ITOMError("Writes are disabled. Set SERVICENOW_WRITE_ENABLED=true to allow writes.",
                                "WRITE_DISABLED")
        try:
            return self.c._request(method.upper(), path, params=params, json_data=json)
        except Exception as e:
            raise _wrap(e)


class ItomSNBackend(SNClientBackend):
    """The Snow Agent's sn_client.SNClient (writes are gated by the agent's confirm step)."""

    def request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None,
                json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        try:
            return self.sn.request(method.upper(), path, params=params, json=json)
        except Exception as e:
            raise _wrap(e)
