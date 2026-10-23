"""
ITOM tool tests against an in-memory fake ServiceNow (tests/fake_servicenow.py). NOT a real instance.
Covers capability gating, live-choice validation, topology / impact walks, unmapped CIs, application service
creation (dry run, confirmation, duplicates, roles, verification), flows, and the CMDB / incident extensions.
"""

import anyio
import pytest

from fake_servicenow import FakeSN
from servicenow_mcp.server import create_server
from snow_itom import (ALL_TOOLS, CMDB_TOOLS, ITOMError, ITOMService, ItomMcpBackend, ItomSNBackend, TOOLS,
                       TOOLS_BY_NAME)

CLASSES = [("cmdb", ""), ("cmdb_ci", "cmdb"), ("cmdb_ci_hardware", "cmdb_ci"), ("cmdb_ci_computer", "cmdb_ci_hardware"),
           ("cmdb_ci_server", "cmdb_ci_computer"), ("cmdb_ci_linux_server", "cmdb_ci_server"),
           ("cmdb_ci_appl", "cmdb_ci"), ("cmdb_ci_database", "cmdb_ci"), ("cmdb_ci_service", "cmdb_ci"),
           ("cmdb_ci_service_business", "cmdb_ci_service"), ("cmdb_ci_service_auto", "cmdb_ci_service"),
           ("cmdb_ci_service_discovered", "cmdb_ci_service_auto")]
OTHER_TABLES = ["cmdb_rel_ci", "cmdb_rel_type", "svc_ci_assoc", "ecc_agent", "discovery_status", "sys_hub_flow",
                "sys_flow_context", "wf_workflow", "wf_workflow_version", "wf_context", "incident"]


def world(roles=("admin",), with_em=False, with_discovery=False):
    sn = FakeSN("admin")
    ids = {}
    for name, sup in CLASSES:
        ids[name] = sn.add("sys_db_object", name=name, label=name.replace("_", " ").title(),
                           super_class=ids.get(sup, ""))["sys_id"]
    for t in OTHER_TABLES + (["em_alert"] if with_em else []) + (["discovery_schedule"] if with_discovery else []):
        sn.add("sys_db_object", name=t, label=t)
    me = sn.add("sys_user", user_name="admin", name="Test Admin", active="true")
    for role in roles:
        r = sn.add("sys_user_role", name=role)
        sn.add("sys_user_has_role", user=me["sys_id"], role=r["sys_id"])
    dep = sn.add("cmdb_rel_type", name="Depends on::Used by")["sys_id"]
    runs = sn.add("cmdb_rel_type", name="Runs on::Runs")["sys_id"]
    w = {"sn": sn, "dep": dep, "runs": runs}
    w["svc"] = sn.add("cmdb_ci_service_business", name="Online Banking", number="BSN0001", operational_status="1")
    w["app"] = sn.add("cmdb_ci_appl", name="Bank App", operational_status="1")
    w["db"] = sn.add("cmdb_ci_database", name="bankdb", operational_status="1")
    w["srv"] = sn.add("cmdb_ci_linux_server", name="lnx01", operational_status="1", ip_address="10.0.0.1")
    w["orphan"] = sn.add("cmdb_ci_linux_server", name="lnx-orphan", operational_status="1")
    w["member"] = sn.add("cmdb_ci_linux_server", name="lnx-member", operational_status="1")
    w["svc2"] = sn.add("cmdb_ci_service_discovered", name="Payments", type="1")
    w["svc2"]["_relations"] = [{"parent": "", "child": w["member"]["sys_id"]}]
    for p, c, t in ((w["svc"], w["app"], dep), (w["app"], w["srv"], runs), (w["app"], w["db"], dep),
                    (w["db"], w["srv"], runs)):
        sn.add("cmdb_rel_ci", parent=p["sys_id"], child=c["sys_id"], type=t)
    sn.add("svc_ci_assoc", service_id=w["svc2"]["sys_id"], ci_id=w["member"]["sys_id"])
    w["inc_ci"] = sn.add("incident", number="INC0001", short_description="Disk full", active="true",
                         cmdb_ci=w["srv"]["sys_id"])
    sn.add("incident", number="INC0002", short_description="Slow", active="true", business_service=w["svc"]["sys_id"])
    sn.add("incident", number="INC0003", short_description="Old", active="false", cmdb_ci=w["srv"]["sys_id"])
    for v, lab in (("1", "Operational"), ("2", "Non-Operational")):
        sn.add("sys_choice", name="cmdb_ci", element="operational_status", value=v, label=lab, inactive="false",
               language="en", sequence="1", dependent_value="")
    for v in ("1 - most critical", "2 - somewhat critical"):
        sn.add("sys_choice", name="cmdb_ci_service", element="busines_criticality", value=v, label=v,
               inactive="false", language="en", sequence="1", dependent_value="")
    for v in ("Production", "Test"):
        sn.add("sys_choice", name="cmdb_ci_service", element="environment", value=v, label=v, inactive="false",
               language="en", sequence="1", dependent_value="")
    for v in ("Starting", "Active", "Completed", "Canceled"):
        sn.add("sys_choice", name="discovery_status", element="state", value=v, label=v, inactive="false",
               language="en", sequence="1", dependent_value="")
    for v, lab in (("COMPLETE", "Complete"), ("ERROR", "Error"), ("WAITING", "Waiting")):
        sn.add("sys_choice", name="sys_flow_context", element="state", value=v, label=lab, inactive="false",
               language="en", sequence="1", dependent_value="")
    return w


