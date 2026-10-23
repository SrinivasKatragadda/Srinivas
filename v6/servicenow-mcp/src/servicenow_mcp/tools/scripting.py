"""
Scripting and Code Inspection Tools.
Allows querying Business Rules, Client Scripts, Script Includes, and safe background execution.
"""

from typing import Any, Dict, List, Optional
from mcp.server.mcpserver import MCPServer
from servicenow_mcp.client import default_client, ServiceNowClient
from servicenow_mcp.config import is_scripting_allowed
from servicenow_mcp.errors import ServiceNowError
from servicenow_mcp.tools.core import compute_diff

BUSINESS_RULE_FIELDS = (
    "sys_id,name,collection,active,when,order,priority,advanced,"
    "action_insert,action_update,action_delete,action_query,"
    "filter_condition,condition,script,description,sys_scope,sys_updated_on,sys_updated_by"
)
BUSINESS_RULE_WHEN_VALUES = {"before", "after", "async", "display"}
SCRIPT_INCLUDE_FIELDS = (
    "sys_id,name,api_name,active,client_callable,access,caller_access,script,description,"
    "sys_scope,sys_updated_on,sys_updated_by"
)
SCRIPT_INCLUDE_ACCESS_VALUES = {"public", "package_private"}
FIX_SCRIPT_FIELDS = (
    "sys_id,name,description,script,record_for_rollback,unloadable,before,"
    "sys_scope,sys_updated_on,sys_updated_by"
)


