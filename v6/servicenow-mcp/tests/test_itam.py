"""
ITAM tool tests against an in-memory fake ServiceNow (tests/fake_servicenow.py). NOT a real instance.
Covers validation, duplicate protection, confirmation gates, role checks, privacy and MCP registration.
"""

import anyio
import pytest

from fake_servicenow import seeded
from servicenow_mcp.server import create_server
from snow_itam import ITAMError, ITAMService, McpClientBackend, TOOLS, TOOLS_BY_NAME


def svc(sn):
    return ITAMService(McpClientBackend(sn))


def creates(sn, table=None):
    return [w for w in sn.writes if w[0] == "create" and (table is None or w[1] == table)]


# ── registry ────────────────────────────────────────────────────────────────
def test_registry_is_consistent():
    assert len(TOOLS) == len(TOOLS_BY_NAME) == 38
    for spec in TOOLS:
        assert hasattr(ITAMService, spec.method), spec.name
        assert spec.kind in ("read", "write")
        schema = spec.json_schema()
        assert set(schema["required"]) == {p.name for p in spec.params if p.required}
        if spec.kind == "write":
            assert "dry_run" in schema["properties"], spec.name


def test_registered_on_mcp_server_with_schemas():
    tools = {t.name: t for t in anyio.run(create_server().list_tools)}
    for spec in TOOLS:
        assert spec.name in tools
    schema = tools["create_asset"].input_schema
    assert schema["required"] == ["model"] and "serial_number" in schema["properties"]


def test_unknown_argument_rejected():
    with pytest.raises(ITAMError, match="Unknown argument"):
        svc(seeded()).call("search_assets", {"colour": "red"})


# ── assets ───────────────────────────────────────────────────────────────────
def test_create_asset_dry_run_resolves_and_writes_nothing():
    sn = seeded()
    r = svc(sn).call("create_asset", {"model": "Dell XPS 13", "serial_number": "SER-100", "stockroom": "Main Stockroom",
                                      "dry_run": True})
    assert r["dry_run"] is True
    p = r["payload"]
    assert p["model"] == "5" * 32 and p["model_category"] == "1" * 32 and p["install_status"] == "6"
    assert p["stockroom"] == "a" * 32 and p["quantity"] == "1"
    assert not creates(sn)


def test_create_asset_writes_and_verifies():
    sn = seeded()
    r = svc(sn).create_asset("Dell XPS 13", serial_number="SER-101", stockroom="Main Stockroom", substatus="available")
    assert r["success"] and r["verified"] and r["table"] == "alm_hardware"
    assert creates(sn, "alm_hardware")[0][2]["substatus"] == "available"


def test_create_asset_duplicate_serial_and_tag():
    sn = seeded()
    sn.add("alm_hardware", serial_number="SER-200", model="5" * 32, asset_tag="P100")
    with pytest.raises(ITAMError) as e:
        svc(sn).create_asset("Dell XPS 13", serial_number="SER-200", stockroom="Main Stockroom")
    assert e.value.code == "DUPLICATE"
    with pytest.raises(ITAMError) as e:
        svc(sn).create_asset("Dell XPS 13", asset_tag="P100", stockroom="Main Stockroom")
    assert e.value.code == "DUPLICATE"


def test_create_asset_links_existing_ci_with_same_serial():
    sn = seeded()
    r = svc(sn).create_asset("Dell XPS 13", serial_number="SER-CI-1", stockroom="Main Stockroom", dry_run=True)
    assert r["payload"]["ci"] == "e" * 32
    assert any("existing CI" in w for w in r["warnings"])