@pytest.fixture(autouse=True)
def _no_verify_wait(monkeypatch):
    monkeypatch.setattr(ITOMService, "VERIFY_DELAYS", (0, 0, 0))


def svc(sn):
    return ITOMService(ItomMcpBackend(sn))


def api_writes(sn):
    return [x for x in sn.writes if x[0] in ("api", "create", "update", "delete")]


# ── registry ────────────────────────────────────────────────────────────────
LOW_IMPACT_WRITES = {"acknowledge_alert", "create_cmdb_ci", "update_cmdb_ci"}   # dry_run, no confirm gate


def test_registry_is_consistent():
    assert len(TOOLS) == 22 and len(CMDB_TOOLS) == 4 and len(TOOLS_BY_NAME) == 26
    for spec in ALL_TOOLS:
        assert hasattr(ITOMService, spec.method), spec.name
        if spec.kind == "write":
            names = {p.name for p in spec.params}
            assert "dry_run" in names, spec.name
            assert "confirm" in names or spec.name in LOW_IMPACT_WRITES, spec.name


def test_registered_on_mcp_server_keeping_existing_names():
    tools = {t.name: t for t in anyio.run(create_server().list_tools)}
    for spec in TOOLS:
        assert spec.name in tools
    for legacy in ("list_discovery_schedules", "list_mid_servers", "list_active_events"):
        assert legacy in tools
    assert set(tools["list_active_events"].input_schema["properties"]) == {"severity", "state", "limit"}


def test_unknown_argument_rejected():
    with pytest.raises(ITOMError, match="Unknown argument"):
        svc(world()["sn"]).call("list_mid_servers", {"colour": "red"})


# ── capabilities / plugin gating ───────────────────────────────────────────────
def test_capabilities_report_missing_plugins():
    w = world()
    w["sn"].add("v_plugin", id="com.snc.cmdb", active="active")
    quick = svc(w["sn"]).call("get_itom_capabilities", {})
    assert {f.get("plugin_state") for f in quick["features"] if "plugin" in f} == {"not checked"}
    r = svc(w["sn"]).call("get_itom_capabilities", {"include_plugin_state": True})
    feats = {f["tools"].split(",")[0]: f for f in r["features"]}
    alerts, cmdb = feats["list_active_events"], feats["list_ci_classes"]
    assert cmdb["plugin_state"] == "active" and alerts["plugin_state"] == "not installed"
    assert alerts["available"] is False and alerts["missing_tables"] == ["em_alert"]
    assert "not yet verified live" in alerts["verification"] and cmdb["verification"] == "verified on a live instance"
    assert cmdb["available"] is True
    assert r["mid_servers"] == {"total": 0, "up": 0}
    assert any("Discovery" in f["requires"] for f in r["not_implemented"])
    assert not any("Event Management (com.glide.em)" == f["requires"] for f in r["not_implemented"])
    assert r["your_itom_roles"] == ["admin"]


def test_alerts_and_schedules_need_their_plugins():
    s = svc(world()["sn"])
    with pytest.raises(ITOMError) as e:
        s.call("list_active_events", {})
    assert e.value.code == "PLUGIN_REQUIRED" and "com.glide.em" in e.value.detail
    with pytest.raises(ITOMError) as e:
        s.call("list_discovery_schedules", {})
    assert e.value.code == "PLUGIN_REQUIRED" and "com.snc.discovery" in e.value.detail


def test_plugin_error_surfaces_through_mcp():
    server = create_server(world()["sn"])
    with pytest.raises(Exception) as e:
        anyio.run(server.call_tool, "list_active_events", {})
    assert "not available on this instance" in str(e.value.__cause__ or e.value)


def test_alerts_validate_state_and_severity_live():
    w = world(with_em=True)
    sn = w["sn"]
    for v in ("Open", "Closed"):
        sn.add("sys_choice", name="em_alert", element="state", value=v, label=v, inactive="false", language="en",
               sequence="1", dependent_value="")
    sn.add("sys_choice", name="em_alert", element="severity", value="1", label="Critical", inactive="false",
           language="en", sequence="1", dependent_value="")
    sn.add("em_alert", number="Alert0001", state="Open", severity="1", initial_event_time="2026-10-01 10:00:00")
    sn.add("em_alert", number="Alert0002", state="Closed", severity="1", initial_event_time="2026-10-02 10:00:00")
    r = svc(sn).call("list_active_events", {"severity": "critical"})
    assert [a["number"] for a in r["alerts"]] == ["Alert0001"]
    with pytest.raises(ITOMError, match="not a valid alert state"):
        svc(sn).call("list_active_events", {"state": "Acknowledged"})