def register_scripting_tools(server: MCPServer, client: Optional[ServiceNowClient] = None):
    active_client = client or default_client

    def _resolve_business_rule(name_or_sysid: str, table: str = "") -> Dict[str, Any]:
        """Fetch a business rule by sys_id, or by exact name (optionally scoped to a table)."""
        ident = name_or_sysid.strip()
        if len(ident) == 32 and ident.isalnum():
            rows = active_client.query_records(
                table="sys_script", query=f"sys_id={ident}", fields=BUSINESS_RULE_FIELDS, limit=1
            )
            if rows:
                return rows[0]

        parts = [f"name={ident}"]
        if table:
            parts.append(f"collection={table}")
        rows = active_client.query_records(
            table="sys_script", query="^".join(parts), fields=BUSINESS_RULE_FIELDS, limit=5
        )
        if not rows:
            raise ServiceNowError(f"Business rule '{ident}' was not found.", code="NOT_FOUND", status_code=404)
        if len(rows) > 1:
            matches = [{"sys_id": r.get("sys_id"), "table": r.get("collection")} for r in rows]
            raise ServiceNowError(
                f"Business rule name '{ident}' is ambiguous ({len(rows)} matches).",
                code="AMBIGUOUS",
                detail=f"Pass a sys_id or a table to disambiguate. Matches: {matches}",
            )
        return rows[0]

    def _resolve_script_include(name_or_sysid: str) -> Dict[str, Any]:
        """Fetch a script include by sys_id, exact name, or exact API name (e.g. 'global.MyUtils')."""
        ident = name_or_sysid.strip()
        rows = active_client.query_records(
            table="sys_script_include",
            query=f"sys_id={ident}^ORname={ident}^ORapi_name={ident}",
            fields=SCRIPT_INCLUDE_FIELDS,
            limit=5,
        )
        if not rows:
            raise ServiceNowError(f"Script include '{ident}' was not found.", code="NOT_FOUND", status_code=404)
        if len(rows) > 1:
            matches = [{"sys_id": r.get("sys_id"), "api_name": r.get("api_name")} for r in rows]
            raise ServiceNowError(
                f"Script include '{ident}' is ambiguous ({len(rows)} matches).",
                code="AMBIGUOUS",
                detail=f"Pass a sys_id or full api_name to disambiguate. Matches: {matches}",
            )
        return rows[0]

    def _resolve_fix_script(name_or_sysid: str) -> Dict[str, Any]:
        """Fetch a fix script by sys_id or exact name."""
        ident = name_or_sysid.strip()
        rows = active_client.query_records(
            table="sys_script_fix", query=f"sys_id={ident}^ORname={ident}", fields=FIX_SCRIPT_FIELDS, limit=5
        )
        if not rows:
            raise ServiceNowError(f"Fix script '{ident}' was not found.", code="NOT_FOUND", status_code=404)
        if len(rows) > 1:
            matches = [{"sys_id": r.get("sys_id"), "sys_scope": r.get("sys_scope")} for r in rows]
            raise ServiceNowError(
                f"Fix script name '{ident}' is ambiguous ({len(rows)} matches).",
                code="AMBIGUOUS",
                detail=f"Pass a sys_id to disambiguate. Matches: {matches}",
            )
        return rows[0]

    def _replace_in_script(current_script: str, script_find: str, script_replace: str, label: str) -> tuple:
        occurrences = current_script.count(script_find) if script_find else 0
        if occurrences == 0:
            raise ServiceNowError(
                f"'script_find' text was not found in the {label} script.",
                code="SCRIPT_MATCH_NOT_FOUND",
                detail="Fetch the record first and copy the text exactly, including whitespace.",
            )
        return current_script.replace(script_find, script_replace), occurrences

    def _apply_script_record_update(
        table: str, record: Dict[str, Any], payload: Dict[str, Any], dry_run: bool, label: str
    ) -> Dict[str, Any]:
        sys_id = record["sys_id"]
        diff = compute_diff(record, payload)
        if dry_run:
            return {
                "dry_run": True,
                "sys_id": sys_id,
                "name": record.get("name"),
                "table": record.get("collection", table),
                "diff": diff,
                "payload": payload,
            }
        if not diff:
            return {"success": True, "sys_id": sys_id, "message": f"No changes; {label} already matches."}
        res = active_client.update_record(table, sys_id, payload)
        return {
            "success": True,
            "sys_id": sys_id,
            "name": res.get("name", record.get("name")),
            "changed_fields": sorted(diff.keys()),
            "record": res,
        }

    @server.tool(
        name="get_business_rules",
        description="Inspect Business Rules (sys_script) defined for a table.",
    )
    def get_business_rules(
        table: str,
        active_only: bool = True,
        limit: int = 25,
    ) -> Dict[str, Any]:
        """
        Args:
            table: Table name (e.g. 'incident').
            active_only: Only active business rules.
            limit: Maximum count to return.
        """
        parts = [f"collection={table}"]
        if active_only:
            parts.append("active=true")
        query = "^".join(parts)
        fields = "sys_id,name,when,action_insert,action_update,action_delete,action_query,order,script,filter_condition"
        rules = active_client.query_records(
            table="sys_script",
            query=query,
            fields=fields,
            limit=limit,
            order_by="order",
        )
        return {
            "table": table,
            "count": len(rules),
            "business_rules": rules,
        }

    @server.tool(
        name="get_business_rule",
        description="Fetch a single Business Rule (sys_script) by sys_id or exact name, including its full script.",
    )
    def get_business_rule(name_or_sysid: str, table: str = "") -> Dict[str, Any]:
        """
        Args:
            name_or_sysid: Business rule sys_id or exact name.
            table: Optional table name to disambiguate rules sharing the same name.
        """
        return {"business_rule": _resolve_business_rule(name_or_sysid, table)}

    @server.tool(
        name="update_business_rule",
        description=(
            "Edit an existing Business Rule (sys_script): script, trigger timing, operations, order, "
            "conditions, or active flag. Supports full script replacement or find/replace within the script. "
            "Pass dry_run=true to preview the diff without saving."
        ),
    )
    def update_business_rule(
        name_or_sysid: str,
        table: str = "",
        script: Optional[str] = None,
        script_find: Optional[str] = None,
        script_replace: Optional[str] = None,
        name: Optional[str] = None,
        when: Optional[str] = None,
        order: Optional[int] = None,
        active: Optional[bool] = None,
        action_insert: Optional[bool] = None,
        action_update: Optional[bool] = None,
        action_delete: Optional[bool] = None,
        action_query: Optional[bool] = None,
        filter_condition: Optional[str] = None,
        condition: Optional[str] = None,
        description: Optional[str] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Args:
            name_or_sysid: Business rule sys_id or exact name.
            table: Optional table name to disambiguate rules sharing the same name.
            script: New full script body (replaces the existing script).
            script_find: Exact text to find in the existing script (use with script_replace).
            script_replace: Replacement text for every occurrence of script_find.
            name: New rule name.
            when: Timing: 'before', 'after', 'async', or 'display'.
            order: Execution order (lower runs first).
            active: Enable or disable the rule.
            action_insert: Run on insert.
            action_update: Run on update.
            action_delete: Run on delete.
            action_query: Run on query.
            filter_condition: Encoded query for the condition builder (e.g. 'priority=1^active=true').
            condition: Advanced JavaScript condition (e.g. "current.state.changesTo(6)").
            description: Rule description.
            dry_run: If True, returns the field diff without modifying ServiceNow.
        """
        if script is not None and script_find is not None:
            raise ServiceNowError("Pass either 'script' or 'script_find'/'script_replace', not both.", "INVALID_ARGS")
        if (script_find is None) != (script_replace is None):
            raise ServiceNowError("'script_find' and 'script_replace' must be passed together.", "INVALID_ARGS")
        if when is not None and when not in BUSINESS_RULE_WHEN_VALUES:
            raise ServiceNowError(
                f"Invalid 'when' value '{when}'. Use one of: {sorted(BUSINESS_RULE_WHEN_VALUES)}.", "INVALID_ARGS"
            )

        rule = _resolve_business_rule(name_or_sysid, table)
        payload: Dict[str, Any] = {}

        if script is not None:
            payload["script"] = script
        elif script_find is not None:
            payload["script"], occurrences = _replace_in_script(
                rule.get("script") or "", script_find, script_replace, "business rule"
            )

        for key, value in (("name", name), ("when", when), ("filter_condition", filter_condition),
                           ("condition", condition), ("description", description)):
            if value is not None:
                payload[key] = value
        if order is not None:
            payload["order"] = str(order)
        for key, flag in (("active", active), ("action_insert", action_insert), ("action_update", action_update),
                          ("action_delete", action_delete), ("action_query", action_query)):
            if flag is not None:
                payload[key] = "true" if flag else "false"
        if "script" in payload or "condition" in payload:
            payload["advanced"] = "true"

        if not payload:
            raise ServiceNowError("No fields to update were provided.", "INVALID_ARGS")

        result = _apply_script_record_update("sys_script", rule, payload, dry_run, "business rule")
        if script_find is not None:
            result["script_replacements"] = occurrences
        return result

    @server.tool(
        name="set_business_rule_active",
        description="Activate or deactivate a Business Rule (sys_script). Pass dry_run=true to preview.",
    )
    def set_business_rule_active(
        name_or_sysid: str,
        active: bool,
        table: str = "",
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Args:
            name_or_sysid: Business rule sys_id or exact name.
            active: True to activate, False to deactivate.
            table: Optional table name to disambiguate rules sharing the same name.
            dry_run: If True, returns the field diff without modifying ServiceNow.
        """
        rule = _resolve_business_rule(name_or_sysid, table)
        return _apply_script_record_update(
            "sys_script", rule, {"active": "true" if active else "false"}, dry_run, "business rule"
        )

    @server.tool(
        name="create_business_rule",
        description=(
            "Create a new Business Rule (sys_script) on a table with script, timing, operations, and conditions. "
            "Pass dry_run=true to preview the payload without creating."
        ),
    )
    def create_business_rule(
        name: str,
        table: str,
        script: str = "",
        when: str = "before",
        order: int = 100,
        active: bool = True,
        action_insert: bool = False,
        action_update: bool = False,
        action_delete: bool = False,
        action_query: bool = False,
        filter_condition: str = "",
        condition: str = "",
        description: str = "",
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Args:
            name: Business rule name.
            table: Table the rule runs on (e.g. 'incident').
            script: Server-side script body. Defaults to an empty executeRule wrapper.
            when: Timing: 'before', 'after', 'async', or 'display'.
            order: Execution order (lower runs first).
            active: Whether the rule is active once created.
            action_insert: Run on insert.
            action_update: Run on update.
            action_delete: Run on delete.
            action_query: Run on query.
            filter_condition: Encoded query for the condition builder (e.g. 'priority=1^active=true').
            condition: Advanced JavaScript condition (e.g. "current.state.changesTo(6)").
            description: Rule description.
            dry_run: If True, returns the resolved payload without creating the rule.
        """
        if when not in BUSINESS_RULE_WHEN_VALUES:
            raise ServiceNowError(
                f"Invalid 'when' value '{when}'. Use one of: {sorted(BUSINESS_RULE_WHEN_VALUES)}.", "INVALID_ARGS"
            )
        if when != "display" and not (action_insert or action_update or action_delete or action_query):
            raise ServiceNowError(
                "At least one of action_insert, action_update, action_delete, or action_query must be true "
                "(except for 'display' rules), otherwise the rule never runs.",
                "INVALID_ARGS",
            )

        if not script:
            script = "(function executeRule(current, previous /*null when async*/) {\n\n})(current, previous);"

        payload: Dict[str, Any] = {
            "name": name,
            "collection": table,
            "when": when,
            "order": str(order),
            "active": "true" if active else "false",
            "advanced": "true",
            "action_insert": "true" if action_insert else "false",
            "action_update": "true" if action_update else "false",
            "action_delete": "true" if action_delete else "false",
            "action_query": "true" if action_query else "false",
            "script": script,
        }
        for key, value in (("filter_condition", filter_condition), ("condition", condition),
                           ("description", description)):
            if value:
                payload[key] = value

        existing = active_client.query_records(
            table="sys_script", query=f"name={name}^collection={table}", fields="sys_id", limit=1
        )
        warning = (
            f"A business rule named '{name}' already exists on '{table}' (sys_id {existing[0].get('sys_id')})."
            if existing else ""
        )

        if dry_run:
            result: Dict[str, Any] = {"dry_run": True, "table": "sys_script", "payload": payload}
        else:
            res = active_client.create_record("sys_script", payload)
            result = {"success": True, "sys_id": res.get("sys_id"), "name": res.get("name", name), "record": res}
        if warning:
            result["warning"] = warning
        return result

    @server.tool(
        name="get_client_scripts",
        description="Inspect Client Scripts (sys_script_client) defined for a table.",
    )
    def get_client_scripts(
        table: str,
        type: str = "",
        active_only: bool = True,
        limit: int = 25,
    ) -> Dict[str, Any]:
        """
        Args:
            table: Table name.
            type: Script type: 'onLoad', 'onChange', 'onSubmit', 'onCellEdit'.
            active_only: Only active scripts.
            limit: Maximum count.
        """
        parts = [f"table={table}"]
        if active_only:
            parts.append("active=true")
        if type:
            parts.append(f"type={type}")
        query = "^".join(parts)
        fields = "sys_id,name,type,field,ui_type,script"
        scripts = active_client.query_records(
            table="sys_script_client",
            query=query,
            fields=fields,
            limit=limit,
        )
        return {
            "table": table,
            "count": len(scripts),
            "client_scripts": scripts,
        }

    @server.tool(
        name="get_script_includes",
        description="Inspect Script Includes (sys_script_include) by name, API name, or script content.",
    )
    def get_script_includes(
        name: str = "",
        api_name: str = "",
        search: str = "",
        limit: int = 15,
    ) -> Dict[str, Any]:
        """
        Args:
            name: Name of the script include.
            api_name: API name / scope name.
            search: Search keyword inside the script code.
            limit: Maximum count to return.
        """
        parts = ["active=true"]
        if name:
            parts.append(f"nameLIKE{name}")
        if api_name:
            parts.append(f"api_nameLIKE{api_name}")
        if search:
            parts.append(f"scriptLIKE{search}")

        query = "^".join(parts)
        fields = "sys_id,name,api_name,client_callable,access,script,description"
        includes = active_client.query_records(
            table="sys_script_include",
            query=query,
            fields=fields,
            limit=limit,
        )
        return {
            "count": len(includes),
            "script_includes": includes,
        }

    @server.tool(
        name="get_script_include",
        description="Fetch a single Script Include (sys_script_include) by sys_id, exact name, or API name, including its full script.",
    )
    def get_script_include(name_or_sysid: str) -> Dict[str, Any]:
        """
        Args:
            name_or_sysid: Script include sys_id, exact name (e.g. 'MyUtils'), or API name (e.g. 'global.MyUtils').
        """
        return {"script_include": _resolve_script_include(name_or_sysid)}

    @server.tool(
        name="update_script_include",
        description=(
            "Edit an existing Script Include (sys_script_include): script, active flag, client callable, access, "
            "or description. Supports full script replacement or find/replace within the script. "
            "Pass dry_run=true to preview the diff without saving."
        ),
    )
    def update_script_include(
        name_or_sysid: str,
        script: Optional[str] = None,
        script_find: Optional[str] = None,
        script_replace: Optional[str] = None,
        active: Optional[bool] = None,
        client_callable: Optional[bool] = None,
        access: Optional[str] = None,
        description: Optional[str] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Args:
            name_or_sysid: Script include sys_id, exact name, or API name (e.g. 'global.MyUtils').
            script: New full script body (replaces the existing script).
            script_find: Exact text to find in the existing script (use with script_replace).
            script_replace: Replacement text for every occurrence of script_find.
            active: Enable or disable the script include.
            client_callable: Allow calls from client scripts via GlideAjax.
            access: Accessible from: 'public' (all application scopes) or 'package_private' (this scope only).
            description: Script include description.
            dry_run: If True, returns the field diff without modifying ServiceNow.
        """
        if script is not None and script_find is not None:
            raise ServiceNowError("Pass either 'script' or 'script_find'/'script_replace', not both.", "INVALID_ARGS")
        if (script_find is None) != (script_replace is None):
            raise ServiceNowError("'script_find' and 'script_replace' must be passed together.", "INVALID_ARGS")
        if access is not None and access not in SCRIPT_INCLUDE_ACCESS_VALUES:
            raise ServiceNowError(
                f"Invalid 'access' value '{access}'. Use one of: {sorted(SCRIPT_INCLUDE_ACCESS_VALUES)}.", "INVALID_ARGS"
            )

        include = _resolve_script_include(name_or_sysid)
        payload: Dict[str, Any] = {}

        if script is not None:
            payload["script"] = script
        elif script_find is not None:
            payload["script"], occurrences = _replace_in_script(
                include.get("script") or "", script_find, script_replace, "script include"
            )

        if access is not None:
            payload["access"] = access
        if description is not None:
            payload["description"] = description
        for key, flag in (("active", active), ("client_callable", client_callable)):
            if flag is not None:
                payload[key] = "true" if flag else "false"

        if not payload:
            raise ServiceNowError("No fields to update were provided.", "INVALID_ARGS")

        result = _apply_script_record_update("sys_script_include", include, payload, dry_run, "script include")
        if script_find is not None:
            result["script_replacements"] = occurrences
        return result

    @server.tool(
        name="list_fix_scripts",
        description="Search Fix Scripts (sys_script_fix) by name, description, or script content.",
    )
    def list_fix_scripts(
        name: str = "",
        search: str = "",
        limit: int = 20,
    ) -> Dict[str, Any]:
        """
        Args:
            name: Partial fix script name.
            search: Keyword to find in the description or script code.
            limit: Maximum count to return.
        """
        parts = []
        if name:
            parts.append(f"nameLIKE{name}")
        if search:
            parts.append(f"descriptionLIKE{search}^ORscriptLIKE{search}")
        rows = active_client.query_records(
            table="sys_script_fix",
            query="^".join(parts),
            fields="sys_id,name,description,record_for_rollback,unloadable,sys_scope,sys_updated_on,sys_updated_by",
            limit=limit,
            order_by="-sys_updated_on",
        )
        return {"count": len(rows), "fix_scripts": rows}

    @server.tool(
        name="get_fix_script",
        description="Fetch a single Fix Script (sys_script_fix) by sys_id or exact name, including its full script.",
    )
    def get_fix_script(name_or_sysid: str) -> Dict[str, Any]:
        """
        Args:
            name_or_sysid: Fix script sys_id or exact name.
        """
        return {"fix_script": _resolve_fix_script(name_or_sysid)}

    @server.tool(
        name="create_fix_script",
        description="Create a new Fix Script (sys_script_fix). Pass dry_run=true to preview the payload without creating.",
    )
    def create_fix_script(
        name: str,
        script: str,
        description: str = "",
        record_for_rollback: bool = True,
        unloadable: bool = False,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Args:
            name: Fix script name.
            script: Server-side JavaScript body.
            description: What the fix script does.
            record_for_rollback: Record database changes so the run can be rolled back.
            unloadable: Include the fix script when the application is installed/upgraded.
            dry_run: If True, returns the resolved payload without creating the fix script.
        """
        payload: Dict[str, Any] = {
            "name": name,
            "script": script,
            "record_for_rollback": "true" if record_for_rollback else "false",
            "unloadable": "true" if unloadable else "false",
        }
        if description:
            payload["description"] = description

        existing = active_client.query_records(
            table="sys_script_fix", query=f"name={name}", fields="sys_id", limit=1
        )
        warning = (
            f"A fix script named '{name}' already exists (sys_id {existing[0].get('sys_id')})." if existing else ""
        )

        if dry_run:
            result: Dict[str, Any] = {"dry_run": True, "table": "sys_script_fix", "payload": payload}
        else:
            res = active_client.create_record("sys_script_fix", payload)
            result = {"success": True, "sys_id": res.get("sys_id"), "name": res.get("name", name), "record": res}
        if warning:
            result["warning"] = warning
        return result

    @server.tool(
        name="update_fix_script",
        description=(
            "Edit an existing Fix Script (sys_script_fix): script, name, description, rollback and unloadable flags. "
            "Supports full script replacement or find/replace within the script. "
            "Pass dry_run=true to preview the diff without saving."
        ),
    )
    def update_fix_script(
        name_or_sysid: str,
        script: Optional[str] = None,
        script_find: Optional[str] = None,
        script_replace: Optional[str] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        record_for_rollback: Optional[bool] = None,
        unloadable: Optional[bool] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Args:
            name_or_sysid: Fix script sys_id or exact name.
            script: New full script body (replaces the existing script).
            script_find: Exact text to find in the existing script (use with script_replace).
            script_replace: Replacement text for every occurrence of script_find.
            name: New fix script name.
            description: Fix script description.
            record_for_rollback: Record database changes so the run can be rolled back.
            unloadable: Include the fix script when the application is installed/upgraded.
            dry_run: If True, returns the field diff without modifying ServiceNow.
        """
        if script is not None and script_find is not None:
            raise ServiceNowError("Pass either 'script' or 'script_find'/'script_replace', not both.", "INVALID_ARGS")
        if (script_find is None) != (script_replace is None):
            raise ServiceNowError("'script_find' and 'script_replace' must be passed together.", "INVALID_ARGS")

        fix = _resolve_fix_script(name_or_sysid)
        payload: Dict[str, Any] = {}

        if script is not None:
            payload["script"] = script
        elif script_find is not None:
            payload["script"], occurrences = _replace_in_script(
                fix.get("script") or "", script_find, script_replace, "fix script"
            )

        for key, value in (("name", name), ("description", description)):
            if value is not None:
                payload[key] = value
        for key, flag in (("record_for_rollback", record_for_rollback), ("unloadable", unloadable)):
            if flag is not None:
                payload[key] = "true" if flag else "false"

        if not payload:
            raise ServiceNowError("No fields to update were provided.", "INVALID_ARGS")

        result = _apply_script_record_update("sys_script_fix", fix, payload, dry_run, "fix script")
        if script_find is not None:
            result["script_replacements"] = occurrences
        return result

    @server.tool(
        name="list_background_script_history",
        description=(
            "List past background script runs (sys_script_execution_history) with their script and result, "
            "so a previous script can be reviewed, edited, and re-run with execute_background_script."
        ),
    )
    def list_background_script_history(
        search: str = "",
        executed_by: str = "",
        limit: int = 10,
    ) -> Dict[str, Any]:
        """
        Args:
            search: Keyword to find in the script code.
            executed_by: user_name of the user who ran the script.
            limit: Maximum count to return (most recent first).
        """
        parts = []
        if search:
            parts.append(f"scriptLIKE{search}")
        if executed_by:
            parts.append(f"executed_by.user_name={executed_by}")
        rows = active_client.query_records(
            table="sys_script_execution_history",
            query="^".join(parts),
            fields="sys_id,script,result,executed_by,started,finished,rollback_context",
            limit=limit,
            display_value="true",
            order_by="-started",
        )
        return {"count": len(rows), "executions": rows}

    @server.tool(
        name="execute_background_script",
        description="Safely execute server-side JavaScript on ServiceNow (requires SERVICENOW_ALLOW_SCRIPTING=true).",
    )
    def execute_background_script(script: str) -> Dict[str, Any]:
        """
        Args:
            script: JavaScript code to execute in background context.
        """
        if not is_scripting_allowed():
            raise ServiceNowError("Script execution is disabled by server configuration.", "SCRIPTING_DISABLED")

        # ServiceNow processors / sys.scripts.do endpoint or temporary fix script evaluation
        try:
            # We execute via sys_trigger or sys.scripts.do or REST script runner
            url = f"{active_client.instance_url}/sys.scripts.do"
            session = active_client.session
            auth = (active_client.username, active_client.password)
            r = session.post(
                url,
                auth=auth,
                data={"script": script, "runscript": "Run script"},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                verify=active_client.verify_tls,
                timeout=active_client.timeout,
            )
            return {
                "success": r.ok,
                "status_code": r.status_code,
                "output": r.text[:2000],
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
            }
