"""
Unit and integration test suite for ServiceNow MCP Server.
Validates tool registration, schema generation, dry-run execution, query validation,
and MCP protocol compliance.
"""

import anyio
import pytest
from servicenow_mcp.server import create_server
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.tools.core import validate_encoded_query, compute_diff
from servicenow_mcp.errors import basic_auth_diagnostic, forbidden_diagnostic


def test_server_creation_and_tool_registration():
    server = create_server()
    tools = anyio.run(server.list_tools)
    tool_names = {t.name for t in tools}

    # Verify key tool categories exist
    expected_sample_tools = [
        "query_records",
        "get_record",
        "create_record",
        "update_record",
        "delete_record",
        "get_table_schema",
        "get_mandatory_fields",
        "validate_query",
        "execute_stats",
        "create_incident",
        "get_incident",
        "list_incidents",
        "update_incident",
        "resolve_incident",
        "close_incident",
        "create_change_request",
        "list_change_requests",
        "create_problem",
        "list_problems",
        "list_tasks",
        "approve_request",
        "search_cmdb_ci",
        "get_cmdb_ci",
        "list_ci_relationships",
        "list_catalog_items",
        "order_catalog_item",
        "search_knowledge",
        "get_user",
        "list_users",
        "get_business_rules",
        "get_client_scripts",
        "get_script_includes",
        "list_atf_suites",
        "get_system_property",
        "list_update_sets",
        "list_mid_servers",
        "snow_check_connection",
    ]

    for tool in expected_sample_tools:
        assert tool in tool_names, f"Expected tool '{tool}' to be registered"

    assert len(tools) >= 70, f"Expected at least 70 tools registered, got {len(tools)}"


def test_resources_registration():
    server = create_server()
    resources = anyio.run(server.list_resources)
    uris = {str(r.uri) for r in resources}
    assert "servicenow://instance/status" in uris
    assert "servicenow://tables/core" in uris


def test_prompts_registration():
    server = create_server()
    prompts = anyio.run(server.list_prompts)
    prompt_names = {p.name for p in prompts}
    assert "investigate_incident" in prompt_names
    assert "create_change_plan" in prompt_names
    assert "generate_business_rule" in prompt_names


def test_validate_encoded_query():
    # Valid query
    r1 = validate_encoded_query("active=true^priority=1^ORDERBYDESCsys_created_on")
    assert r1["valid"] is True
    assert len(r1["issues"]) == 0

    # Invalid query with raw spaces
    r2 = validate_encoded_query("active = true")
    assert r2["valid"] is False
    assert any("unencoded spaces" in iss for iss in r2["issues"])

    # Invalid query using SQL AND instead of ^
    r3 = validate_encoded_query("active=true AND priority=1")
    assert r3["valid"] is False
    assert any("AND" in iss for iss in r3["issues"])


def test_compute_diff():
    before = {"state": "1", "priority": "3", "assigned_to": {"value": "user123"}}
    new_fields = {"state": "2", "priority": "3", "assigned_to": "user456"}
    diff = compute_diff(before, new_fields)

    assert "state" in diff
    assert diff["state"]["from"] == "1"
    assert diff["state"]["to"] == "2"

    assert "priority" not in diff  # Unchanged

    assert "assigned_to" in diff
    assert diff["assigned_to"]["from"] == "user123"
    assert diff["assigned_to"]["to"] == "user456"


def test_dry_run_create():
    server = create_server()
    result = anyio.run(
        server.call_tool,
        "create_record",
        {
            "table": "incident",
            "fields": {"short_description": "Test Dry Run Incident", "urgency": "1"},
            "dry_run": True,
        },
    )
    assert not result.is_error
    data = result.structured_content.get("result", result.structured_content)
    assert data["dry_run"] is True
    assert data["payload"]["short_description"] == "Test Dry Run Incident"