def test_discovery_schedules_when_installed():
    w = world(with_discovery=True)
    w["sn"].add("discovery_schedule", name="Nightly", active="true", discover="CIs")
    w["sn"].add("discovery_schedule", name="Off", active="false", discover="CIs")
    assert [s["name"] for s in svc(w["sn"]).call("list_discovery_schedules", {})["schedules"]] == ["Nightly"]


def test_mid_servers_and_discovery_status():
    w = world()
    sn = w["sn"]
    r = svc(sn).call("list_mid_servers", {})
    assert r["count"] == 0 and "MID Server" in r["note"]
    sn.add("ecc_agent", name="mid1", status="Up")
    sn.add("ecc_agent", name="mid2", status="Down")
    assert [m["name"] for m in svc(sn).call("list_mid_servers", {"active_only": True})["mid_servers"]] == ["mid1"]
    sn.add("discovery_status", number="DIS0001", state="Completed", sys_created_on="2026-10-01 10:00:00")
    sn.add("discovery_status", number="DIS0002", state="Active", sys_created_on="2026-10-02 10:00:00")
    assert [x["number"] for x in svc(sn).call("get_discovery_status", {"state": "active"})["statuses"]] == ["DIS0002"]
    assert svc(sn).call("get_discovery_status", {"status": "DIS0001"})["discovery_status"]["state"] == "Completed"
    with pytest.raises(ITOMError, match="not a valid discovery state"):
        svc(sn).call("get_discovery_status", {"state": "Running"})
    with pytest.raises(ITOMError) as e:
        svc(sn).call("get_discovery_status", {"status": "DIS9999"})
    assert e.value.code == "NOT_FOUND"


# ── topology ─────────────────────────────────────────────────────────────────
def test_list_ci_classes_walks_the_hierarchy():
    s = svc(world()["sn"])
    r = s.call("list_ci_classes", {"root": "cmdb_ci_service"})
    assert {c["name"] for c in r["classes"]} == {"cmdb_ci_service", "cmdb_ci_service_business",
                                                 "cmdb_ci_service_auto", "cmdb_ci_service_discovered"}
    assert next(c for c in r["classes"] if c["name"] == "cmdb_ci_service_discovered")["depth"] == 2
    assert [c["name"] for c in s.call("list_ci_classes", {"text": "linux"})["classes"]] == ["cmdb_ci_linux_server"]
    with pytest.raises(ITOMError):
        s.call("list_ci_classes", {"root": "not_a_table"})


def test_service_topology_walks_down_and_reports_truncation():
    w = world()
    r = svc(w["sn"]).call("get_service_topology", {"service": "Online Banking"})
    names = {n["name"]: n["level"] for n in r["nodes"]}
    assert names == {"Bank App": 1, "bankdb": 2, "lnx01": 2}
    assert r["edge_count"] == 4 and r["truncated"] is False
    shallow = svc(w["sn"]).call("get_service_topology", {"service": "BSN0001", "depth": 1})
    assert [n["name"] for n in shallow["nodes"]] == ["Bank App"] and shallow["truncated"] is True
    only_runs = svc(w["sn"]).call("get_service_topology", {"service": "Online Banking",
                                                           "relationship_type": "Depends on::Used by"})
    assert {n["name"] for n in only_runs["nodes"]} == {"Bank App", "bankdb"}


def test_service_topology_includes_service_members_and_content():
    w = world()
    r = svc(w["sn"]).call("get_service_topology", {"service": "Payments", "include_service_content": True})
    assert [(n["name"], n["via"]) for n in r["nodes"]] == [("lnx-member", "svc_ci_assoc")]
    assert r["service_content"]["service"]["service_relations"] == [{"parent": "", "child": w["member"]["sys_id"]}]
    biz = svc(w["sn"]).call("get_service_topology", {"service": "Online Banking", "include_service_content": True})
    assert biz["service_content"]["available"] is False


def test_service_not_found():
    with pytest.raises(ITOMError) as e:
        svc(world()["sn"]).call("get_service_topology", {"service": "Nope"})
    assert e.value.code == "NOT_FOUND"


def test_ci_impact_finds_services_and_open_incidents():
    w = world()
    r = svc(w["sn"]).call("get_ci_impact", {"ci": "lnx01"})
    assert [s["name"] for s in r["impacted_services"]] == ["Online Banking"]
    assert {n["name"] for n in r["upstream_nodes"]} == {"Bank App", "bankdb", "Online Banking"}
    assert sorted(i["number"] for i in r["open_incidents"]) == ["INC0001", "INC0002"]   # INC0003 inactive
    member = svc(w["sn"]).call("get_ci_impact", {"ci": "lnx-member", "include_incidents": False})
    assert [(s["name"], s["found_via"]) for s in member["impacted_services"]] == [("Payments", "svc_ci_assoc")]
    assert "open_incidents" not in member