def test_create_asset_validation():
    s = svc(seeded())
    with pytest.raises(ITAMError, match="need a stockroom"):
        s.create_asset("Dell XPS 13", serial_number="X1")
    with pytest.raises(ITAMError, match="Only consumables"):
        s.create_asset("Dell XPS 13", quantity=3, stockroom="Main Stockroom")
    with pytest.raises(ITAMError, match="create_license"):
        s.create_asset("Office 365")
    with pytest.raises(ITAMError, match="not allowed with state"):
        s.create_asset("Dell XPS 13", stockroom="Main Stockroom", substatus="disposed")
    with pytest.raises(ITAMError, match="cannot start as"):
        s.create_asset("Dell XPS 13", state="Retired", substatus="disposed")
    with pytest.raises(ITAMError) as e:
        s.create_asset("No Such Model", stockroom="Main Stockroom")
    assert e.value.code == "NOT_FOUND"


def _hw(sn, **kw):
    base = {"model": "5" * 32, "install_status": "6", "substatus": "available", "stockroom": "a" * 32,
            "serial_number": "SER-300", "asset_tag": "P300", "display_name": "Dell XPS 13"}
    return sn.add("alm_hardware", **{**base, **kw})


def test_lifecycle_validation_and_confirmation():
    sn = seeded()
    a = _hw(sn)
    s = svc(sn)
    with pytest.raises(ITAMError, match="Allowed substates for In maintenance: none"):
        s.update_asset_lifecycle("P300", "In maintenance", "In repair")
    with pytest.raises(ITAMError, match="retire_asset"):
        s.update_asset_lifecycle("P300", "Retired", "disposed")
    r = s.update_asset_lifecycle("P300", "Missing", "lost")
    assert r["requires_confirmation"] and not [w for w in sn.writes if w[0] == "update"]
    r = s.update_asset_lifecycle("P300", "Missing", "lost", confirm=True)
    assert r["success"] and r["verified"] and sn.by_id(a["sys_id"])["install_status"] == "8"


def test_repair_does_not_need_confirmation():
    sn = seeded()
    _hw(sn)
    r = svc(sn).update_asset_lifecycle("SER-300", "In maintenance", work_note="Screen replaced")
    assert r["success"] and r["to_state"] == "In maintenance"
    r = svc(sn).update_asset_lifecycle("SER-300", "In stock", "In repair")   # in-stock repair substate
    assert r["success"] and r["to_state"] == "In stock"


def test_assign_and_return():
    sn = seeded()
    a = _hw(sn)
    s = svc(sn)
    r = s.assign_asset("P300", "assign", user="jdoe")
    assert r["requires_confirmation"]
    r = s.assign_asset("P300", "assign", user="jdoe", confirm=True)
    row = sn.by_id(a["sys_id"])
    assert r["verified"] and row["assigned_to"] == "b" * 32 and row["install_status"] == "1"
    r = s.assign_asset("P300", "unassign", confirm=True)
    row = sn.by_id(a["sys_id"])
    assert row["assigned_to"] == "" and row["install_status"] == "6" and row["substatus"] == "available"


def test_reserve_refused_while_assigned():
    sn = seeded()
    _hw(sn, assigned_to="b" * 32, install_status="1", substatus="")
    with pytest.raises(ITAMError, match="unassign it before reserving"):
        svc(sn).assign_asset("P300", "reserve", user="rroe")


def test_retire_needs_substatus_and_confirm():
    sn = seeded()
    a = _hw(sn)
    s = svc(sn)
    with pytest.raises(ITAMError, match="substatus is required"):
        s.retire_asset("P300", "")
    assert s.retire_asset("P300", "Obsolete")["requires_confirmation"]
    r = s.retire_asset("P300", "Obsolete", disposal_reason="End of life", confirm=True)
    row = sn.by_id(a["sys_id"])
    assert r["verified"] and row["install_status"] == "7" and row["substatus"] == "obsolete" and row["retirement_date"]
    with pytest.raises(ITAMError, match="already Retired"):
        s.retire_asset("P300", "Sold", confirm=True)


