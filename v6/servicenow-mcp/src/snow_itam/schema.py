"""
ITAM table, field and feature definitions.

Every table and field here was verified against sys_dictionary of a live instance (Asset Management,
Contract Management and Procurement plugins). Lifecycle states/substates are NOT defined here: they are
read live from sys_choice so they always match the instance.
"""

from typing import Dict, Tuple

# asset_class key -> asset table, product model table(s)
ASSET_CLASSES: Dict[str, Dict[str, str]] = {
    "asset": {"table": "alm_asset", "model_table": "cmdb_model"},
    "hardware": {"table": "alm_hardware", "model_table": "cmdb_hardware_product_model"},
    "consumable": {"table": "alm_consumable", "model_table": "cmdb_consumable_product_model"},
    "license": {"table": "alm_license", "model_table": "cmdb_software_product_model"},
    "facility": {"table": "alm_facility", "model_table": "cmdb_model"},
}
TABLE_TO_CLASS = {v["table"]: k for k, v in ASSET_CLASSES.items()}
MODEL_CLASS_TO_ASSET = {
    "cmdb_hardware_product_model": "hardware",
    "cmdb_consumable_product_model": "consumable",
    "cmdb_software_product_model": "license",
}

ASSET_FIELDS = (
    "sys_id,sys_class_name,asset_tag,display_name,serial_number,model,model_category,install_status,substatus,"
    "quantity,assigned_to,assigned,reserved_for,managed_by,owned_by,company,department,location,stockroom,vendor,"
    "ci,warranty_expiration,install_date,purchase_date,order_date,po_number,acquisition_method,request_line,"
    "retirement_date,disposal_reason,parent,support_group,supported_by,sys_updated_on"
)
# Money and accounting fields: returned only when the caller asks (include_financials=True)
ASSET_FINANCIAL_FIELDS = (
    "cost,salvage_value,resale_price,resold_value,depreciated_amount,residual,residual_date,depreciation,"
    "depreciation_date,gl_account,invoice_number,lease_id,expenditure_type,cost_center"
)
LICENSE_FIELDS = "rights,assigned_condition,entitlement_condition,is_merged_license,merged_into"
LICENSE_SECRET_FIELDS = "license_key"  # product keys: returned only with include_license_key=True

# update_asset: field -> reference table and lookup columns ("" = plain value)
ASSET_UPDATABLE: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "asset_tag": ("", ()), "serial_number": ("", ()), "comments": ("", ()), "work_notes": ("", ()),
    "warranty_expiration": ("", ()), "install_date": ("", ()), "purchase_date": ("", ()), "order_date": ("", ()),
    "po_number": ("", ()), "acquisition_method": ("", ()), "cost": ("", ()),
    "location": ("cmn_location", ("name",)), "department": ("cmn_department", ("name",)),
    "company": ("core_company", ("name",)), "vendor": ("core_company", ("name",)),
    "managed_by": ("sys_user", ("user_name", "email", "name")),
    "owned_by": ("sys_user", ("user_name", "email", "name")),
    "supported_by": ("sys_user", ("user_name", "email", "name")),
    "support_group": ("sys_user_group", ("name",)),
}
# Fields that have a dedicated workflow tool (never changed through update_asset)
ASSET_WORKFLOW_FIELDS = {
    "install_status": "update_asset_lifecycle / retire_asset", "substatus": "update_asset_lifecycle",
    "assigned_to": "assign_asset", "reserved_for": "assign_asset", "stockroom": "transfer_asset",
    "model": "create a new asset (the model defines the asset class)", "ci": "asset_ci_reconciliation_report",
    "quantity": "adjust_consumable_quantity", "retirement_date": "retire_asset", "disposal_reason": "retire_asset",
}
ACQUISITION_METHODS = ("purchase", "lease", "loan", "rental", "daas")

# Lifecycle states whose change needs confirm=true (values are install_status choices)
STATE_RETIRED = "7"
STATE_IN_USE = "1"
STATE_IN_STOCK = "6"
CONSEQUENTIAL_STATES = {"7": "Retired", "8": "Missing", "10": "Consumed"}

CONTRACT_FIELDS = (
    "sys_id,number,short_description,vendor,vendor_contract,contract_model,state,substate,starts,ends,"
    "renewal_date,renewable,has_auto_renewal_clause,notice_date,renewal_notice_period,contract_administrator,"
    "approver,renewal_contact,business_owner,po_number,license_type,license_quantity_entitled,parent_contract,"
    "location,sys_updated_on"
)
CONTRACT_COMMERCIAL_FIELDS = (
    "payment_amount,payment_schedule,total_cost,monthly_cost,yearly_cost,lifetime_cost,cost_per_unit,sub_total_cost,"
    "tax_cost,sales_tax,tax_rate,discount,cost_adjustment,cost_adjustment_percentage,cost_adjustment_type,"
    "late_payment_fees,liability_cap,termination_fees,renewal_cap,cost_center,invoice_payment_terms,"
    "terms_and_conditions"
)
CONTRACT_UPDATABLE: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "short_description": ("", ()), "description": ("", ()), "starts": ("", ()), "ends": ("", ()),
    "renewal_date": ("", ()), "renewable": ("", ()), "has_auto_renewal_clause": ("", ()), "notice_date": ("", ()),
    "renewal_notice_period": ("", ()), "po_number": ("", ()), "vendor_contract": ("", ()),
    "state": ("", ()), "substate": ("", ()), "license_quantity_entitled": ("", ()),
    "contract_administrator": ("sys_user", ("user_name", "email", "name")),
    "approver": ("sys_user", ("user_name", "email", "name")),
    "renewal_contact": ("sys_user", ("user_name", "email", "name")),
    "business_owner": ("sys_user", ("user_name", "email", "name")),
}
CONTRACT_CONSEQUENTIAL = {"state", "substate", "ends"}