def test_find_unmapped_cis():
    w = world()
    r = svc(w["sn"]).call("find_unmapped_cis", {"ci_class": "cmdb_ci_server"})
    assert [c["name"] for c in r["unmapped"]] == ["lnx-orphan"] and r["complete"] is True
    loose = svc(w["sn"]).call("find_unmapped_cis", {"ci_class": "cmdb_ci_server", "check_service_membership": False})
    assert sorted(c["name"] for c in loose["unmapped"]) == ["lnx-member", "lnx-orphan"]
    partial = svc(w["sn"]).call("find_unmapped_cis", {"ci_class": "cmdb_ci_server", "scan_limit": 1})
    assert partial["scanned"] == 1 and partial["complete"] is False and "note" in partial
    with pytest.raises(ITOMError, match="not a CMDB CI class"):
        svc(w["sn"]).call("find_unmapped_cis", {"ci_class": "incident"})


# ── application services ─────────────────────────────────────────────────────
REL = [{"child": "Bank App"}, {"parent": "Bank App", "child": "lnx01"}]


def test_create_application_service_dry_run_and_confirmation_write_nothing():
    w = world()
    sn = w["sn"]
    d = svc(sn).call("create_application_service", {"name": "ITOM-MCP-TEST svc", "relations": REL,
                                                     "fields": {"environment": "production"}, "dry_run": True})
    assert d["dry_run"] is True and d["requires_confirmation"] is True
    assert d["payload"]["environment"] == "Production"
    assert d["payload"]["service_relations"] == [{"parent": "", "child": w["app"]["sys_id"]},
                                                 {"parent": w["app"]["sys_id"], "child": w["srv"]["sys_id"]}]
    gate = svc(sn).call("create_application_service", {"name": "ITOM-MCP-TEST svc", "relations": REL})
    assert gate["success"] is False and gate["requires_confirmation"] is True
    assert api_writes(sn) == []


def test_create_application_service_simple_cis_form():
    w = world()
    d = svc(w["sn"]).call("create_application_service", {"name": "ITOM-MCP-TEST svc", "cis": "Bank App, bankdb",
                                                         "relations": [{"parent": "Bank App", "child": "lnx01"}],
                                                         "dry_run": True})
    assert d["payload"]["service_relations"] == [{"parent": "", "child": w["app"]["sys_id"]},
                                                 {"parent": "", "child": w["db"]["sys_id"]},
                                                 {"parent": w["app"]["sys_id"], "child": w["srv"]["sys_id"]}]


def test_create_application_service_creates_and_verifies():
    w = world()
    sn = w["sn"]
    r = svc(sn).call("create_application_service", {"name": "ITOM-MCP-TEST svc", "relations": REL,
                                                     "fields": {"busines_criticality": "1 - most critical",
                                                                "operational_status": "Operational"},
                                                     "confirm": True})
    assert r["success"] is True and r["verified"] is True
    assert r["relations_expected"] == 2 and r["relations_found"] == 2 and r["relations_checked_in"] == "getContent"
    row = sn.by_id(r["sys_id"])
    assert row["sys_class_name"] == "cmdb_ci_service_discovered" and row["operational_status"] == "1"
    assert [x[0] for x in api_writes(sn)] == ["api"]


def test_create_application_service_refuses_duplicates_bad_input_and_missing_roles():
    w = world()
    s = svc(w["sn"])
    with pytest.raises(ITOMError) as e:
        s.call("create_application_service", {"name": "Online Banking", "confirm": True})
    assert e.value.code == "DUPLICATE"   # any service class, not only application services
    with pytest.raises(ITOMError, match="not a valid environment"):
        s.call("create_application_service", {"name": "X", "fields": {"environment": "Moon"}})
    with pytest.raises(ITOMError, match="cannot be set"):
        s.call("create_application_service", {"name": "X", "fields": {"sys_class_name": "cmdb_ci"}})
    with pytest.raises(ITOMError, match="not part of the service"):
        s.call("create_application_service", {"name": "X", "relations": [{"child": "Bank App"},
                                                                         {"parent": "bankdb", "child": "lnx01"}]})
    with pytest.raises(ITOMError, match="entry point"):
        s.call("create_application_service", {"name": "X", "relations": [{"parent": "bankdb", "child": "lnx01"}]})
    with pytest.raises(ITOMError) as e:
        s.call("create_application_service", {"name": "X", "relations": [{"child": "no-such-ci"}]})
    assert e.value.code == "NOT_FOUND"
    with pytest.raises(ITOMError) as e:
        svc(world(roles=("itil",))["sn"]).call("create_application_service", {"name": "X", "confirm": True})
    assert e.value.code == "FORBIDDEN"
    assert api_writes(w["sn"]) == []


def test_create_application_service_reports_dropped_relations():
    w = world()
    sn = w["sn"]
    real = sn._request

    def drops_one(method, path, params=None, json_data=None):   # an instance that skips a CI (e.g. domain rules)
        res = real(method, path, params, json_data)
        for row in sn.table("cmdb_ci_service_discovered"):
            row["_relations"] = [r for r in row.get("_relations", []) if r["child"] != w["srv"]["sys_id"]]
        return res
    sn._request = drops_one
    r = svc(sn).call("create_application_service", {"name": "ITOM-MCP-TEST svc", "relations": REL, "confirm": True})
    assert r["success"] is True and r["verified"] is False
    assert [m["child_name"] for m in r["relations_not_yet_listed"]] == ["lnx01"]