def test_update_asset_blocks_workflow_fields_and_gates_owner_change():
    sn = seeded()
    _hw(sn, owned_by="b" * 32)
    s = svc(sn)
    with pytest.raises(ITAMError, match="assign_asset"):
        s.update_asset("P300", {"assigned_to": "jdoe"})
    with pytest.raises(ITAMError, match="cannot be changed"):
        s.update_asset("P300", {"colour": "red"})
    r = s.update_asset("P300", {"owned_by": "rroe", "location": "Pune"})
    assert r["requires_confirmation"] and r["diff"]["owned_by"]["to"] == "c" * 32
    assert s.update_asset("P300", {"location": "Pune"}, confirm=False)["success"]


def test_role_check_blocks_users_without_itam_roles():
    sn = seeded(with_roles=("itil",))
    _hw(sn)
    with pytest.raises(ITAMError) as e:
        svc(sn).update_asset_lifecycle("P300", "In maintenance")
    assert e.value.code == "FORBIDDEN"


def test_role_check_allows_asset_role():
    sn = seeded(with_roles=("asset",))
    _hw(sn)
    assert svc(sn).update_asset_lifecycle("P300", "In maintenance")["success"]


def test_financial_fields_only_on_request():
    sn = seeded()
    _hw(sn, cost="999")
    s = svc(sn)
    assert "cost" not in s.search_assets("hardware")["assets"][0]
    assert s.search_assets("hardware", include_financials=True)["assets"][0]["cost"] == "999"


# ── inventory ────────────────────────────────────────────────────────────────
def test_transfer_creates_draft_order_and_line():
    sn = seeded()
    _hw(sn)
    s = svc(sn)
    assert s.transfer_asset("P300", "Second Stockroom")["requires_confirmation"]
    r = s.transfer_asset("P300", "Second Stockroom", confirm=True)
    assert r["success"] and r["line"]["verified"]
    order = creates(sn, "alm_transfer_order")[0][2]
    assert order["stage"] == "draft" and order["to_stockroom"] == "d" * 32
    with pytest.raises(ITAMError) as e:
        s.transfer_asset("P300", "Second Stockroom", confirm=True)
    assert e.value.code == "DUPLICATE"


def test_transfer_refuses_assets_not_in_stock():
    sn = seeded()
    _hw(sn, install_status="1", substatus="")
    with pytest.raises(ITAMError, match="Only assets 'In stock'"):
        svc(sn).transfer_asset("P300", "Second Stockroom", confirm=True)


def test_stockroom_inventory_and_stock_rules():
    sn = seeded()
    sn.add("alm_consumable", model="6" * 32, quantity="4", install_status="6", substatus="available", stockroom="a" * 32)
    sn.add("alm_stock_rule", model="6" * 32, stockroom="a" * 32, threshold="10", order_size="20", active="true",
           restocking_option="vendor")
    r = svc(sn).get_stockroom_inventory("Main Stockroom")
    assert r["total_units"] == 4
    assert r["stock_rules"][0]["below_threshold"] is True and r["stock_rules"][0]["available_units"] == 4


def test_consumable_quantity_decrease_needs_confirm():
    sn = seeded()
    c = sn.add("alm_consumable", model="6" * 32, quantity="5", install_status="6", stockroom="a" * 32,
               display_name="USB-C Cable", asset_tag="C1")
    s = svc(sn)
    assert s.adjust_consumable_quantity("C1", 3)["requires_confirmation"]
    assert s.adjust_consumable_quantity("C1", 3, confirm=True)["verified"]
    assert sn.by_id(c["sys_id"])["quantity"] == "3"
    with pytest.raises(ITAMError, match="Only consumable"):
        _hw(sn)
        s.adjust_consumable_quantity("P300", 2)


def test_model_and_stockroom_duplicates():
    s = svc(seeded())
    with pytest.raises(ITAMError) as e:
        s.create_asset_model("hardware", "Dell XPS 13", "Dell", model_category="Computer")
    assert e.value.code == "DUPLICATE"
    with pytest.raises(ITAMError, match="creates alm_consumable"):
        s.create_asset_model("hardware", "New One", "Dell", model_category="Consumable")
    with pytest.raises(ITAMError) as e:
        s.create_stockroom("Main Stockroom", "Pune")
    assert e.value.code == "DUPLICATE"


