"""
Service Catalog and Request Fulfillment Tools.
Allows exploring catalog items, inspecting catalog variables, ordering items,
and tracking Requested Items (RITMs) and Requests (REQs).
"""

from typing import Any, Dict, List, Optional
from mcp.server.mcpserver import MCPServer
from servicenow_mcp.client import default_client, ServiceNowClient
from servicenow_mcp.errors import ServiceNowError


def register_catalog_tools(server: MCPServer, client: Optional[ServiceNowClient] = None):
    active_client = client or default_client

    @server.tool(
        name="list_catalog_items",
        description="List active ServiceNow Service Catalog items (sc_cat_item).",
    )
    def list_catalog_items(
        category: str = "",
        search: str = "",
        limit: int = 20,
    ) -> Dict[str, Any]:
        """
        Args:
            category: Filter by category name or sys_id.
            search: Search in name or short_description.
            limit: Maximum items to return (default 20).
        """
        parts = ["active=true"]
        if category:
            parts.append(f"category.titleLIKE{category}^ORcategory={category}")
        if search:
            parts.append(f"nameLIKE{search}^ORshort_descriptionLIKE{search}")

        query = "^".join(parts)
        fields = "sys_id,name,short_description,category,price,recurring_price,workflow"
        items = active_client.query_records(
            table="sc_cat_item",
            query=query,
            fields=fields,
            limit=limit,
            display_value="true",
        )
        return {
            "count": len(items),
            "catalog_items": items,
        }

    @server.tool(
        name="get_catalog_item",
        description="Retrieve catalog item details along with its questions/variables (item_option_new).",
    )
    def get_catalog_item(item_sys_id: str) -> Dict[str, Any]:
        """
        Args:
            item_sys_id: System ID of the catalog item.
        """
        item = active_client.get_record("sc_cat_item", item_sys_id, display_value="true")
        # Fetch variables for this item
        variables = active_client.query_records(
            table="item_option_new",
            query=f"cat_item={item_sys_id}^active=true^ORDERBYorder",
            fields="sys_id,name,question_text,type,mandatory,default_value",
            display_value="true",
        )
        return {
            "catalog_item": item,
            "variables": variables,
        }

    @server.tool(
        name="order_catalog_item",
        description="Order a service catalog item with variable values.",
    )
    def order_catalog_item(
        item_sys_id: str,
        variables: Optional[Dict[str, Any]] = None,
        quantity: int = 1,
    ) -> Dict[str, Any]:
        """
        Args:
            item_sys_id: System ID of the catalog item to order.
            variables: Dictionary of variable names and their values.
            quantity: Quantity (default 1).
        """
        # Call ServiceNow Service Catalog API: /api/sn_sc/servicecatalog/items/{sys_id}/order_now
        payload = {
            "sysparm_quantity": quantity,
            "variables": variables or {},
        }
        res = active_client._request("POST", f"/api/sn_sc/servicecatalog/items/{item_sys_id}/order_now", json_data=payload)
        result = res.get("result", {})
        return {
            "success": True,
            "request_number": result.get("request_number"),
            "request_id": result.get("request_id"),
            "result": result,
        }

    @server.tool(
        name="list_sc_requests",
        description="List Requested Items (sc_req_item / RITM) with state and stage tracking.",
    )
    def list_sc_requests(
        state: str = "",
        stage: str = "",
        request_number: str = "",
        limit: int = 20,
    ) -> Dict[str, Any]:
        """
        Args:
            state: RITM state code.
            stage: Workflow stage (e.g. 'request_approved', 'fulfillment').
            request_number: Parent Request number (e.g. REQ00123).
            limit: Maximum count to return.
        """
        parts = ["active=true"]
        if state:
            parts.append(f"state={state}")
        if stage:
            parts.append(f"stage={stage}")
        if request_number:
            parts.append(f"request.number={request_number}")

        query = "^".join(parts)
        fields = "sys_id,number,cat_item,state,stage,request,assigned_to,opened_by,sys_created_on"
        ritms = active_client.query_records(
            table="sc_req_item",
            query=query,
            fields=fields,
            limit=limit,
            order_by="-sys_created_on",
            display_value="true",
        )
        return {
            "count": len(ritms),
            "requested_items": ritms,
        }

    @server.tool(
        name="get_request_item",
        description="Get complete details of a specific Requested Item (RITM) by number or sys_id.",
    )
    def get_request_item(number_or_sysid: str) -> Dict[str, Any]:
        """
        Args:
            number_or_sysid: RITM number (RITM...) or sys_id.
        """
        ident = number_or_sysid.strip()
        if len(ident) == 32:
            rec = active_client.get_record("sc_req_item", ident, display_value="true")
        else:
            rows = active_client.query_records("sc_req_item", query=f"number={ident}", limit=1, display_value="true")
            if not rows:
                raise ServiceNowError(f"Requested item '{ident}' not found.", code="NOT_FOUND", status_code=404)
            rec = rows[0]
        return {"requested_item": rec}
