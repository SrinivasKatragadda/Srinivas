"""
CMDB and Configuration Item Management Tools.
Provides CI search, relationship mapping, dependency analysis, and CMDB health metrics.
"""

from typing import Any, Dict, List, Optional
from mcp.server.mcpserver import MCPServer
from servicenow_mcp.client import default_client, ServiceNowClient
from servicenow_mcp.errors import ServiceNowError
from snow_itam import ITAMError, make_function
from snow_itom import CMDB_TOOLS, ITOMService, ItomMcpBackend


def register_cmdb_tools(server: MCPServer, client: Optional[ServiceNowClient] = None):
    active_client = client or default_client

    # search / get / create / update CI: one implementation shared with the Snow Agent (snow_itom/cmdb.py)
    def run(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return ITOMService(ItomMcpBackend(active_client)).call(name, arguments)
        except ITAMError as e:   # ITOMError and backend errors
            # str(e) = message + what to do (e.g. which plugin to install): MCP clients only see the message
            raise ServiceNowError(str(e), e.code, e.detail, e.status)

    for spec in CMDB_TOOLS:
        server.tool(name=spec.name, description=spec.description)(make_function(spec, run))

    @server.tool(
        name="list_ci_relationships",
        description=(
            "Show parent (upstream) and child (downstream) relationships for a CI in cmdb_rel_ci, one level deep. "
            "For multi-level walks use get_service_topology / get_ci_impact."
        ),
    )
    def list_ci_relationships(ci_sys_id: str, limit: int = 20, relationship_type: str = "") -> Dict[str, Any]:
        """
        Args:
            ci_sys_id: System ID of the Configuration Item.
            limit: Maximum relationships per direction (default 20, max 500).
            relationship_type: Only this type, by name (e.g. 'Depends on::Used by') or sys_id.
        """
        limit = max(1, min(int(limit or 20), 500))
        type_q = ""
        if relationship_type:
            type_q = f"^type={relationship_type}" if len(relationship_type) == 32 else f"^type.name={relationship_type}"
        # Parents (where this CI is child)
        parents = active_client.query_records(
            table="cmdb_rel_ci",
            query=f"child={ci_sys_id}{type_q}",
            fields="sys_id,parent,type,child",
            limit=limit,
            display_value="true",
        )
        # Children (where this CI is parent)
        children = active_client.query_records(
            table="cmdb_rel_ci",
            query=f"parent={ci_sys_id}{type_q}",
            fields="sys_id,parent,type,child",
            limit=limit,
            display_value="true",
        )
        return {
            "ci_sys_id": ci_sys_id,
            "upstream_parents": parents,
            "downstream_children": children,
            "total_relationships": len(parents) + len(children),
            **({"truncated": True} if limit in (len(parents), len(children)) else {}),
        }

    @server.tool(
        name="create_ci_relationship",
        description="Create a relationship between two Configuration Items in cmdb_rel_ci.",
    )
    def create_ci_relationship(
        parent_sys_id: str,
        child_sys_id: str,
        type_name_or_sys_id: str = "Depends on::Used by",
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Args:
            parent_sys_id: Parent CI sys_id.
            child_sys_id: Child CI sys_id.
            type_name_or_sys_id: Relationship type name (e.g. 'Runs on::Runs', 'Depends on::Used by') or sys_id from cmdb_rel_type.
            dry_run: If True, validates and previews without creating.
        """
        if parent_sys_id == child_sys_id:
            raise ServiceNowError("A CI cannot be related to itself", "INVALID_ARGS")
        for label, ci in (("parent", parent_sys_id), ("child", child_sys_id)):
            if not active_client.query_records("cmdb_ci", query=f"sys_id={ci}", fields="sys_id", limit=1):
                raise ServiceNowError(f"The {label} CI {ci} does not exist", "NOT_FOUND", status_code=404)
        rel_type = type_name_or_sys_id
        if len(rel_type) != 32:
            types = active_client.query_records(
                table="cmdb_rel_type",
                query=f"name={type_name_or_sys_id}",
                fields="sys_id,name",
                limit=2,
            )
            if len(types) != 1:
                raise ServiceNowError(
                    f"Relationship type '{type_name_or_sys_id}' was not found exactly once in cmdb_rel_type",
                    "INVALID_ARGS",
                    "Use the full name, e.g. 'Depends on::Used by' or 'Runs on::Runs', or pass the sys_id",
                )
            rel_type = types[0]["sys_id"]
        dup = active_client.query_records("cmdb_rel_ci", query=f"parent={parent_sys_id}^child={child_sys_id}^type={rel_type}",
                                          fields="sys_id", limit=1)
        if dup:
            raise ServiceNowError("This relationship already exists", "DUPLICATE", f"sys_id {dup[0]['sys_id']}", 409)
        if dry_run:
            return {"dry_run": True, "payload": {"parent": parent_sys_id, "child": child_sys_id, "type": rel_type}}

        payload = {
            "parent": parent_sys_id,
            "child": child_sys_id,
            "type": rel_type,
        }
        res = active_client.create_record("cmdb_rel_ci", payload)
        sys_id = res.get("sys_id")
        check = active_client.get_record("cmdb_rel_ci", sys_id, fields="sys_id,parent,child,type") if sys_id else {}
        verified = bool(check) and all(str(check.get(k) or "") == v for k, v in payload.items())
        return {
            "success": True,
            "message": "CI relationship created.",
            "sys_id": sys_id,
            "verified": verified,
            **({} if verified else {"note": "The relationship did not read back as written; check it in ServiceNow"}),
            "record": res,
        }

    @server.tool(
        name="cmdb_health_dashboard",
        description="Get CMDB data quality and completeness metrics across key CI classes.",
    )
    def cmdb_health_dashboard() -> Dict[str, Any]:
        """Calculates completeness metrics for server and application CIs."""
        try:
            total_servers = active_client.get_stats("cmdb_ci_server", count=True).get("stats", {}).get("count", "0")
            active_servers = active_client.get_stats("cmdb_ci_server", query="operational_status=1", count=True).get("stats", {}).get("count", "0")
            missing_ip = active_client.get_stats("cmdb_ci_server", query="ip_addressISEMPTY", count=True).get("stats", {}).get("count", "0")
            total_apps = active_client.get_stats("cmdb_ci_appl", count=True).get("stats", {}).get("count", "0")

            servers_missing_ip = int(missing_ip) if missing_ip else 0
            return {
                "server_metrics": {
                    "total_servers": int(total_servers) if total_servers else 0,
                    "operational_servers": int(active_servers) if active_servers else 0,
                    "servers_missing_ip": servers_missing_ip,
                },
                "application_metrics": {
                    "total_applications": int(total_apps) if total_apps else 0,
                },
                # Based only on the checks above (not ServiceNow's CMDB Health scores)
                "status": "Needs attention" if servers_missing_ip else "No issues found by these checks",
            }
        except Exception as e:
            return {"error": str(e), "status": "Error retrieving CMDB metrics"}