class _StubBusinessRuleClient(ServiceNowClient):
    """Client stub that serves one business rule and records updates."""

    def __init__(self):
        super().__init__(instance_url="https://stub.service-now.com", username="u", password="p")
        self.rule = {
            "sys_id": "a" * 32,
            "name": "Set Priority",
            "collection": "incident",
            "active": "true",
            "when": "before",
            "order": "100",
            "script": "(function executeRule(current, previous) {\n  current.priority = 3;\n})(current, previous);",
        }
        self.updates = []

    def query_records(self, table, query="", fields=None, limit=20, offset=0, display_value="false", order_by=""):
        if table == "sys_script" and (self.rule["sys_id"] in query or f"name={self.rule['name']}" in query):
            return [dict(self.rule)]
        return []

    def update_record(self, table, sys_id, fields):
        self.updates.append((table, sys_id, fields))
        return {**self.rule, **fields}

    def create_record(self, table, fields):
        self.updates.append((table, None, fields))
        return {"sys_id": "b" * 32, **fields}


def _call(server, tool, args):
    result = anyio.run(server.call_tool, tool, args)
    assert not result.is_error, result
    return result.structured_content.get("result", result.structured_content)


def test_business_rule_edit_tools_registered():
    tools = anyio.run(create_server().list_tools)
    names = {t.name for t in tools}
    for tool in ("get_business_rule", "update_business_rule", "set_business_rule_active", "create_business_rule"):
        assert tool in names


def test_update_business_rule_dry_run_find_replace():
    stub = _StubBusinessRuleClient()
    server = create_server(stub)
    data = _call(server, "update_business_rule", {
        "name_or_sysid": "Set Priority",
        "script_find": "current.priority = 3;",
        "script_replace": "current.priority = 2;",
        "order": 50,
        "dry_run": True,
    })
    assert data["dry_run"] is True
    assert "current.priority = 2;" in data["diff"]["script"]["to"]
    assert data["diff"]["order"] == {"from": "100", "to": "50"}
    assert data["script_replacements"] == 1
    assert stub.updates == []


def test_set_business_rule_active_writes(monkeypatch):
    monkeypatch.setenv("SERVICENOW_WRITE_ENABLED", "true")
    stub = _StubBusinessRuleClient()
    server = create_server(stub)
    data = _call(server, "set_business_rule_active", {"name_or_sysid": "a" * 32, "active": False})
    assert data["success"] is True
    assert stub.updates == [("sys_script", "a" * 32, {"active": "false"})]


def test_create_business_rule():
    stub = _StubBusinessRuleClient()
    server = create_server(stub)

    preview = _call(server, "create_business_rule", {
        "name": "Notify On P1", "table": "incident", "when": "after", "action_insert": True, "dry_run": True,
    })
    assert preview["dry_run"] is True
    assert preview["payload"]["collection"] == "incident"
    assert preview["payload"]["action_insert"] == "true"
    assert "warning" not in preview
    assert stub.updates == []

    created = _call(server, "create_business_rule", {
        "name": "Notify On P1", "table": "incident", "action_update": True, "script": "gs.info('hi');",
    })
    assert created["success"] is True
    assert created["sys_id"] == "b" * 32
    assert stub.updates[0][2]["script"] == "gs.info('hi');"


def test_create_business_rule_requires_action():
    server = create_server(_StubBusinessRuleClient())
    with pytest.raises(Exception) as exc_info:
        anyio.run(server.call_tool, "create_business_rule", {"name": "X", "table": "incident"})
    assert "At least one of action_insert" in str(exc_info.value.__cause__)


def test_create_business_rule_warns_on_duplicate():
    server = create_server(_StubBusinessRuleClient())
    data = _call(server, "create_business_rule", {
        "name": "Set Priority", "table": "incident", "action_insert": True, "dry_run": True,
    })
    assert "already exists" in data["warning"]


class _StubScriptIncludeClient(ServiceNowClient):
    """Client stub that serves one script include and records updates."""

    def __init__(self):
        super().__init__(instance_url="https://stub.service-now.com", username="u", password="p")
        self.include = {
            "sys_id": "c" * 32,
            "name": "MyUtils",
            "api_name": "global.MyUtils",
            "active": "true",
            "client_callable": "false",
            "access": "package_private",
            "script": "var MyUtils = Class.create();\nMyUtils.prototype = { limit: 10, type: 'MyUtils' };",
        }
        self.updates = []

    def query_records(self, table, query="", fields=None, limit=20, offset=0, display_value="false", order_by=""):
        if table == "sys_script_include" and "global.MyUtils" in query:
            return [dict(self.include)]
        return []

    def update_record(self, table, sys_id, fields):
        self.updates.append((table, sys_id, fields))
        return {**self.include, **fields}