# ── licenses ────────────────────────────────────────────────────────────────
def test_license_allocation_capacity_duplicates_and_deallocation():
    sn = seeded()
    lic = sn.add("alm_license", model="7" * 32, rights="1", display_name="Office 365 - 1 seat", asset_tag="SW1")
    s = svc(sn)
    r = s.allocate_license("SW1", user="jdoe")
    assert r["success"] and creates(sn, "alm_entitlement_user")
    with pytest.raises(ITAMError) as e:
        s.allocate_license("SW1", user="jdoe")
    assert e.value.code == "DUPLICATE"
    over = s.allocate_license("SW1", user="rroe")
    assert over["requires_confirmation"] and "exceeds" in over["reasons"][0]
    summary = s.license_allocation_summary("SW1")
    assert summary["licenses"][0]["allocated"] == 1 and "NOT a software compliance position" in summary["note"]
    assert s.deallocate_license("SW1", user="jdoe")["requires_confirmation"]
    assert s.deallocate_license("SW1", user="jdoe", confirm=True)["verified"]
    assert s.get_license(lic["sys_id"])["allocated_total"] == 0


def test_license_key_is_never_echoed():
    sn = seeded()
    r = svc(sn).create_license("Office 365", 5, license_key="AAAA-BBBB-CCCC", dry_run=True)
    assert r["payload"]["license_key"] == "(hidden)"
    sn.add("alm_license", model="7" * 32, rights="5", display_name="Keyed", license_key="SECRET-1", asset_tag="SW9")
    assert "license_key" not in svc(sn).get_license("SW9")["license"]


# ── contracts ───────────────────────────────────────────────────────────────
def test_contract_lifecycle():
    sn = seeded()
    s = svc(sn)
    r = s.create_contract("Laptop maintenance", "Dell", "Maintenance", "DELL-2026-01", "2026-01-01", "2026-12-31")
    assert r["success"] and creates(sn, "ast_contract")[0][2]["state"] == "draft"
    with pytest.raises(ITAMError) as e:
        s.create_contract("Again", "Dell", "Maintenance", "DELL-2026-01", "2026-01-01", "2026-12-31")
    assert e.value.code == "DUPLICATE"
    with pytest.raises(ITAMError, match="ends is before starts"):
        s.create_contract("Bad", "Dell", "Maintenance", "DELL-X", "2026-12-31", "2026-01-01")
    cid = r["sys_id"]
    assert s.update_contract(cid, {"renewal_date": "2026-11-30"})["success"]
    assert s.update_contract(cid, {"state": "active"})["requires_confirmation"]
    with pytest.raises(ITAMError, match="state must be one of"):
        s.update_contract(cid, {"state": "approved"})
    _hw(sn)
    assert s.link_asset_to_contract(cid, "P300")["success"]
    with pytest.raises(ITAMError) as e:
        s.link_asset_to_contract(cid, "P300")
    assert e.value.code == "DUPLICATE"
    assert s.unlink_asset_from_contract(cid, "P300")["requires_confirmation"]
    assert s.unlink_asset_from_contract(cid, "P300", confirm=True)["verified"]


# ── procurement ─────────────────────────────────────────────────────────────
def test_purchase_order_flow_gates():
    sn = seeded()
    s = svc(sn)
    with pytest.raises(ITAMError, match="needs a quantity"):
        s.create_purchase_order("Dell", "Main Stockroom", [{"model": "Dell XPS 13", "quantity": 0}])
    r = s.create_purchase_order("Dell", "Main Stockroom", [{"model": "Dell XPS 13", "quantity": 2}])
    assert r["success"] and creates(sn, "proc_po")[0][2]["status"] == "requested" and len(r["lines"]) == 1
    po = r["sys_id"]
    assert s.order_purchase_order(po)["requires_confirmation"]
    assert s.order_purchase_order(po, confirm=True)["verified"]
    line = sn.table("proc_po_item")[0]
    with pytest.raises(ITAMError, match="Only ordered lines"):
        s.receive_purchase_order_item(line["sys_id"], 1, confirm=True)
    line.update(status="ordered", remaining_quantity="2", received_quantity="0")
    with pytest.raises(ITAMError, match="between 1 and the remaining 2"):
        s.receive_purchase_order_item(line["sys_id"], 3, confirm=True)
    assert s.receive_purchase_order_item(line["sys_id"], 1)["requires_confirmation"]


