"""
Tests for the generic script record tools (list/get/create/update_script_record) and the script type registry.
"""

import anyio
import pytest
from servicenow_mcp.server import create_server
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.tools.script_types import SCRIPT_TYPES


class _TableStubClient(ServiceNowClient):
    """In-memory client: answers simple 'field=value' / '^OR' queries over per-table rows and records writes."""

    def __init__(self, tables):
        super().__init__(instance_url="https://stub.service-now.com", username="u", password="p")
        self.tables = tables
        self.writes = []

    @staticmethod
    def _clauses(query):
        """Split an encoded query into AND-clauses, each a list of OR-alternatives (equality only)."""
        clauses = []
        for part in (query or "").split("^"):
            if not part or part.startswith("ORDERBY"):
                continue
            if part.startswith("OR") and clauses:
                clauses[-1].append(part[2:])
            else:
                clauses.append([part])
        return clauses

    def query_records(self, table, query="", fields=None, limit=20, offset=0, display_value="false", order_by=""):
        def matches(row):
            return all(
                any("=" in alt and str(row.get(alt.split("=", 1)[0], "")) == alt.split("=", 1)[1] for alt in clause)
                for clause in self._clauses(query)
            )
        return [dict(r) for r in self.tables.get(table, []) if matches(r)][:limit]

    def create_record(self, table, fields):
        self.writes.append(("create", table, fields))
        return {"sys_id": "f" * 32, **fields}

    def update_record(self, table, sys_id, fields):
        self.writes.append(("update", table, sys_id, fields))
        return {"sys_id": sys_id, **fields}


def _call(server, tool, args):
    result = anyio.run(server.call_tool, tool, args)
    assert not result.is_error, result
    return result.structured_content.get("result", result.structured_content)


def _call_error(server, tool, args) -> str:
    with pytest.raises(Exception) as exc_info:
        anyio.run(server.call_tool, tool, args)
    return str(exc_info.value.__cause__ or exc_info.value)


def _stub():
    return _TableStubClient({
        "sys_user": [{"sys_id": "1" * 32, "user_name": "admin"}],
        "sys_security_operation": [{"sys_id": "2" * 32, "name": "read"}],
        "sys_security_type": [{"sys_id": "3" * 32, "name": "record"}],
        "sys_transform_map": [{"sys_id": "4" * 32, "name": "User Import"}],
        "sys_ui_policy": [{
            "sys_id": "5" * 32, "short_description": "Hide close notes", "table": "incident",
            "run_scripts": "false", "script_true": "function onCondition() {\n  g_form.setDisplay('close_notes', false);\n}",
        }],
        "sys_transform_script": [{"sys_id": "6" * 32, "map": "4" * 32, "when": "onBefore", "script": "ignore = false;"}],
    })


def test_registry_is_consistent():
    for key, spec in SCRIPT_TYPES.items():
        assert spec.key == key
        assert spec.side in ("server", "client", "hybrid")
        for f in (*spec.script_fields, *spec.required, *spec.lookup_fields, *spec.choices, *spec.references):
            assert f in spec.fields or f in ("api_name",), f"{key}: '{f}' not in fields"
        for f, kind in spec.fields.items():
            assert kind in ("str", "bool", "int", "ref"), f"{key}.{f}"
            assert (kind == "ref") == (f in spec.references), f"{key}.{f} reference mismatch"
        if spec.table_field:
            assert spec.table_field in spec.fields
        if spec.parent_field:
            assert spec.parent_field in spec.references
        for trigger, flags in spec.implied.items():
            assert trigger in spec.fields and all(fl in spec.fields for fl in flags)


def test_generic_tools_registered():
    names = {t.name for t in anyio.run(create_server().list_tools)}
    for tool in ("list_script_types", "list_script_records", "get_script_record",
                 "create_script_record", "update_script_record"):
        assert tool in names


def test_list_script_types():
    server = create_server(_stub())
    data = _call(server, "list_script_types", {"side": "client"})
    keys = {t["script_type"] for t in data["script_types"]}
    assert {"client_script", "catalog_client_script", "ui_policy", "ui_script", "validation_script"} <= keys
    detail = _call(server, "list_script_types", {"script_type": "scheduled_job"})
    assert detail["table"] == "sysauto_script"
    assert "daily" in detail["choices"]["run_type"]