def test_create_application_service_waits_for_async_population():
    w = world()
    sn = w["sn"]
    real, gets = sn._request, []

    def late(method, path, params=None, json_data=None):   # live behaviour: CIs listed only after a few seconds
        res = real(method, path, params, json_data)
        if method == "GET":
            gets.append(path)
            if len(gets) == 1:
                return {"service": {"service_relations": []}}
        return res
    sn._request = late
    r = svc(sn).call("create_application_service", {"name": "ITOM-MCP-TEST svc", "relations": REL, "confirm": True})
    assert r["verified"] is True and len(gets) == 2


def test_create_application_service_falls_back_to_membership():
    w = world()
    sn = w["sn"]
    real = sn._request

    def no_content(method, path, params=None, json_data=None):
        if method == "GET":
            raise RuntimeError("getContent unavailable")
        return real(method, path, params, json_data)
    sn._request = no_content
    r = svc(sn).call("create_application_service", {"name": "ITOM-MCP-TEST svc", "relations": REL, "confirm": True})
    assert r["verified"] is True and r["relations_checked_in"] == "svc_ci_assoc"


def test_write_lock_blocks_api_writes(monkeypatch):
    monkeypatch.setenv("SERVICENOW_WRITE_ENABLED", "false")
    w = world()
    with pytest.raises(ITOMError) as e:
        svc(w["sn"]).call("create_application_service", {"name": "ITOM-MCP-TEST svc", "relations": REL,
                                                          "confirm": True})
    assert e.value.code == "WRITE_DISABLED"
    assert api_writes(w["sn"]) == []


# ── flows ─────────────────────────────────────────────────────────────────────
def flows_world():
    w = world()
    sn = w["sn"]
    w["flow"] = sn.add("sys_hub_flow", name="Notify Host", internal_name="notify_host", type="flow", active="true")
    sn.add("sys_hub_flow", name="Old Flow", internal_name="old_flow", type="flow", active="false")
    sn.add("sys_hub_flow", name="Send Email", internal_name="send_email", type="subflow", active="true")
    wf = sn.add("wf_workflow", name="Change Approval")
    wf["table"] = "change_request"          # set after add(): 'table' is add()'s own parameter
    sn.add("wf_workflow_version", workflow=wf["sys_id"], active="true", published="true")
    sn.add("wf_workflow", name="Draft WF")
    w["ctx_err"] = sn.add("sys_flow_context", name="notify_host", flow=w["flow"]["sys_id"], state="ERROR",
                          error_message="Boom", sys_created_on="2026-10-02 10:00:00")
    sn.add("sys_flow_context", name="notify_host", flow=w["flow"]["sys_id"], state="COMPLETE",
           sys_created_on="2026-10-01 10:00:00")
    w["wf_ctx"] = sn.add("wf_context", name="Change Approval", workflow=wf["sys_id"], state="executing",
                         started="2026-10-03 10:00:00")
    return w


def test_list_flows_by_kind():
    s = svc(flows_world()["sn"])
    assert [f["name"] for f in s.call("list_flows", {})["flows"]] == ["Notify Host"]
    assert [f["name"] for f in s.call("list_flows", {"kind": "subflow"})["flows"]] == ["Send Email"]
    assert [f["name"] for f in s.call("list_flows", {"kind": "workflow"})["workflows"]] == ["Change Approval"]
    everything = s.call("list_flows", {"kind": "all", "active_only": False})
    assert everything["count"] == 5
    with pytest.raises(ITOMError):
        s.call("list_flows", {"kind": "playbook"})


def test_flow_executions_and_detail():
    w = flows_world()
    s = svc(w["sn"])
    r = s.call("list_flow_executions", {"flow": "Notify Host", "state": "error"})
    assert [x["error_message"] for x in r["executions"]] == ["Boom"]
    assert s.call("list_flow_executions", {"flow": "notify_host", "since": "2026-10-02"})["count"] == 1
    assert s.call("list_flow_executions", {"engine": "workflow"})["count"] == 1
    with pytest.raises(ITOMError, match="not a valid execution state"):
        s.call("list_flow_executions", {"state": "Exploded"})
    assert s.call("get_flow_execution", {"execution": w["ctx_err"]["sys_id"]})["engine"] == "flow"
    assert s.call("get_flow_execution", {"execution": w["wf_ctx"]["sys_id"]})["engine"] == "workflow"
    with pytest.raises(ITOMError):
        s.call("get_flow_execution", {"execution": "notify_host"})


