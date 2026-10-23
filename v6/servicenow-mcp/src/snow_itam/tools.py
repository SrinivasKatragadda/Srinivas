"""
The ITAM tool registry: one definition per tool, used by every host.

  * servicenow-mcp (MCPServer) and the Snow Agent MCP server (FastMCP) register `make_function(spec, run)`,
    a function whose signature / annotations / docstring describe the parameters.
  * The Snow Agent's tool catalog uses `spec.json_schema()` and `spec.kind`.
"""

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from snow_itam.errors import ITAMError

REQUIRED = inspect.Parameter.empty


@dataclass(frozen=True)
class Param:
    name: str
    type: Any
    default: Any = REQUIRED
    description: str = ""

    @property
    def required(self) -> bool:
        return self.default is REQUIRED


@dataclass(frozen=True)
class ToolSpec:
    name: str
    method: str
    kind: str          # "read" | "write"
    description: str
    params: Tuple[Param, ...] = field(default_factory=tuple)
    area: str = ""

    def bind(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        known = {p.name for p in self.params}
        unknown = sorted(set(arguments) - known)
        if unknown:
            raise ITAMError(f"Unknown argument(s) for {self.name}: {unknown}", "INVALID_ARGS",
                            f"Allowed: {sorted(known)}")
        out = {}
        for p in self.params:
            if p.name in arguments and arguments[p.name] is not None:
                out[p.name] = _coerce(p, arguments[p.name])
            elif p.required:
                raise ITAMError(f"'{p.name}' is required for {self.name}", "INVALID_ARGS")
        return out

    def json_schema(self) -> Dict[str, Any]:
        props, req = {}, []
        for p in self.params:
            props[p.name] = {**_json_type(p.type), "description": p.description}
            if p.required:
                req.append(p.name)
            elif p.default not in (None, REQUIRED):
                props[p.name]["default"] = p.default
        return {"type": "object", "properties": props, "required": req, "additionalProperties": False}


def _base_type(t: Any) -> Any:
    args = getattr(t, "__args__", None)
    if args and type(None) in args:            # Optional[X]
        return next(a for a in args if a is not type(None))
    return t


def _json_type(t: Any) -> Dict[str, Any]:
    b = _base_type(t)
    origin = getattr(b, "__origin__", None)
    if b is bool:
        return {"type": "boolean"}
    if b is int:
        return {"type": "integer"}
    if b is float:
        return {"type": "number"}
    if origin in (dict, Dict) or b is dict:
        return {"type": "object"}
    if origin in (list, List) or b is list:
        return {"type": "array", "items": {"type": "object"}}
    return {"type": "string"}


def _coerce(p: Param, v: Any) -> Any:
    b = _base_type(p.type)
    try:
        if b is bool:
            return v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "y")
        if b is int:
            return int(v)
        if b is float:
            return float(v)
    except (TypeError, ValueError):
        raise ITAMError(f"'{p.name}' must be {b.__name__}", "INVALID_ARGS")
    if b is str:
        return str(v)
    return v


def make_function(spec: ToolSpec, run: Callable[[str, Dict[str, Any]], Any]) -> Callable:
    """A real Python function for MCP frameworks that build input schemas from signatures."""
    def tool(**kwargs):
        return run(spec.name, {k: v for k, v in kwargs.items()})
    params = [inspect.Parameter(p.name, inspect.Parameter.KEYWORD_ONLY,
                                default=p.default, annotation=p.type) for p in spec.params]
    tool.__signature__ = inspect.Signature(params, return_annotation=Dict[str, Any])
    tool.__annotations__ = {**{p.name: p.type for p in spec.params}, "return": Dict[str, Any]}
    tool.__name__ = spec.name
    tool.__qualname__ = spec.name
    tool.__doc__ = spec.description + "\n\nArgs:\n" + "\n".join(
        f"    {p.name}: {p.description}" for p in spec.params)
    return tool


# ── shared parameter definitions ───────────────────────────────────────────────
def P(name, type_, default=REQUIRED, desc=""):
    return Param(name, type_, default, desc)