def test_procurement_reports_missing_plugin():
    sn = seeded()
    sn.rows = [r for r in sn.rows if not (r["sys_class_name"] == "sys_db_object" and r["name"].startswith("proc_"))]
    with pytest.raises(ITAMError) as e:
        svc(sn).list_purchase_orders()
    assert e.value.code == "PLUGIN_REQUIRED" and "com.snc.procurement" in e.value.detail


# ── reports ─────────────────────────────────────────────────────────────────
def test_reconciliation_and_capabilities():
    sn = seeded()
    _hw(sn)                                              # no CI
    ci = sn.add("cmdb_ci_computer", name="X", serial_number="AAA", asset="")
    _hw(sn, serial_number="BBB", asset_tag="P301", ci=ci["sys_id"])   # serial differs from its CI
    r = svc(sn).asset_ci_reconciliation_report()
    assert r["hardware_assets_without_ci"]["count"] == 1
    assert r["serial_mismatch"]["count"] == 1
    assert r["hardware_cis_without_asset"]["count"] == 2
    cap = svc(sn).get_itam_capabilities()
    assert all(f["available"] for f in cap["features"] if f["plugin"] != "com.sn_itam_workspace")
    assert any("Software Asset Management Professional" in f["requires"] for f in cap["not_implemented"])


# ── hardened CI tools (itam.cis.*) through the MCP server ──────────────────────
def _ci_server():
    sn = seeded()
    hw = next(r for r in sn.rows if r["sys_class_name"] == "sys_db_object" and r["name"] == "cmdb_ci_hardware")
    sn.add("sys_db_object", name="cmdb_ci_computer", super_class=hw["sys_id"])
    sn.add("sys_db_object", name="cmdb_ci_appl")
    sn.add("cmdb_rel_type", sys_id="f" * 32, name="Depends on::Used by")
    return sn, create_server(sn)


def _tool(server, name, args):
    res = anyio.run(server.call_tool, name, args)
    assert not res.is_error, res
    return res.structured_content.get("result", res.structured_content)


def _tool_error(server, name, args):
    with pytest.raises(Exception) as e:
        anyio.run(server.call_tool, name, args)
    return str(e.value.__cause__ or e.value)


def test_create_ci_duplicate_and_hardware_warning():
    sn, server = _ci_server()
    assert "already exists" in _tool_error(server, "create_cmdb_ci", {"name": "PUNE-LT-01", "sys_class_name": "cmdb_ci_computer"})
    assert "serial 'SER-CI-1'" in _tool_error(server, "create_cmdb_ci", {
        "name": "NEW-LT", "sys_class_name": "cmdb_ci_computer", "fields": {"serial_number": "SER-CI-1"}})
    r = _tool(server, "create_cmdb_ci", {"name": "NEW-LT", "sys_class_name": "cmdb_ci_computer", "dry_run": True})
    assert r["dry_run"] and "asset record" in r["warnings"][0]
    r = _tool(server, "create_cmdb_ci", {"name": "billing-app", "sys_class_name": "cmdb_ci_appl"})
    assert r["verified"] and not r["warnings"]


def test_update_ci_dry_run_and_verification():
    sn, server = _ci_server()
    r = _tool(server, "update_cmdb_ci", {"ci_sys_id": "e" * 32, "fields": {"serial_number": "NEW"}, "dry_run": True})
    assert r["diff"]["serial_number"]["to"] == "NEW" and not [w for w in sn.writes if w[0] == "update"]
    r = _tool(server, "update_cmdb_ci", {"ci_sys_id": "e" * 32, "fields": {"serial_number": "NEW"}})
    assert r["verified"] and r["changed_fields"] == ["serial_number"]