PO_FIELDS = (
    "sys_id,number,short_description,status,vendor,vendor_contract,contract,ship_to,location,requested_by,"
    "requested_for,assigned_to,po_date,due_by,expected_delivery,ordered,received,init_request,sys_updated_on"
)
PO_FINANCIAL_FIELDS = "total_cost,ship_rate,budget_number,terms"
PO_LINE_FIELDS = (
    "sys_id,number,purchase_order,model,short_description,status,ordered_quantity,received_quantity,"
    "remaining_quantity,part_number,vendor,requested_for,request_line,expected_delivery,ordered,received"
)
PO_LINE_FINANCIAL_FIELDS = "cost,list_price,discount,total_cost,total_list_price"

# Roles that allow each area of work (admin always allowed). Names verified in sys_user_role.
ROLES = {
    "asset": ["asset", "inventory_admin"],
    "inventory": ["inventory_admin", "asset"],
    "model": ["model_manager", "asset"],
    "license": ["sam", "asset"],
    "contract": ["contract_manager", "asset"],
    "procurement": ["procurement_admin", "procurement_user", "asset"],
}

# Feature -> what it needs. Plugin ids / tables verified; "requires" text is shown to users.
FEATURES = [
    {"feature": "Hardware asset lifecycle (acquire, provision, use, transfer, repair, retire)",
     "plugin": "com.snc.asset_management", "tables": ["alm_hardware"], "tools": "assets / lifecycle"},
    {"feature": "Stockrooms, stock rules and inventory", "plugin": "com.snc.asset_management",
     "tables": ["alm_stockroom", "alm_stock_rule"], "tools": "inventory"},
    {"feature": "Transfer orders", "plugin": "com.snc.asset_management",
     "tables": ["alm_transfer_order", "alm_transfer_order_line"], "tools": "transfer_asset"},
    {"feature": "Consumables", "plugin": "com.snc.asset_management", "tables": ["alm_consumable"],
     "tools": "adjust_consumable_quantity"},
    {"feature": "Product models", "plugin": "com.snc.asset_management", "tables": ["cmdb_model"],
     "tools": "models"},
    {"feature": "Software licenses and allocations (basic)", "plugin": "com.snc.asset_management",
     "tables": ["alm_license", "alm_entitlement"], "tools": "licenses"},
    {"feature": "Contracts, renewals, contract-asset links", "plugin": "com.snc.contract_management",
     "tables": ["ast_contract", "clm_m2m_contract_asset"], "tools": "contracts"},
    {"feature": "Purchase orders and receiving", "plugin": "com.snc.procurement",
     "tables": ["proc_po", "proc_po_item", "proc_rec_slip", "proc_rec_slip_item"], "tools": "procurement"},
    {"feature": "Asset-CI reconciliation (CMDB integration)", "plugin": "com.snc.asset_management",
     "tables": ["alm_hardware", "cmdb_ci_hardware"], "tools": "asset_ci_reconciliation_report"},
    {"feature": "Asset Management Workspace", "plugin": "com.sn_itam_workspace", "tables": [],
     "tools": "itam_overview (same summary through the API)"},
]
# Features this server does not implement. Licensed products are named, not detected: their plugin ids and
# tables could not be verified on an instance without them, so nothing is guessed.
LICENSED_FEATURES = [
    {"feature": "SAM normalization, compliance positions, install reconciliation, SaaS License Management",
     "requires": "Software Asset Management Professional (ITAM SAM subscription)"},
    {"feature": "HAM Pro: disposal orders / e-waste, reclamation, refresh, model lifecycle, attestation",
     "requires": "Hardware Asset Management application (ITAM HAM subscription)"},
    {"feature": "Cloud Asset Management / Cloud Cost Management",
     "requires": "ITOM cloud licensing with Discovery and a MID Server"},
    {"feature": "Financial reporting / cost allocation", "requires": "Cost Management (com.snc.cost_management)",
     "plugin": "com.snc.cost_management"},
]
UI_ONLY_FEATURES = [
    {"feature": "Mobile asset receiving", "plugin": "com.sn_itam_mobile",
     "note": "Mobile app UI; the same actions are available as tools (receive_purchase_order_item, lifecycle)"},
]

NOT_A_COMPLIANCE_POSITION = (
    "Counts of license rights vs allocations recorded in alm_license / alm_entitlement only. This is NOT a "
    "software compliance position: install discovery, normalization and license metrics need SAM Professional."
)