ASSET = P("asset", str, desc="Asset sys_id, asset tag or serial number")
DRY = P("dry_run", bool, False, "Preview the change without writing")
CONFIRM = P("confirm", bool, False, "Required true for consequential changes (returned as requires_confirmation)")
LIMIT = P("limit", int, 20, "Maximum rows to return (max 200)")
FIN = P("include_financials", bool, False, "Include cost / accounting fields")

TOOLS: List[ToolSpec] = [
    # ── capabilities & reports ──
    ToolSpec("get_itam_capabilities", "get_itam_capabilities", "read",
             "Show which ITAM features this instance supports (plugins, tables, your roles) and which need "
             "licensed apps (SAM Pro, HAM Pro, Cloud).", (), "reports"),
    ToolSpec("itam_overview", "itam_overview", "read",
             "Asset estate summary: assets by class and state, stockrooms, expiring warranties and contracts, "
             "open transfers and purchase orders, data-quality counts.", (), "reports"),
    ToolSpec("asset_ci_reconciliation_report", "asset_ci_reconciliation_report", "read",
             "Find where asset records and CMDB CIs disagree: assets without CI, CIs without asset, serial / model / "
             "state mismatches (counts and samples).",
             (P("sample_size", int, 10, "Sample rows per check (max 50)"),), "reports"),
    ToolSpec("list_expiring_warranties", "list_expiring_warranties", "read",
             "List hardware assets whose warranty expires within N days.",
             (P("within_days", int, 90, "Window in days from today"),
              P("include_expired", bool, False, "Also include already expired warranties"),
              P("location", str, "", "Location name or sys_id"), P("limit", int, 25, "Maximum rows"),
              P("include_assignee", bool, False, "Include the assigned user")), "reports"),
    # ── assets ──
    ToolSpec("search_assets", "search_assets", "read",
             "Search asset records (alm_asset and its classes) by text, model, lifecycle state, user, stockroom, "
             "location or CI link. Asset records are separate from CMDB CIs.",
             (P("asset_class", str, "asset", "asset | hardware | consumable | license | facility"),
              P("text", str, "", "Matches asset tag, serial number or display name"),
              P("model", str, "", "Model name or sys_id"), P("state", str, "", "Lifecycle state label or value"),
              P("substatus", str, "", "Substate label or value"), P("assigned_to", str, "", "User name, email or sys_id"),
              P("stockroom", str, "", "Stockroom name or sys_id"), P("location", str, "", "Location name or sys_id"),
              P("has_ci", Optional[bool], None, "true = linked to a CI, false = no CI"), LIMIT, FIN), "asset"),
    ToolSpec("get_asset", "get_asset", "read",
             "Get one asset with its linked CI, contracts and (for licenses) allocation count.",
             (ASSET, FIN), "asset"),
    ToolSpec("get_asset_lifecycle_states", "get_asset_lifecycle_states", "read",
             "List the lifecycle states and allowed substates configured on the instance.",
             (P("asset_class", str, "hardware", "asset | hardware | consumable | license | facility"),), "asset"),
    ToolSpec("create_asset", "create_asset", "write",
             "Create a hardware or consumable asset from a product model, with duplicate protection on serial number "
             "and asset tag (assets and CIs). Use create_license for software.",
             (P("model", str, desc="Product model name or sys_id (its category decides the asset class)"),
              P("asset_class", str, "", "Optional check: hardware | consumable"),
              P("serial_number", str, "", "Serial number"), P("asset_tag", str, "", "Asset tag"),
              P("quantity", int, 1, "Units (consumables only)"),
              P("state", str, "In stock", "Initial lifecycle state (In stock, On order, In use, Build ...)"),
              P("substatus", str, "", "Initial substate, e.g. available"),
              P("stockroom", str, "", "Stockroom (required for In stock)"), P("location", str, "", "Location"),
              P("assigned_to", str, "", "User for In use assets"), P("company", str, "", "Company"),
              P("department", str, "", "Department"), P("vendor", str, "", "Vendor company"),
              P("purchase_date", str, "", "YYYY-MM-DD"), P("warranty_expiration", str, "", "YYYY-MM-DD"),
              P("po_number", str, "", "Purchase order number"),
              P("acquisition_method", str, "", "purchase | lease | loan | rental | daas"),
              P("cost", Optional[float], None, "Purchase cost"), P("comments", str, "", "Comments"),
              P("link_existing_ci", bool, True, "Link a CI with the same serial instead of creating a duplicate"),
              DRY), "asset"),
    ToolSpec("update_asset", "update_asset", "write",
             "Update descriptive asset fields (tag, serial, location, department, company, vendor, owner, dates, "
             "PO number, cost). Lifecycle, assignment, stockroom and quantity have their own tools.",
             (ASSET, P("fields", Dict[str, Any], desc="Field -> value; references accept names"), DRY, CONFIRM),
             "asset"),
    ToolSpec("update_asset_lifecycle", "update_asset_lifecycle", "write",
             "Move an asset to a lifecycle state/substate validated against the instance (e.g. In maintenance / "
             "In repair, In stock / Available, Missing / Lost). Retirement uses retire_asset.",
             (ASSET, P("state", str, desc="Target state label or value"), P("substatus", str, "", "Target substate"),
              P("stockroom", str, "", "Stockroom (for In stock)"), P("location", str, "", "Location"),
              P("work_note", str, "", "Work note recorded on the asset"), DRY, CONFIRM), "asset"),
    ToolSpec("assign_asset", "assign_asset", "write",
             "Assign an asset to a user (In use), return it to stock, or reserve / unreserve it for a user.",
             (ASSET, P("action", str, "assign", "assign | unassign | reserve | unreserve"),
              P("user", str, "", "User name, email or sys_id (assign / reserve)"),
              P("stockroom", str, "", "Stockroom when returning to stock"),
              P("substatus", str, "", "Optional substate"), DRY, CONFIRM), "asset"),
    ToolSpec("retire_asset", "retire_asset", "write",
             "Retire an asset with a retirement substate (disposed, sold, donated, vendor credit ...), reason and "
             "date. Always needs confirm=true.",
             (ASSET, P("substatus", str, desc="Retired substate, e.g. disposed, sold, donated, obsolete"),
              P("disposal_reason", str, "", "Why it is retired"), P("retirement_date", str, "", "YYYY-MM-DD, default today"),
              DRY, CONFIRM), "asset"),
    # ── models & inventory ──
    ToolSpec("list_asset_models", "list_asset_models", "read", "List hardware, consumable or software product models.",
             (P("asset_class", str, "", "hardware | consumable | software (empty = all models)"),
              P("name", str, "", "Name / model number contains"), P("manufacturer", str, "", "Manufacturer"), LIMIT),
             "inventory"),
    ToolSpec("create_asset_model", "create_asset_model", "write",
             "Create a product model (needed before creating assets of a new product). Duplicate-checked by name and "
             "manufacturer.",
             (P("asset_class", str, desc="hardware | consumable | software"), P("name", str, desc="Model name"),
              P("manufacturer", str, desc="Manufacturer company name or sys_id"),
              P("model_category", str, "", "Model category (required for hardware / consumable)"),
              P("model_number", str, "", "Model number"), P("short_description", str, "", "Description"), DRY),
             "inventory"),
    ToolSpec("list_stockrooms", "list_stockrooms", "read", "List stockrooms.",
             (P("name", str, "", "Name contains"), P("type", str, "", "Stockroom type"), P("location", str, "", "Location"),
              P("limit", int, 25, "Maximum rows")), "inventory"),
    ToolSpec("create_stockroom", "create_stockroom", "write", "Create a stockroom (duplicate-checked by name).",
             (P("name", str, desc="Stockroom name"), P("location", str, desc="Location name or sys_id"),
              P("type", str, "", "Stockroom type, e.g. Warehouse"), P("manager", str, "", "Manager user"),
              P("assignment_group", str, "", "Assignment group"), DRY), "inventory"),
    ToolSpec("get_stockroom_inventory", "get_stockroom_inventory", "read",
             "Stock levels in a stockroom by model and state, with stock rules and below-threshold flags.",
             (P("stockroom", str, desc="Stockroom name or sys_id"),
              P("include_stock_rules", bool, True, "Include restocking rules")), "inventory"),
    ToolSpec("list_transfer_orders", "list_transfer_orders", "read", "List asset transfer orders.",
             (P("stage", str, "", "draft, requested, shipment_preparation, partially_shipped, fully_shipped, "
                                  "received, delivered, cancelled"),
              P("stockroom", str, "", "From or to this stockroom"), LIMIT), "inventory"),
    ToolSpec("transfer_asset", "transfer_asset", "write",
             "Create a Draft transfer order moving an in-stock asset to another stockroom (ServiceNow's transfer tasks "
             "then handle shipping and receiving).",
             (ASSET, P("to_stockroom", str, desc="Destination stockroom"), P("quantity", int, 1, "Units (consumables)"),
              P("delivery_by_date", str, "", "YYYY-MM-DD"), DRY, CONFIRM), "inventory"),
    ToolSpec("adjust_consumable_quantity", "adjust_consumable_quantity", "write",
             "Set the quantity of a consumable asset record (lowering it needs confirm=true).",
             (ASSET, P("quantity", int, desc="New quantity"), P("work_note", str, "", "Reason"), DRY, CONFIRM),
             "inventory"),
    # ── licenses ──
    ToolSpec("search_licenses", "search_licenses", "read",
             "Search software license records with rights and allocation counts (not a compliance position).",
             (P("text", str, "", "Name or asset tag contains"), P("model", str, "", "Software model"),
              P("vendor", str, "", "Vendor"), LIMIT, P("include_allocation_counts", bool, True, "Count allocations")),
             "license"),
    ToolSpec("get_license", "get_license", "read",
             "Get one software license with rights, allocations to users / CIs and a sample.",
             (P("license", str, desc="License sys_id, asset tag or display name"),
              P("include_license_key", bool, False, "Include the product key"),
              P("sample_size", int, 10, "Allocation sample rows")), "license"),
    ToolSpec("create_license", "create_license", "write",
             "Create a software license record for a software model with a number of rights.",
             (P("model", str, desc="Software product model"), P("rights", int, desc="Number of rights purchased"),
              P("vendor", str, "", "Vendor"),
              P("license_key", str, "", "Product key (never echoed back)"), P("purchase_date", str, "", "YYYY-MM-DD"),
              P("po_number", str, "", "PO number"), DRY), "license"),
    ToolSpec("list_license_allocations", "list_license_allocations", "read", "List who / what a license is allocated to.",
             (P("license", str, desc="License sys_id, asset tag or display name"), P("limit", int, 25, "Maximum rows")),
             "license"),
    ToolSpec("allocate_license", "allocate_license", "write",
             "Allocate a license to a user or a CI (allocating beyond the recorded rights needs confirm=true).",
             (P("license", str, desc="License"), P("user", str, "", "User"), P("ci", str, "", "CI name, serial or sys_id"),
              DRY, CONFIRM), "license"),
    ToolSpec("deallocate_license", "deallocate_license", "write",
             "Remove a license allocation (always needs confirm=true).",
             (P("license", str, "", "License"), P("user", str, "", "User"), P("ci", str, "", "CI"),
              P("allocation_sys_id", str, "", "Allocation sys_id"), DRY, CONFIRM), "license"),
    ToolSpec("license_allocation_summary", "license_allocation_summary", "read",
             "Rights vs allocations per license (counts only — not a compliance position).",
             (P("license", str, "", "One license, or empty for the first N"), P("limit", int, 25, "Licenses to include")),
             "license"),
    # ── contracts ──
    ToolSpec("search_contracts", "search_contracts", "read",
             "Search contracts by text, vendor, state, or those expiring within N days (renewals).",
             (P("text", str, "", "Number, description or vendor contract contains"), P("vendor", str, "", "Vendor"),
              P("state", str, "", "draft | active | expired | cancelled"),
              P("expiring_within_days", Optional[int], None, "Active contracts ending within N days"), LIMIT, FIN),
             "contract"),
    ToolSpec("get_contract", "get_contract", "read", "Get one contract with the assets it covers.",
             (P("contract", str, desc="Contract sys_id, number or vendor contract number"),
              P("include_assets", bool, True, "List covered assets"), FIN,
              P("asset_limit", int, 25, "Maximum covered assets")), "contract"),
    ToolSpec("create_contract", "create_contract", "write",
             "Create a Draft contract (duplicate-checked by vendor + vendor contract number).",
             (P("short_description", str, desc="Title"), P("vendor", str, desc="Vendor company"),
              P("contract_model", str, desc="Contract model (e.g. Lease, Maintenance, Software License)"),
              P("vendor_contract", str, desc="Vendor's contract number"), P("starts", str, desc="YYYY-MM-DD"),
              P("ends", str, desc="YYYY-MM-DD"), P("contract_administrator", str, "", "User"),
              P("approver", str, "", "User"), P("renewal_date", str, "", "YYYY-MM-DD"),
              P("description", str, "", "Description"), DRY), "contract"),
    ToolSpec("update_contract", "update_contract", "write",
             "Update contract dates, renewal details, contacts or state (state / end date changes need confirm=true).",
             (P("contract", str, desc="Contract"), P("fields", Dict[str, Any], desc="Field -> value"), DRY, CONFIRM),
             "contract"),
    ToolSpec("link_asset_to_contract", "link_asset_to_contract", "write", "Add an asset to a contract's coverage.",
             (P("contract", str, desc="Contract"), ASSET, DRY), "contract"),
    ToolSpec("unlink_asset_from_contract", "unlink_asset_from_contract", "write",
             "Remove an asset from a contract (needs confirm=true).",
             (P("contract", str, desc="Contract"), ASSET, DRY, CONFIRM), "contract"),
    # ── procurement ──
    ToolSpec("list_purchase_orders", "list_purchase_orders", "read", "List purchase orders (Procurement plugin).",
             (P("status", str, "", "requested | ordered | pending | received | canceled | suspended"),
              P("vendor", str, "", "Vendor"), P("text", str, "", "Number or description contains"), LIMIT, FIN),
             "procurement"),
    ToolSpec("get_purchase_order", "get_purchase_order", "read", "Get a purchase order with its lines and receipts.",
             (P("po", str, desc="Purchase order sys_id or number"), FIN), "procurement"),
    ToolSpec("create_purchase_order", "create_purchase_order", "write",
             "Create a purchase order in status Requested with lines [{model, quantity, cost?, short_description?}]. "
             "Nothing is ordered until order_purchase_order.",
             (P("vendor", str, desc="Vendor"), P("ship_to", str, desc="Receiving stockroom"),
              P("lines", List[Dict[str, Any]], desc="Order lines"), P("short_description", str, "", "Title"),
              P("requested_for", str, "", "User"), P("due_by", str, "", "YYYY-MM-DD"), DRY), "procurement"),
    ToolSpec("order_purchase_order", "order_purchase_order", "write",
             "Place a Requested purchase order (status Ordered). Needs confirm=true. Where the instance makes the "
             "status read-only to the API, it returns the link to use ServiceNow's Order button instead.",
             (P("po", str, desc="Purchase order"), DRY, CONFIRM), "procurement"),
    ToolSpec("receive_purchase_order_item", "receive_purchase_order_item", "write",
             "Receive units of an ordered purchase order line into stock; ServiceNow creates the asset records. "
             "Needs confirm=true.",
             (P("po_line", str, desc="Purchase order line sys_id or number"), P("quantity", int, desc="Units received"),
              P("stockroom", str, "", "Receiving stockroom (default: the order's ship-to)"), DRY, CONFIRM),
             "procurement"),
]

TOOLS_BY_NAME: Dict[str, ToolSpec] = {t.name: t for t in TOOLS}
assert len(TOOLS_BY_NAME) == len(TOOLS), "duplicate ITAM tool names"