def test_create_scheduled_job_resolves_reference():
    stub = _stub()
    server = create_server(stub)
    data = _call(server, "create_script_record", {
        "script_type": "scheduled_job",
        "fields": {"name": "Nightly cleanup", "run_type": "daily", "run_time": "02:00:00",
                   "active": True, "run_as": "admin", "script": "gs.info('cleanup');"},
        "dry_run": True,
    })
    assert data["payload"]["run_as"] == "1" * 32
    assert data["payload"]["active"] == "true"
    assert stub.writes == []


def test_create_acl_resolves_operation_and_sets_advanced():
    stub = _stub()
    server = create_server(stub)
    data = _call(server, "create_script_record", {
        "script_type": "acl",
        "fields": {"name": "incident.close_notes", "operation": "read", "type": "record",
                   "script": "answer = gs.hasRole('itil');"},
    })
    assert data["success"] is True
    _, table, payload = stub.writes[0]
    assert table == "sys_security_acl"
    assert payload["operation"] == "2" * 32 and payload["type"] == "3" * 32
    assert payload["advanced"] == "true"


def test_create_validates_fields_choices_and_required():
    server = create_server(_stub())
    assert "Unknown field" in _call_error(server, "create_script_record", {
        "script_type": "client_script", "fields": {"name": "x", "bogus": 1}})
    assert "Invalid value" in _call_error(server, "create_script_record", {
        "script_type": "client_script",
        "fields": {"name": "x", "table": "incident", "type": "onBlur", "script": "x"}})
    assert "Missing required" in _call_error(server, "create_script_record", {
        "script_type": "client_script", "fields": {"name": "x"}})
    assert "Unknown script_type" in _call_error(server, "create_script_record", {
        "script_type": "nope", "fields": {}})


def test_update_ui_policy_find_replace_implies_run_scripts():
    stub = _stub()
    server = create_server(stub)
    data = _call(server, "update_script_record", {
        "script_type": "ui_policy",
        "name_or_sysid": "Hide close notes",
        "script_field": "script_true",
        "script_find": "false);",
        "script_replace": "true);",
        "dry_run": True,
    })
    assert "setDisplay('close_notes', true);" in data["diff"]["script_true"]["to"]
    assert data["diff"]["run_scripts"] == {"from": "false", "to": "true"}
    assert data["script_replacements"] == 1
    assert stub.writes == []


def test_update_rejects_non_script_field_for_find_replace():
    server = create_server(_stub())
    assert "not a script field" in _call_error(server, "update_script_record", {
        "script_type": "ui_policy", "name_or_sysid": "Hide close notes",
        "script_field": "table", "script_find": "a", "script_replace": "b"})


def test_nameless_type_requires_sys_id_and_lists_by_parent():
    stub = _stub()
    server = create_server(stub)
    assert "pass a sys_id" in _call_error(server, "get_script_record", {
        "script_type": "transform_script", "name_or_sysid": "onBefore"})
    listed = _call(server, "list_script_records", {"script_type": "transform_script", "parent": "User Import"})
    assert listed["count"] == 1
    saved = _call(server, "update_script_record", {
        "script_type": "transform_script", "name_or_sysid": "6" * 32, "fields": {"order": 200}})
    assert stub.writes == [("update", "sys_transform_script", "6" * 32, {"order": "200"})]
    assert saved["changed_fields"] == ["order"]


def test_protected_record_is_flagged_and_not_editable():
    stub = _TableStubClient({"sys_ws_operation": [{"sys_id": "7" * 32, "name": "Get Items", "sys_policy": "protected"}]})
    server = create_server(stub)
    got = _call(server, "get_script_record", {"script_type": "scripted_rest_resource", "name_or_sysid": "Get Items"})
    assert "protected" in got["warning"]
    assert "cannot be edited" in _call_error(server, "update_script_record", {
        "script_type": "scripted_rest_resource", "name_or_sysid": "Get Items", "fields": {"active": False}})
    assert stub.writes == []
