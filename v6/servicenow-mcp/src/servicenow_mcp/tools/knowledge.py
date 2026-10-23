"""
Knowledge Management Tools.
Provides search, retrieval, creation, and lifecycle management for ServiceNow Knowledge Base articles.
"""

from typing import Any, Dict, List, Optional
from mcp.server.mcpserver import MCPServer
from servicenow_mcp.client import default_client, ServiceNowClient
from servicenow_mcp.errors import ServiceNowError


def register_knowledge_tools(server: MCPServer, client: Optional[ServiceNowClient] = None):
    active_client = client or default_client

    def _resolve_kb_sys_id(number_or_sysid: str) -> str:
        ident = number_or_sysid.strip()
        if len(ident) == 32 and not ident.upper().startswith("KB"):
            return ident
        rows = active_client.query_records(
            table="kb_knowledge",
            query=f"number={ident}^ORsys_id={ident}",
            fields="sys_id,number",
            limit=1,
        )
        if not rows:
            raise ServiceNowError(f"Knowledge article '{ident}' not found.", code="NOT_FOUND", status_code=404)
        return rows[0]["sys_id"]

    @server.tool(
        name="search_knowledge",
        description="Search published ServiceNow knowledge base articles by keyword, category, or topic.",
    )
    def search_knowledge(
        query: str,
        category: str = "",
        limit: int = 10,
    ) -> Dict[str, Any]:
        """
        Args:
            query: Keywords to search in title and article body.
            category: Optional knowledge category filter.
            limit: Maximum articles to return (default 10).
        """
        parts = ["workflow_state=published"]
        if query:
            parts.append(f"short_descriptionLIKE{query}^ORtextLIKE{query}")
        if category:
            parts.append(f"kb_category.labelLIKE{category}^ORkb_category={category}")

        final_query = "^".join(parts)
        fields = "sys_id,number,short_description,kb_category,kb_knowledge_base,workflow_state,published,author"
        articles = active_client.query_records(
            table="kb_knowledge",
            query=final_query,
            fields=fields,
            limit=limit,
            order_by="-published",
            display_value="true",
        )
        return {
            "count": len(articles),
            "articles": articles,
        }

    @server.tool(
        name="get_knowledge_article",
        description="Retrieve the full text, author, and metadata of a knowledge base article by KB number or sys_id.",
    )
    def get_knowledge_article(number_or_sysid: str) -> Dict[str, Any]:
        """
        Args:
            number_or_sysid: Article number (e.g. KB001001) or sys_id.
        """
        sys_id = _resolve_kb_sys_id(number_or_sysid)
        article = active_client.get_record("kb_knowledge", sys_id, display_value="true")
        return {"article": article}

    @server.tool(
        name="create_knowledge_article",
        description="Create a new draft Knowledge Base article in ServiceNow.",
    )
    def create_knowledge_article(
        short_description: str,
        text: str,
        kb_knowledge_base: str = "",
        kb_category: str = "",
    ) -> Dict[str, Any]:
        """
        Args:
            short_description: Title of the article.
            text: HTML or plain text body of the article.
            kb_knowledge_base: Sys_id or name of the Knowledge Base.
            kb_category: Sys_id or name of the category.
        """
        payload: Dict[str, Any] = {
            "short_description": short_description,
            "text": text,
            "workflow_state": "draft",
        }
        if kb_knowledge_base:
            payload["kb_knowledge_base"] = kb_knowledge_base
        if kb_category:
            payload["kb_category"] = kb_category

        res = active_client.create_record("kb_knowledge", payload)
        num = res.get("number", "Unknown")
        return {
            "success": True,
            "number": num,
            "sys_id": res.get("sys_id"),
            "summary": f"Knowledge article {num} created in draft state.",
            "record": res,
        }

    @server.tool(
        name="retire_knowledge_article",
        description="Retire an outdated knowledge article (sets workflow_state to retired).",
    )
    def retire_knowledge_article(number_or_sysid: str) -> Dict[str, Any]:
        """
        Args:
            number_or_sysid: Article number or sys_id.
        """
        sys_id = _resolve_kb_sys_id(number_or_sysid)
        res = active_client.update_record("kb_knowledge", sys_id, {"workflow_state": "retired"})
        return {
            "success": True,
            "message": f"Knowledge article {number_or_sysid} retired.",
            "record": res,
        }