def test_script_include_edit_tools_registered():
    names = {t.name for t in anyio.run(create_server().list_tools)}
    assert "get_script_include" in names
    assert "update_script_include" in names


def test_update_script_include_find_replace_and_flags():
    stub = _StubScriptIncludeClient()
    server = create_server(stub)

    preview = _call(server, "update_script_include", {
        "name_or_sysid": "global.MyUtils",
        "script_find": "limit: 10",
        "script_replace": "limit: 50",
        "client_callable": True,
        "dry_run": True,
    })
    assert preview["dry_run"] is True
    assert "limit: 50" in preview["diff"]["script"]["to"]
    assert preview["diff"]["client_callable"] == {"from": "false", "to": "true"}
    assert stub.updates == []

    saved = _call(server, "update_script_include", {"name_or_sysid": "global.MyUtils", "access": "public"})
    assert saved["changed_fields"] == ["access"]
    assert stub.updates == [("sys_script_include", "c" * 32, {"access": "public"})]


class _StubFixScriptClient(ServiceNowClient):
    """Client stub that serves one fix script and records writes."""

    def __init__(self):
        super().__init__(instance_url="https://stub.service-now.com", username="u", password="p")
        self.fix = {
            "sys_id": "d" * 32,
            "name": "Backfill Priority",
            "script": "var gr = new GlideRecord('incident');\ngr.setLimit(100);\ngr.query();",
            "record_for_rollback": "true",
            "unloadable": "false",
        }
        self.writes = []

    def query_records(self, table, query="", fields=None, limit=20, offset=0, display_value="false", order_by=""):
        if table == "sys_script_fix" and "Backfill Priority" in query:
            return [dict(self.fix)]
        return []

    def update_record(self, table, sys_id, fields):
        self.writes.append((table, sys_id, fields))
        return {**self.fix, **fields}

    def create_record(self, table, fields):
        self.writes.append((table, None, fields))
        return {"sys_id": "e" * 32, **fields}


def test_fix_script_tools_registered():
    names = {t.name for t in anyio.run(create_server().list_tools)}
    for tool in ("list_fix_scripts", "get_fix_script", "create_fix_script", "update_fix_script",
                 "list_background_script_history"):
        assert tool in names


def test_update_fix_script_find_replace():
    stub = _StubFixScriptClient()
    server = create_server(stub)
    preview = _call(server, "update_fix_script", {
        "name_or_sysid": "Backfill Priority",
        "script_find": "gr.setLimit(100);",
        "script_replace": "gr.setLimit(500);",
        "dry_run": True,
    })
    assert "gr.setLimit(500);" in preview["diff"]["script"]["to"]
    assert preview["script_replacements"] == 1
    assert stub.writes == []

    saved = _call(server, "update_fix_script", {"name_or_sysid": "Backfill Priority", "record_for_rollback": False})
    assert stub.writes == [("sys_script_fix", "d" * 32, {"record_for_rollback": "false"})]
    assert saved["changed_fields"] == ["record_for_rollback"]


def test_create_fix_script():
    stub = _StubFixScriptClient()
    server = create_server(stub)
    created = _call(server, "create_fix_script", {"name": "Cleanup Orphans", "script": "gs.info('x');"})
    assert created["sys_id"] == "e" * 32
    assert "warning" not in created
    assert stub.writes[0][2]["record_for_rollback"] == "true"

    dup = _call(server, "create_fix_script", {"name": "Backfill Priority", "script": "", "dry_run": True})
    assert "already exists" in dup["warning"]


def test_diagnostic_messages():
    b_diag = basic_auth_diagnostic()
    assert "401" in b_diag
    assert "SSO" in b_diag or "Federated" in b_diag

    f_diag = forbidden_diagnostic()
    assert "403" in f_diag


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