# ── agent adapter ─────────────────────────────────────────────────────────────
class _AgentClient:
    """sn_client.SNClient-shaped wrapper around the fake (query/get/create/patch/delete/request)."""
    def __init__(self, sn):
        self.fake, self.instance, self.username = sn, sn.instance_url, sn.username

    def query(self, table, query="", fields="", limit=100, display_value="false", offset=0):
        return self.fake.query_records(table, query, fields, limit, offset, display_value)

    def get(self, table, sys_id, fields="", display_value="false"):
        return self.fake.get_record(table, sys_id, fields, display_value)

    def request(self, method, path, params=None, json=None):
        return self.fake._request(method, path, params, json)


def test_agent_backend_runs_the_same_tools():
    w = world()
    s = ITOMService(ItomSNBackend(_AgentClient(w["sn"])))
    assert [x["name"] for x in s.call("get_ci_impact", {"ci": "lnx01"})["impacted_services"]] == ["Online Banking"]
    r = s.call("create_application_service", {"name": "ITOM-MCP-TEST agent", "relations": REL, "confirm": True})
    assert r["verified"] is True


# ── CMDB / incident extensions (servicenow_mcp tools) ──────────────────────────
def _call(server, tool, args):
    result = anyio.run(server.call_tool, tool, args)
    assert not result.is_error, result
    return result.structured_content.get("result", result.structured_content)


def test_list_ci_relationships_limit_and_type():
    w = world()
    server = create_server(w["sn"])
    r = _call(server, "list_ci_relationships", {"ci_sys_id": w["app"]["sys_id"]})
    assert r["total_relationships"] == 3 and "truncated" not in r
    r = _call(server, "list_ci_relationships", {"ci_sys_id": w["app"]["sys_id"], "relationship_type": "Runs on::Runs"})
    assert len(r["downstream_children"]) == 1 and r["upstream_parents"] == []
    r = _call(server, "list_ci_relationships", {"ci_sys_id": w["app"]["sys_id"], "limit": 1})
    assert r["truncated"] is True


def test_create_ci_relationship_is_verified():
    w = world()
    w["sn"].add("sys_db_object", name="cmdb_ci_x")
    server = create_server(w["sn"])
    r = _call(server, "create_ci_relationship", {"parent_sys_id": w["orphan"]["sys_id"],
                                                 "child_sys_id": w["member"]["sys_id"],
                                                 "type_name_or_sys_id": w["dep"]})
    assert r["success"] is True and r["verified"] is True


def test_cmdb_health_status_reflects_checks():
    w = world()
    r = _call(create_server(w["sn"]), "cmdb_health_dashboard", {})
    assert r["server_metrics"]["servers_missing_ip"] == 2 and r["status"] == "Needs attention"


def test_create_incident_links_ci_and_service_with_duplicate_protection():
    w = world()
    server = create_server(w["sn"])
    r = _call(server, "create_incident", {"short_description": "Disk full", "cmdb_ci": "lnx01"})
    assert r["success"] is False and r["existing_number"] == "INC0001"
    r = _call(server, "create_incident", {"short_description": "CPU high", "cmdb_ci": "lnx01",
                                          "business_service": "Online Banking"})
    assert r["success"] is True
    created = [x for x in w["sn"].writes if x[0] == "create" and x[1] == "incident"][-1][2]
    assert created["cmdb_ci"] == w["srv"]["sys_id"] and created["business_service"] == w["svc"]["sys_id"]
    r = _call(server, "create_incident", {"short_description": "Disk full", "cmdb_ci": "lnx01",
                                          "allow_duplicate": True})
    assert r["success"] is True
    with pytest.raises(Exception) as e:
        anyio.run(server.call_tool, "create_incident", {"short_description": "x", "cmdb_ci": "nope"})
    assert "No CI matches" in str(e.value.__cause__ or e.value)


# ── Event Management: methodology scenarios (a) no plugin (b) plugin (c) other release (d) activated later (e) role ─
def install_em(w, without=(), closed_label="Closed", choices=True):
    """Simulate activating com.glide.em: tables, dictionary (documented layout minus `without`), choices."""
    from snow_itom import schema as S
    sn = w["sn"]
    for t in ("em_event", "em_alert"):
        sn.add("sys_db_object", name=t, label=t)
    for t, cols in (("em_event", S.EVENT_REQUIRED + S.EVENT_OPTIONAL), ("em_alert", S.ALERT_REQUIRED + S.ALERT_OPTIONAL)):
        for c in cols:
            if c not in without:
                sn.add("sys_dictionary", name=t, element=c)
    if choices:
        for v, lab in (("Open", "Open"), ("Reopen", "Reopen"), ("Closed", closed_label)):
            sn.add("sys_choice", name="em_alert", element="state", value=v, label=lab, inactive="false",
                   language="en", sequence="1", dependent_value="")
        for v, lab in (("0", "Clear"), ("1", "Critical"), ("2", "Major"), ("3", "Minor"), ("4", "Warning")):
            sn.add("sys_choice", name="em_alert", element="severity", value=v, label=lab, inactive="false",
                   language="en", sequence="1", dependent_value="")
        for v in ("Ready", "Processing", "Processed", "Error"):
            sn.add("sys_choice", name="em_event", element="state", value=v, label=v, inactive="false",
                   language="en", sequence="1", dependent_value="")
    w["alert"] = sn.add("em_alert", number="Alert0001", state="Open", severity="1", acknowledged="false",
                        description="CPU 99%", source="Nagios", node="lnx01", cmdb_ci=w["srv"]["sys_id"])
    w["alert2"] = sn.add("em_alert", number="Alert0002", state="Open", severity="3", acknowledged="false",
                         description="Disk 80%", source="Nagios", node="bankdb", cmdb_ci=w["db"]["sys_id"],
                         parent=w["alert"]["sys_id"])
    return w