def test_ci_relationship_rejects_unknown_type_and_duplicates():
    sn, server = _ci_server()
    other = sn.add("cmdb_ci_appl", name="app")
    assert "not found exactly once" in _tool_error(server, "create_ci_relationship", {
        "parent_sys_id": other["sys_id"], "child_sys_id": "e" * 32, "type_name_or_sys_id": "Uses"})
    args = {"parent_sys_id": other["sys_id"], "child_sys_id": "e" * 32}
    _tool(server, "create_ci_relationship", args)
    assert "already exists" in _tool_error(server, "create_ci_relationship", args)


def test_search_ci_no_longer_hides_non_operational():
    sn, server = _ci_server()
    sn.add("cmdb_ci_computer", name="OLD-LT", operational_status="2")
    names = {c["name"] for c in _tool(server, "search_cmdb_ci", {"sys_class_name": "cmdb_ci_computer"})["cis"]}
    assert {"PUNE-LT-01", "OLD-LT"} <= names


# ── behaviours found in the live run against dev404254 ─────────────────────────
def test_lifecycle_change_kept_back_by_platform_is_a_failure():
    sn = seeded()
    _hw(sn)
    sn.read_only = {"install_status", "substatus"}       # e.g. a business rule restoring the old state
    r = svc(sn).retire_asset("P300", "Obsolete", confirm=True)
    assert r["success"] is False and "did not apply install_status" in r["error"]


def test_open_transfer_blocks_retire_and_assign():
    sn = seeded()
    a = _hw(sn)
    sn.add("alm_transfer_order_line", asset=a["sys_id"], stage="draft", number="TOL001")
    with pytest.raises(ITAMError, match="open transfer order line TOL001"):
        svc(sn).retire_asset("P300", "Obsolete", confirm=True)
    with pytest.raises(ITAMError, match="open transfer order"):
        svc(sn).assign_asset("P300", "assign", user="jdoe", confirm=True)


def test_order_with_read_only_status_returns_action_required():
    sn = seeded()
    sn.add("sys_dictionary", name="proc_po", element="status", read_only="true")
    s = svc(sn)
    po = s.create_purchase_order("Dell", "Main Stockroom", [{"model": "Dell XPS 13", "quantity": 1}])["sys_id"]
    r = s.order_purchase_order(po, confirm=True)
    assert r["action_required"] and r["success"] is False and "click Order" in r["message"]
    assert not [w for w in sn.writes if w[0] == "update" and w[1] == "proc_po"]


def test_loosely_typed_model_name_uses_single_close_match():
    sn = seeded()
    sn.add("cmdb_hardware_product_model", name='MacBook Pro 17"', display_name='Apple MacBook Pro 17"',
           cmdb_model_category="1" * 32, manufacturer="4" * 32, status="In Production")
    r = svc(sn).call("create_asset", {"model": "Apple MacBook Pro 17", "stockroom": "Main Stockroom", "dry_run": True})
    assert r["dry_run"] and "matched" in r and 'MacBook Pro 17"' in r["matched"][0]


def test_ambiguous_close_match_lists_candidates_and_users_stay_exact():
    sn = seeded()
    for n in ('MacBook Pro 15"', 'MacBook Air 13"'):
        sn.add("cmdb_hardware_product_model", name=n, display_name="Apple " + n, cmdb_model_category="1" * 32)
    with pytest.raises(ITAMError) as e:
        svc(sn).create_asset("MacBook", stockroom="Main Stockroom", dry_run=True)
    assert e.value.code == "AMBIGUOUS" and "MacBook Pro 15" in e.value.detail
    _hw(sn)
    with pytest.raises(ITAMError) as e:
        svc(sn).assign_asset("P300", "assign", user="Jane", confirm=True)   # no fuzzy matching for people
    assert e.value.code == "NOT_FOUND"
