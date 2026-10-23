"""
Generic Script Record Tools.
List, read, create, and edit any server-side, client-side, or hybrid script record type
(scheduled jobs, ACLs, transform scripts, script actions, Scripted REST APIs, inbound email
actions, record producers, data policies, client scripts, catalog client scripts, UI policies,
UI scripts, validation scripts, UI actions, UI pages, UI macros, Service Portal widgets, ...).
"""

import re
from typing import Any, Dict, Optional
from mcp.server.mcpserver import MCPServer
from servicenow_mcp.client import default_client, ServiceNowClient
from servicenow_mcp.errors import ServiceNowError
from servicenow_mcp.tools.core import compute_diff
from servicenow_mcp.tools.script_types import (
    SCRIPT_TYPES,
    ScriptType,
    describe_script_type,
    get_script_type,
)

_SYS_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_TRUE_VALUES = {"true", "1", "yes", "on"}
_FALSE_VALUES = {"false", "0", "no", "off"}


def _is_sys_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_SYS_ID_RE.match(value.strip()))


PROTECTED_NOTE = (
    "This is a protected record (sys_policy=protected): ServiceNow hides its script fields from the API "
    "and does not allow it to be edited. Copy it to a new record instead."
)


def register_script_record_tools(server: MCPServer, client: Optional[ServiceNowClient] = None):
    active_client = client or default_client

    def _resolve_reference(spec: ScriptType, field_name: str, value: Any) -> str:
        """Turn a display value (e.g. a transform map name) into a sys_id for reference fields."""
        text = str(value).strip()
        if not text or _is_sys_id(text):
            return text
        ref_table, display_field = spec.references.get(field_name, ("", ""))
        if not ref_table:
            raise ServiceNowError(f"Field '{field_name}' must be a sys_id.", "INVALID_ARGS")
        rows = active_client.query_records(
            table=ref_table, query=f"{display_field}={text}", fields="sys_id", limit=2
        )
        if not rows:
            raise ServiceNowError(
                f"No {ref_table} record with {display_field}='{text}' for field '{field_name}'.",
                code="NOT_FOUND",
                status_code=404,
            )
        if len(rows) > 1:
            raise ServiceNowError(
                f"'{text}' matches several {ref_table} records for field '{field_name}'; pass a sys_id.",
                code="AMBIGUOUS",
            )
        return rows[0]["sys_id"]

    def _coerce_fields(spec: ScriptType, fields: Dict[str, Any]) -> Dict[str, Any]:
        unknown = sorted(set(fields) - set(spec.fields))
        if unknown:
            raise ServiceNowError(
                f"Unknown field(s) for {spec.key}: {unknown}.",
                code="INVALID_ARGS",
                detail=f"Allowed fields: {sorted(spec.fields)}. Use update_record for other columns.",
            )
        payload: Dict[str, Any] = {}
        for name, value in fields.items():
            kind = spec.fields[name]
            if kind == "bool":
                text = str(value).strip().lower()
                if isinstance(value, bool):
                    text = "true" if value else "false"
                if text not in _TRUE_VALUES | _FALSE_VALUES:
                    raise ServiceNowError(f"Field '{name}' must be true or false, got '{value}'.", "INVALID_ARGS")
                payload[name] = "true" if text in _TRUE_VALUES else "false"
            elif kind == "int":
                try:
                    payload[name] = str(int(value))
                except (TypeError, ValueError):
                    raise ServiceNowError(f"Field '{name}' must be an integer, got '{value}'.", "INVALID_ARGS")
            elif kind == "ref":
                payload[name] = _resolve_reference(spec, name, value)
            else:
                payload[name] = "" if value is None else str(value)

            allowed = spec.choices.get(name)
            if allowed and payload[name] not in allowed:
                raise ServiceNowError(
                    f"Invalid value '{payload[name]}' for {spec.key}.{name}. Use one of: {list(allowed)}.",
                    "INVALID_ARGS",
                )
        return payload

    def _apply_implied(spec: ScriptType, payload: Dict[str, Any], explicit: Dict[str, Any]) -> None:
        for trigger, flags in spec.implied.items():
            if trigger in payload and payload[trigger]:
                for flag, flag_value in flags.items():
                    if flag not in explicit:
                        payload[flag] = flag_value

    def _resolve_record(spec: ScriptType, ident: str, table: str = "", parent: str = "") -> Dict[str, Any]:
        ident = ident.strip()
        if _is_sys_id(ident):
            rows = active_client.query_records(
                table=spec.table, query=f"sys_id={ident}", fields=spec.record_fields(), limit=1
            )
            if rows:
                return rows[0]
        if not spec.lookup_fields:
            raise ServiceNowError(
                f"{spec.label} records have no name; pass a sys_id (use list_script_records to find it).",
                code="NOT_FOUND" if _is_sys_id(ident) else "INVALID_ARGS",
            )

        parts = ["^OR".join(f"{lf}={ident}" for lf in spec.lookup_fields)]
        if table and spec.table_field:
            parts.append(f"{spec.table_field}={table}")
        if parent and spec.parent_field:
            parts.append(f"{spec.parent_field}={_resolve_reference(spec, spec.parent_field, parent)}")
        rows = active_client.query_records(
            table=spec.table, query="^".join(parts), fields=spec.record_fields(), limit=5
        )
        if not rows:
            raise ServiceNowError(f"{spec.label} '{ident}' was not found.", code="NOT_FOUND", status_code=404)
        if len(rows) > 1:
            context = spec.table_field or spec.parent_field
            matches = [{"sys_id": r.get("sys_id"), **({context: r.get(context)} if context else {})} for r in rows]
            raise ServiceNowError(
                f"{spec.label} '{ident}' is ambiguous ({len(rows)} matches).",
                code="AMBIGUOUS",
                detail=f"Pass a sys_id, table, or parent to disambiguate. Matches: {matches}",
            )
        return rows[0]

    @server.tool(
        name="list_script_types",
        description=(
            "List every script record type the generic script tools support (server-side, client-side, hybrid), "
            "with table, editable fields, choices, and notes. Call this before create/update_script_record."
        ),
    )
    def list_script_types(script_type: str = "", side: str = "") -> Dict[str, Any]:
        """
        Args:
            script_type: Return full details for one type only (e.g. 'scheduled_job').
            side: Filter by 'server', 'client', or 'hybrid'.
        """
        if script_type:
            return describe_script_type(get_script_type(script_type))
        specs = [s for s in SCRIPT_TYPES.values() if not side or s.side == side]
        return {
            "count": len(specs),
            "script_types": [describe_script_type(s, include_fields=False) for s in specs],
        }

    @server.tool(
        name="list_script_records",
        description=(
            "Search records of any script type (e.g. scheduled_job, acl, client_script, ui_policy, ui_action, "
            "sp_widget) by name, table, parent record, or script content. Script bodies are omitted; "
            "use get_script_record for the full record."
        ),
    )
    def list_script_records(
        script_type: str,
        name: str = "",
        table: str = "",
        parent: str = "",
        search: str = "",
        active_only: bool = False,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """
        Args:
            script_type: Script type key (see list_script_types).
            name: Partial name / identifier to match.
            table: Table the scripts apply to (for types with a table field, e.g. client_script, ui_action).
            parent: Parent record name or sys_id (e.g. transform map for transform_script, Scripted REST API
                for scripted_rest_resource, UI policy for ui_policy_action, catalog item for catalog_client_script).
            search: Keyword to find inside any script field.
            active_only: Only active records (for types with an active flag).
            limit: Maximum count to return.
        """
        spec = get_script_type(script_type)
        parts = []
        if name:
            lookups = spec.lookup_fields or ()
            if not lookups:
                raise ServiceNowError(f"{spec.label} records have no name to filter on; use parent.", "INVALID_ARGS")
            parts.append("^OR".join(f"{lf}LIKE{name}" for lf in lookups))
        if table:
            if not spec.table_field:
                raise ServiceNowError(f"{spec.label} has no table field; use parent instead.", "INVALID_ARGS")
            parts.append(f"{spec.table_field}={table}")
        if parent:
            if not spec.parent_field:
                raise ServiceNowError(f"{spec.label} has no parent record.", "INVALID_ARGS")
            parts.append(f"{spec.parent_field}={_resolve_reference(spec, spec.parent_field, parent)}")
        if search:
            if not spec.script_fields:
                raise ServiceNowError(f"{spec.label} has no script fields to search.", "INVALID_ARGS")
            parts.append("^OR".join(f"{sf}LIKE{search}" for sf in spec.script_fields))
        if active_only and "active" in spec.fields:
            parts.append("active=true")

        rows = active_client.query_records(
            table=spec.table,
            query="^".join(parts),
            fields=spec.summary_fields(),
            limit=limit,
            order_by="-sys_updated_on",
        )
        return {"script_type": spec.key, "table": spec.table, "count": len(rows), "records": rows}

    @server.tool(
        name="get_script_record",
        description="Fetch one record of any script type by sys_id or name, including all script fields.",
    )
    def get_script_record(
        script_type: str,
        name_or_sysid: str,
        table: str = "",
        parent: str = "",
    ) -> Dict[str, Any]:
        """
        Args:
            script_type: Script type key (see list_script_types).
            name_or_sysid: sys_id, or the record's name / identifier (see lookup_fields in list_script_types).
            table: Optional table to disambiguate records sharing a name.
            parent: Optional parent record name or sys_id to disambiguate.
        """
        spec = get_script_type(script_type)
        record = _resolve_record(spec, name_or_sysid, table, parent)
        result: Dict[str, Any] = {"script_type": spec.key, "table": spec.table, "record": record}
        if record.get("sys_policy") == "protected":
            result["warning"] = PROTECTED_NOTE
        return result

    @server.tool(
        name="create_script_record",
        description=(
            "Create a record of any script type (scheduled job, ACL, transform script, script action, Scripted REST "
            "API/resource, inbound email action, record producer, data policy, client script, catalog client script, "
            "UI policy, UI script, validation script, UI action, UI page, UI macro, portal widget, ...). "
            "Reference fields accept names instead of sys_ids. Pass dry_run=true to preview."
        ),
    )
    def create_script_record(
        script_type: str,
        fields: Dict[str, Any],
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Args:
            script_type: Script type key (see list_script_types for fields and required ones).
            fields: Field values, e.g. {'name': 'Nightly cleanup', 'run_type': 'daily', 'run_time': '02:00:00',
                'script': '...'}. Booleans may be true/false; reference fields may be names.
            dry_run: If True, returns the resolved payload without creating the record.
        """
        spec = get_script_type(script_type)
        payload = _coerce_fields(spec, fields)
        missing = [f for f in spec.required if not payload.get(f)]
        if missing:
            raise ServiceNowError(
                f"Missing required field(s) for {spec.key}: {missing}.",
                code="INVALID_ARGS",
                detail=describe_script_type(spec).get("notes", ""),
            )
        _apply_implied(spec, payload, fields)

        warning = ""
        if spec.lookup_fields and payload.get(spec.lookup_fields[0]):
            dup_parts = [f"{spec.lookup_fields[0]}={payload[spec.lookup_fields[0]]}"]
            for scope_field in (spec.table_field, spec.parent_field):
                if scope_field and payload.get(scope_field):
                    dup_parts.append(f"{scope_field}={payload[scope_field]}")
            existing = active_client.query_records(
                table=spec.table, query="^".join(dup_parts), fields="sys_id", limit=1
            )
            if existing:
                warning = (
                    f"A {spec.label} with {spec.lookup_fields[0]}='{payload[spec.lookup_fields[0]]}' already exists "
                    f"(sys_id {existing[0].get('sys_id')})."
                )

        if dry_run:
            result: Dict[str, Any] = {"dry_run": True, "script_type": spec.key, "table": spec.table, "payload": payload}
        else:
            res = active_client.create_record(spec.table, payload)
            result = {"success": True, "script_type": spec.key, "table": spec.table,
                      "sys_id": res.get("sys_id"), "record": res}
        if warning:
            result["warning"] = warning
        return result

    @server.tool(
        name="update_script_record",
        description=(
            "Edit a record of any script type: set fields and/or find/replace inside one script field "
            "(e.g. script, script_true, client_script, operation_script). Pass dry_run=true to preview the diff."
        ),
    )
    def update_script_record(
        script_type: str,
        name_or_sysid: str,
        fields: Optional[Dict[str, Any]] = None,
        script_field: str = "",
        script_find: Optional[str] = None,
        script_replace: Optional[str] = None,
        table: str = "",
        parent: str = "",
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Args:
            script_type: Script type key (see list_script_types).
            name_or_sysid: sys_id, or the record's name / identifier.
            fields: Field values to set (a script field here replaces the whole script).
            script_field: Script field for find/replace (defaults to the type's primary script field).
            script_find: Exact text to find in the script field (use with script_replace).
            script_replace: Replacement text for every occurrence of script_find.
            table: Optional table to disambiguate records sharing a name.
            parent: Optional parent record name or sys_id to disambiguate.
            dry_run: If True, returns the field diff without modifying ServiceNow.
        """
        spec = get_script_type(script_type)
        fields = fields or {}
        if (script_find is None) != (script_replace is None):
            raise ServiceNowError("'script_find' and 'script_replace' must be passed together.", "INVALID_ARGS")

        target_field = ""
        if script_find is not None:
            if not spec.script_fields:
                raise ServiceNowError(f"{spec.label} has no script fields.", "INVALID_ARGS")
            target_field = script_field or spec.script_fields[0]
            if target_field not in spec.script_fields:
                raise ServiceNowError(
                    f"'{target_field}' is not a script field of {spec.key}. Use one of: {list(spec.script_fields)}.",
                    "INVALID_ARGS",
                )
            if target_field in fields:
                raise ServiceNowError(
                    f"Pass '{target_field}' either in fields or via script_find/script_replace, not both.",
                    "INVALID_ARGS",
                )

        payload = _coerce_fields(spec, fields)
        record = _resolve_record(spec, name_or_sysid, table, parent)
        if record.get("sys_policy") == "protected":
            raise ServiceNowError(f"{spec.label} '{name_or_sysid}' cannot be edited.", "PROTECTED_RECORD", PROTECTED_NOTE)

        occurrences = 0
        if target_field:
            current = record.get(target_field) or ""
            occurrences = current.count(script_find) if script_find else 0
            if occurrences == 0:
                raise ServiceNowError(
                    f"'script_find' text was not found in {spec.key}.{target_field}.",
                    code="SCRIPT_MATCH_NOT_FOUND",
                    detail="Fetch the record with get_script_record and copy the text exactly, including whitespace.",
                )
            payload[target_field] = current.replace(script_find, script_replace)

        if not payload:
            raise ServiceNowError("No fields to update were provided.", "INVALID_ARGS")
        _apply_implied(spec, payload, fields)

        sys_id = record["sys_id"]
        diff = compute_diff(record, payload)
        result: Dict[str, Any]
        if dry_run:
            result = {"dry_run": True, "script_type": spec.key, "table": spec.table, "sys_id": sys_id,
                      "diff": diff, "payload": payload}
        elif not diff:
            result = {"success": True, "script_type": spec.key, "sys_id": sys_id,
                      "message": f"No changes; {spec.label} already matches."}
        else:
            res = active_client.update_record(spec.table, sys_id, payload)
            result = {"success": True, "script_type": spec.key, "table": spec.table, "sys_id": sys_id,
                      "changed_fields": sorted(diff.keys()), "record": res}
        if target_field:
            result["script_replacements"] = occurrences
        return result