EM_CALLS = [("list_events", {}), ("get_event", {"event": "a" * 32}),
            ("create_event", {"source": "Nagios", "node": "lnx01", "severity": "Critical"}),
            ("get_alert", {"alert": "Alert0001"}), ("acknowledge_alert", {"alert": "Alert0001"}),
            ("close_alert", {"alert": "Alert0001"}), ("list_alert_groups", {}),
            ("create_incident_from_alert", {"alert": "Alert0001"}), ("get_service_health", {"service": "Online Banking"}),
            ("list_active_events", {})]


def test_a_every_event_tool_names_the_plugin_when_missing():
    s = svc(world()["sn"])
    for tool, args in EM_CALLS:
        with pytest.raises(ITOMError) as e:
            s.call(tool, args)
        assert e.value.code == "PLUGIN_REQUIRED" and "com.glide.em" in e.value.detail, tool


def test_d_activating_the_plugin_later_works_without_restart():
    w = world(roles=("admin",))
    server = create_server(w["sn"])           # one long-running server, like the agent's
    with pytest.raises(Exception) as e:
        anyio.run(server.call_tool, "get_alert", {"alert": "Alert0001"})
    assert "com.glide.em" in str(e.value.__cause__ or e.value)
    install_em(w)                              # the user activates Event Management
    assert _call(server, "get_alert", {"alert": "Alert0001"})["alert"]["number"] == "Alert0001"


def test_b_create_event_dry_run_confirm_duplicate_and_verify():
    w = install_em(world())
    s = svc(w["sn"])
    args = {"source": "Nagios", "node": "lnx01", "severity": "critical", "description": "CPU 99%",
            "message_key": "lnx01-cpu", "ci": "lnx01"}
    d = s.call("create_event", {**args, "dry_run": True})
    assert d["dry_run"] and d["payload"]["severity"] == "1" and '"cmdb_ci"' in d["payload"]["additional_info"]
    gate = s.call("create_event", args)
    assert gate["requires_confirmation"] and api_writes(w["sn"]) == []
    r = s.call("create_event", {**args, "confirm": True})
    assert r["success"] and r["verified"]
    w["sn"].by_id(r["sys_id"])["state"] = "Ready"            # still waiting to be processed
    with pytest.raises(ITOMError) as e:
        s.call("create_event", {**args, "confirm": True})
    assert e.value.code == "DUPLICATE"
    assert s.call("create_event", {**args, "confirm": True, "allow_duplicate": True})["success"]
    with pytest.raises(ITOMError, match="not a valid severity"):
        s.call("create_event", {**args, "severity": "Apocalyptic", "dry_run": True})
    assert svc(w["sn"]).call("list_events", {"source": "Nagios"})["count"] == 2
    assert svc(w["sn"]).call("get_event", {"event": r["sys_id"]})["event"]["node"] == "lnx01"


def test_b_alerts_get_acknowledge_close_groups():
    w = install_em(world())
    s = svc(w["sn"])
    g = s.call("get_alert", {"alert": "Alert0001"})
    assert [a["number"] for a in g["secondary_alerts"]] == ["Alert0002"]
    ack = s.call("acknowledge_alert", {"alert": "Alert0001", "work_note": "looking"})
    assert ack["success"] and ack["verified"] and w["sn"].by_id(w["alert"]["sys_id"])["acknowledged"] == "true"
    assert s.call("acknowledge_alert", {"alert": "Alert0001"}).get("no_change") is True
    gate = s.call("close_alert", {"alert": "Alert0002"})
    assert gate["requires_confirmation"]
    closed = s.call("close_alert", {"alert": "Alert0002", "confirm": True})
    assert closed["success"] and w["sn"].by_id(w["alert2"]["sys_id"])["state"] == "Closed"
    w["sn"].by_id(w["alert2"]["sys_id"])["state"] = "Open"
    groups = s.call("list_alert_groups", {})
    assert [(x["number"], x["secondary_count"]) for x in groups["groups"]] == [("Alert0001", 1)]
    assert s.call("list_active_events", {"severity": "critical"})["count"] == 1


def test_b_incident_from_alert_links_both_ways_and_refuses_duplicates():
    w = install_em(world())
    s = svc(w["sn"])
    d = s.call("create_incident_from_alert", {"alert": "Alert0001", "dry_run": True})
    assert d["payload"]["urgency"] == "1" and d["payload"]["cmdb_ci"] == w["srv"]["sys_id"]
    r = s.call("create_incident_from_alert", {"alert": "Alert0001", "confirm": True})
    assert r["success"] and r["verified"] and r["alert_linked"]
    assert w["sn"].by_id(w["alert"]["sys_id"])["incident"] == r["incident_sys_id"]
    w["sn"].by_id(r["incident_sys_id"])["active"] = "true"
    again = s.call("create_incident_from_alert", {"alert": "Alert0001", "confirm": True})
    assert again["duplicate"] is True


def test_b_service_health_uses_worst_open_alert():
    w = install_em(world())
    h = svc(w["sn"]).call("get_service_health", {"service": "Online Banking"})
    assert h["health"] == "Critical" and h["open_alerts"] == 2 and h["by_severity"] == {"Critical": 1, "Minor": 1}
    w["sn"].by_id(w["alert"]["sys_id"])["state"] = "Closed"
    w["sn"].by_id(w["alert2"]["sys_id"])["state"] = "Closed"
    assert svc(w["sn"]).call("get_service_health", {"service": "Online Banking"})["health"].startswith("OK")


def test_c_other_release_layouts():
    # optional field missing: reported, the tool still works
    w = install_em(world(), without=("maintenance",))
    assert svc(w["sn"]).call("get_alert", {"alert": "Alert0001"})["fields_not_on_this_instance"] == {
        "em_alert": ["maintenance"]}
    # a field the tool depends on is missing: clear INCOMPATIBLE_INSTANCE
    w = install_em(world(), without=("parent",))
    with pytest.raises(ITOMError) as e:
        svc(w["sn"]).call("list_alert_groups", {})
    assert e.value.code == "INCOMPATIBLE_INSTANCE" and "parent" in e.value.message
    # renamed state labels are followed (value or label); a missing 'Closed' state is reported, nothing written
    w = install_em(world(), closed_label="Resolved")
    assert svc(w["sn"]).call("close_alert", {"alert": "Alert0001", "confirm": True})["success"]
    w = install_em(world(), choices=False)
    with pytest.raises(ITOMError) as e:
        svc(w["sn"]).call("close_alert", {"alert": "Alert0001", "confirm": True})
    assert e.value.code == "INCOMPATIBLE_INSTANCE"
    assert api_writes(w["sn"]) == []


def test_e_roles_are_named_when_missing():
    w = install_em(world(roles=("itil",)))
    with pytest.raises(ITOMError) as e:
        svc(w["sn"]).call("close_alert", {"alert": "Alert0001", "confirm": True})
    assert e.value.code == "FORBIDDEN" and "evt_mgmt_operator" in e.value.message
    ok = install_em(world(roles=("evt_mgmt_operator",)))
    assert svc(ok["sn"]).call("acknowledge_alert", {"alert": "Alert0001"})["success"]


# ── CMDB CIs through the shared module ─────────────────────────────────────────
def test_cmdb_create_resolves_references_and_refuses_unknown_fields():
    w = world()
    sn = w["sn"]
    loc = sn.add("cmn_location", name="Pune")
    for el in ("name", "ip_address", "os", "location", "serial_number"):
        sn.add("sys_dictionary", name="cmdb_ci_computer" if el == "os" else "cmdb_ci", element=el,
               internal_type="reference" if el == "location" else "string",
               reference="cmn_location" if el == "location" else "")
    s = svc(sn)
    d = s.call("create_cmdb_ci", {"name": "ITOM-TEST-SRV", "sys_class_name": "cmdb_ci_linux_server",
                                  "fields": {"ip_address": "10.0.0.5", "os": "Linux", "location": "Pune"},
                                  "dry_run": True})
    assert d["payload"]["location"] == loc["sys_id"] and "asset record" in d["warnings"][0]
    with pytest.raises(ITOMError, match="do not exist"):
        s.call("create_cmdb_ci", {"name": "X", "sys_class_name": "cmdb_ci_linux_server", "fields": {"colour": "red"}})
    r = s.call("create_cmdb_ci", {"name": "ITOM-TEST-SRV", "sys_class_name": "cmdb_ci_linux_server",
                                  "fields": {"ip_address": "10.0.0.5", "location": "Pune"}})
    assert r["success"] and r["verified"] and sn.by_id(r["sys_id"])["location"] == loc["sys_id"]
    u = s.call("update_cmdb_ci", {"ci_sys_id": r["sys_id"], "fields": {"ip_address": "10.0.0.6"},
                                  "table": "cmdb_ci_linux_server"})
    assert u["verified"] and u["changed_fields"] == ["ip_address"]


def test_cmdb_class_by_label():
    w = world()
    for row in w["sn"].table("sys_db_object"):
        if row.get("name") == "cmdb_ci_linux_server":
            row["label"] = "Linux Server"
    s = svc(w["sn"])
    d = s.call("create_cmdb_ci", {"name": "ITOM-TEST-2", "sys_class_name": "Linux Server", "dry_run": True})
    assert d["class"] == "cmdb_ci_linux_server" and "matched" in d
    with pytest.raises(ITOMError, match="does not exist"):
        s.call("create_cmdb_ci", {"name": "X", "sys_class_name": "Toaster", "dry_run": True})
