"""
Registry of ServiceNow script record types (server-side, client-side, and hybrid).
Each entry describes the backing table, editable fields, script fields, choice values,
and reference lookups used by the generic script record tools.
Field names were verified against sys_dictionary on a live instance.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# Field kinds: "str", "bool", "int", "ref" (reference resolved by display value when not a sys_id).


@dataclass(frozen=True)
class ScriptType:
    key: str
    label: str
    table: str
    side: str  # "server", "client", or "hybrid"
    description: str
    fields: Dict[str, str]
    script_fields: Tuple[str, ...]
    lookup_fields: Tuple[str, ...] = ("name",)
    table_field: str = ""
    parent_field: str = ""
    required: Tuple[str, ...] = ()
    choices: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    references: Dict[str, Tuple[str, str]] = field(default_factory=dict)
    # Setting a key field also sets these flags (unless the caller sets them explicitly).
    implied: Dict[str, Dict[str, str]] = field(default_factory=dict)
    notes: str = ""

    def _read_only_lookups(self) -> List[str]:
        return [f for f in self.lookup_fields if f not in self.fields]

    def record_fields(self) -> str:
        names = ["sys_id", *self.fields.keys(), *self._read_only_lookups()]
        return ",".join([*names, "sys_policy", "sys_scope", "sys_updated_on", "sys_updated_by"])

    def summary_fields(self) -> str:
        """Fields for list views: everything except (potentially large) script bodies."""
        names = [f for f in self.fields if f not in self.script_fields]
        return ",".join(["sys_id", *names, *self._read_only_lookups(), "sys_scope", "sys_updated_on"])


def _b(*names: str) -> Dict[str, str]:
    return {n: "bool" for n in names}


def _s(*names: str) -> Dict[str, str]:
    return {n: "str" for n in names}


def _i(*names: str) -> Dict[str, str]:
    return {n: "int" for n in names}


TRISTATE = ("true", "false", "ignore")
UI_TYPE_NOTE = "ui_type: 0=Desktop, 1=Mobile/Service Portal, 10=All."

_TYPES: List[ScriptType] = [
    # ── Server-side ──────────────────────────────────────────────────────────
    ScriptType(
        key="business_rule", label="Business Rule", table="sys_script", side="server",
        description="Runs on insert/update/delete/query (before, after, async, display).",
        fields={**_s("name", "collection", "when", "filter_condition", "condition", "script", "description"),
                **_b("active", "advanced", "action_insert", "action_update", "action_delete", "action_query"),
                **_i("order", "priority")},
        script_fields=("script", "condition"), table_field="collection",
        required=("name", "collection"),
        choices={"when": ("before", "after", "async", "display")},
        implied={"script": {"advanced": "true"}, "condition": {"advanced": "true"}},
        notes="A 'query' business rule is when=before with action_query=true.",
    ),
    ScriptType(
        key="script_include", label="Script Include", table="sys_script_include", side="server",
        description="Reusable server-side classes/functions; client_callable ones are reachable via GlideAjax.",
        fields={**_s("name", "script", "access", "description"), **_b("active", "client_callable")},
        script_fields=("script",), lookup_fields=("name", "api_name"),
        required=("name", "script"),
        choices={"access": ("public", "package_private")},
        notes="The class name in the script must match 'name'. Client-callable classes extend AbstractAjaxProcessor.",
    ),
    ScriptType(
        key="scheduled_job", label="Scheduled Script Execution", table="sysauto_script", side="server",
        description="Time-based background job (daily, weekly, monthly, periodically, once, on demand).",
        fields={**_s("name", "run_type", "run_time", "run_period", "run_start", "condition", "script", "time_zone"),
                **_b("active", "conditional"), **_i("run_dayofweek", "run_dayofmonth"), "run_as": "ref"},
        script_fields=("script", "condition"),
        required=("name", "run_type", "script"),
        choices={"run_type": ("daily", "weekly", "monthly", "periodically", "once", "on_demand",
                              "week_in_month", "day_and_month_in_year", "day_week_month_year",
                              "business_calendar_start", "business_calendar_end")},
        references={"run_as": ("sys_user", "user_name")},
        notes=("run_time is 'HH:MM:SS' (e.g. '02:00:00'); run_period is a duration like '1970-01-01 01:00:00' "
               "for 1 hour; run_start is 'YYYY-MM-DD HH:MM:SS'; run_dayofweek 1=Monday..7=Sunday; "
               "set conditional=true to use the condition script."),
    ),
    ScriptType(
        key="acl", label="Access Control (ACL)", table="sys_security_acl", side="server",
        description="Row/field-level security rule; the script sets answer = true/false.",
        fields={**_s("name", "condition", "script", "description", "decision_type"),
                **_b("active", "admin_overrides", "advanced"), "operation": "ref", "type": "ref"},
        script_fields=("script",),
        required=("name", "operation", "type"),
        choices={"decision_type": ("allow", "deny")},
        references={"operation": ("sys_security_operation", "name"), "type": ("sys_security_type", "name")},
        implied={"script": {"advanced": "true"}},
        notes=("name is 'table' for row-level or 'table.field' for field-level ('table.*' for all fields). "
               "operation e.g. read/write/create/delete; type usually 'record'. Writing ACLs requires the "
               "security_admin role to be elevated for the API user, otherwise ServiceNow rejects the change."),
    ),
    ScriptType(
        key="transform_map", label="Transform Map", table="sys_transform_map", side="server",
        description="Maps an import set staging table to a target table; can run an onTransform script.",
        fields={**_s("name", "source_table", "target_table", "script", "enforce_mandatory_fields"),
                **_b("active", "run_business_rules", "run_script", "copy_empty_fields"), **_i("order")},
        script_fields=("script",), table_field="target_table",
        required=("name", "source_table", "target_table"),
        implied={"script": {"run_script": "true"}},
        notes="Set run_script=true for the transform map 'script' to execute.",
    ),
    ScriptType(
        key="transform_script", label="Transform Script", table="sys_transform_script", side="server",
        description="Transform event script (onBefore, onAfter, onStart, onComplete, onChoiceCreate, onForeignInsert, onReject).",
        fields={**_s("when", "script"), **_b("active"), **_i("order"), "map": "ref"},
        script_fields=("script",), lookup_fields=(), parent_field="map",
        required=("map", "when", "script"),
        choices={"when": ("onBefore", "onAfter", "onStart", "onComplete", "onChoiceCreate",
                          "onForeignInsert", "onReject")},
        references={"map": ("sys_transform_map", "name")},
        notes="Transform scripts have no name; look them up by sys_id or list them by parent transform map.",
    ),
    ScriptType(
        key="transform_field_map", label="Transform Field Map", table="sys_transform_entry", side="server",
        description="Single source-to-target field mapping, optionally computed by a source script.",
        fields={**_s("source_field", "target_field", "source_script", "choice_action", "date_format"),
                **_b("coalesce", "use_source_script"), "map": "ref"},
        script_fields=("source_script",), lookup_fields=("target_field",), parent_field="map",
        required=("map", "target_field"),
        choices={"choice_action": ("create", "ignore", "reject")},
        references={"map": ("sys_transform_map", "name")},
        implied={"source_script": {"use_source_script": "true"}},
        notes="Set use_source_script=true so source_script computes the value (answer = ...).",
    ),
    ScriptType(
        key="script_action", label="Script Action", table="sysevent_script_action", side="server",
        description="Server script triggered by an event in the event queue.",
        fields={**_s("name", "event_name", "condition_script", "script", "description"),
                **_b("active"), **_i("order")},
        script_fields=("script", "condition_script"),
        required=("name", "event_name", "script"),
    ),
    ScriptType(
        key="fix_script", label="Fix Script", table="sys_script_fix", side="server",
        description="Ad-hoc / deployment script for bulk data fixes and migrations.",
        fields={**_s("name", "script", "description"), **_b("record_for_rollback", "unloadable", "before")},
        script_fields=("script",),
        required=("name", "script"),
    ),
    ScriptType(
        key="scripted_rest_api", label="Scripted REST API", table="sys_ws_definition", side="server",
        description="Custom inbound REST service definition (container for resources).",
        fields={**_s("name", "service_id", "short_description", "produces", "consumes", "default_version"),
                **_b("active")},
        script_fields=(), lookup_fields=("name", "service_id"),
        required=("name", "service_id"),
        notes="service_id becomes part of the base URI: /api/<scope>/<service_id>.",
    ),
    ScriptType(
        key="scripted_rest_resource", label="Scripted REST Resource", table="sys_ws_operation", side="server",
        description="An HTTP method + path on a Scripted REST API, implemented by operation_script.",
        fields={**_s("name", "http_method", "relative_path", "operation_script", "short_description",
                     "produces", "consumes"),
                **_b("active", "requires_authentication", "requires_acl_authorization"),
                "web_service_definition": "ref"},
        script_fields=("operation_script",), parent_field="web_service_definition",
        required=("name", "web_service_definition", "http_method", "relative_path", "operation_script"),
        choices={"http_method": ("GET", "POST", "PUT", "PATCH", "DELETE")},
        references={"web_service_definition": ("sys_ws_definition", "name")},
        notes="operation_script: (function process(request, response) { ... })(request, response);",
    ),
    ScriptType(
        key="inbound_email_action", label="Inbound Email Action", table="sysevent_in_email_action", side="server",
        description="Parses inbound email (new, reply, forward) to create or update records.",
        fields={**_s("name", "table", "type", "action", "condition_script", "filter_condition", "script",
                     "description", "event_name"),
                **_b("active", "stop_processing"), **_i("order")},
        script_fields=("script", "condition_script"), table_field="table",
        required=("name", "table", "type"),
        choices={"type": ("new", "reply", "forward"), "action": ("record_action", "reply_email")},
    ),
    ScriptType(
        key="record_producer", label="Record Producer", table="sc_cat_item_producer", side="server",
        description="Catalog item that creates a task record; script maps producer.<variable> to current.",
        fields={**_s("name", "short_description", "table_name", "script", "post_insert_script", "redirect_url"),
                **_b("active")},
        script_fields=("script", "post_insert_script"), table_field="table_name",
        required=("name", "table_name"),
        notes="Assign catalogs/categories (sc_catalogs, category) with update_record if needed.",
    ),
    ScriptType(
        key="data_policy", label="Data Policy", table="sys_data_policy2", side="server",
        description="Enforces mandatory/read-only fields at the database layer (no script). Rules live in data_policy_rule.",
        fields={**_s("short_description", "model_table", "conditions", "description"),
                **_b("active", "apply_import_set", "apply_soap", "enforce_ui", "inherit", "reverse_if_false")},
        script_fields=(), lookup_fields=("short_description",), table_field="model_table",
        required=("short_description", "model_table"),
    ),
    ScriptType(
        key="data_policy_rule", label="Data Policy Rule", table="sys_data_policy_rule", side="server",
        description="A field rule inside a data policy (mandatory / read-only).",
        fields={**_s("field", "table", "mandatory", "disabled"), "sys_data_policy": "ref"},
        script_fields=(), lookup_fields=("field",), parent_field="sys_data_policy",
        required=("sys_data_policy", "field", "table"),
        choices={"mandatory": TRISTATE, "disabled": TRISTATE},
        references={"sys_data_policy": ("sys_data_policy2", "short_description")},
        notes="disabled=true means read-only. 'ignore' leaves the state unchanged.",
    ),
    # ── Client-side ──────────────────────────────────────────────────────────
    ScriptType(
        key="client_script", label="Client Script", table="sys_script_client", side="client",
        description="Form/list browser script: onLoad, onChange, onSubmit, onCellEdit.",
        fields={**_s("name", "table", "type", "field", "script", "condition", "description", "view", "messages"),
                **_b("active", "isolate_script", "applies_extended", "global"), **_i("ui_type", "order")},
        script_fields=("script",), table_field="table",
        required=("name", "table", "type", "script"),
        choices={"type": ("onLoad", "onChange", "onSubmit", "onCellEdit")},
        notes=f"onChange and onCellEdit require 'field'. {UI_TYPE_NOTE}",
    ),
    ScriptType(
        key="catalog_client_script", label="Catalog Client Script", table="catalog_script_client", side="client",
        description="Client script for a catalog item, record producer, or variable set.",
        fields={**_s("name", "type", "applies_to", "cat_variable", "script", "description"),
                **_b("active", "isolate_script", "applies_catalog", "applies_req_item", "applies_sc_task",
                     "applies_target_record"),
                **_i("ui_type"), "cat_item": "ref", "variable_set": "ref"},
        script_fields=("script",), parent_field="cat_item",
        required=("name", "type", "script"),
        choices={"type": ("onLoad", "onChange", "onSubmit"), "applies_to": ("item", "set")},
        references={"cat_item": ("sc_cat_item", "name"), "variable_set": ("item_option_new_set", "title")},
        notes=(f"Set applies_to='item' with cat_item, or 'set' with variable_set. onChange needs "
               f"cat_variable as 'IO:<variable sys_id>'. {UI_TYPE_NOTE}"),
    ),
    ScriptType(
        key="ui_policy", label="UI Policy", table="sys_ui_policy", side="client",
        description="Condition-based mandatory/visible/read-only field states, with optional scripts.",
        fields={**_s("short_description", "table", "conditions", "script_true", "script_false", "description"),
                **_b("active", "on_load", "reverse_if_false", "run_scripts", "global", "inherit", "isolate_script"),
                **_i("order", "ui_type")},
        script_fields=("script_true", "script_false"), lookup_fields=("short_description",), table_field="table",
        required=("short_description", "table"),
        implied={"script_true": {"run_scripts": "true"}, "script_false": {"run_scripts": "true"}},
        notes=f"Scripts run only when run_scripts=true. Field rules live in ui_policy_action. {UI_TYPE_NOTE}",
    ),
    ScriptType(
        key="ui_policy_action", label="UI Policy Action", table="sys_ui_policy_action", side="client",
        description="A field rule inside a UI policy (visible / mandatory / read-only / cleared).",
        fields={**_s("field", "table", "visible", "mandatory", "disabled"), **_b("cleared"), "ui_policy": "ref"},
        script_fields=(), lookup_fields=("field",), parent_field="ui_policy",
        required=("ui_policy", "field", "table"),
        choices={"visible": TRISTATE, "mandatory": TRISTATE, "disabled": TRISTATE},
        references={"ui_policy": ("sys_ui_policy", "short_description")},
        notes="disabled=true means read-only. 'ignore' leaves the state unchanged.",
    ),
    ScriptType(
        key="catalog_ui_policy", label="Catalog UI Policy", table="catalog_ui_policy", side="client",
        description="Controls visibility/mandatory/read-only of catalog variables based on selections.",
        fields={**_s("short_description", "applies_to", "catalog_conditions", "script_true", "script_false",
                     "description"),
                **_b("active", "on_load", "reverse_if_false", "run_scripts", "applies_catalog", "applies_req_item",
                     "applies_sc_task", "applies_target_record"),
                **_i("order"), "catalog_item": "ref", "variable_set": "ref"},
        script_fields=("script_true", "script_false"), lookup_fields=("short_description",),
        parent_field="catalog_item",
        required=("short_description",),
        implied={"script_true": {"run_scripts": "true"}, "script_false": {"run_scripts": "true"}},
        choices={"applies_to": ("item", "set")},
        references={"catalog_item": ("sc_cat_item", "name"), "variable_set": ("item_option_new_set", "title")},
        notes="Variable rules live in catalog_ui_policy_action.",
    ),
    ScriptType(
        key="catalog_ui_policy_action", label="Catalog UI Policy Action", table="catalog_ui_policy_action",
        side="client",
        description="A variable rule inside a catalog UI policy.",
        fields={**_s("catalog_variable", "visible", "mandatory", "disabled"), **_b("cleared"), **_i("order"),
                "ui_policy": "ref", "catalog_item": "ref"},
        script_fields=(), lookup_fields=("catalog_variable",), parent_field="ui_policy",
        required=("ui_policy", "catalog_variable"),
        choices={"visible": TRISTATE, "mandatory": TRISTATE, "disabled": TRISTATE},
        references={"ui_policy": ("catalog_ui_policy", "short_description"), "catalog_item": ("sc_cat_item", "name")},
        notes="catalog_variable is 'IO:<variable sys_id>'.",
    ),
    ScriptType(
        key="ui_script", label="UI Script", table="sys_ui_script", side="client",
        description="Reusable client-side JavaScript library.",
        fields={**_s("name", "script_name", "script", "description"),
                **_b("active", "global", "use_scoped_format", "ignore_in_now_experience"), **_i("ui_type")},
        script_fields=("script",), lookup_fields=("name", "script_name"),
        required=("name", "script"),
        notes=f"global=true loads it on every form. {UI_TYPE_NOTE}",
    ),
    ScriptType(
        key="validation_script", label="Validation Script", table="sys_script_validator", side="client",
        description="Client-side validator for a field type (e.g. email, ip_address); the script is in 'validator'.",
        fields={**_s("internal_type", "validator", "description"), **_b("active"), **_i("ui_type")},
        script_fields=("validator",), lookup_fields=("internal_type",),
        required=("internal_type", "validator"),
        notes="internal_type is the field type name (e.g. 'email'). The validator function returns true or an error message.",
    ),
    # ── Hybrid (client + server) ─────────────────────────────────────────────
    ScriptType(
        key="ui_action", label="UI Action", table="sys_ui_action", side="hybrid",
        description="Form/list button, link, or context menu; server script plus optional client onclick.",
        fields={**_s("name", "table", "action_name", "onclick", "script", "condition", "hint", "comments",
                     "messages", "form_style", "list_style"),
                **_b("active", "client", "isolate_script", "form_button", "form_link", "form_context_menu",
                     "list_button", "list_link", "list_context_menu", "list_choice", "list_banner_button",
                     "show_insert", "show_update", "show_query", "show_multiple_update",
                     "ui11_compatible", "ui16_compatible", "format_for_configurable_workspace",
                     "form_button_v2", "form_menu_button_v2"),
                **_i("order")},
        script_fields=("script", "condition"), lookup_fields=("name", "action_name"), table_field="table",
        required=("name", "table"),
        notes=("For client+server: client=true, onclick='myFunc()', and the script calls "
               "gsftSubmit(null, g_form.getFormElement(), '<action_name>') then runs server code under "
               "'if (typeof window == \"undefined\")'."),
    ),
    ScriptType(
        key="ui_page", label="UI Page", table="sys_ui_page", side="hybrid",
        description="Jelly/HTML page with a client script and a server processing script.",
        fields={**_s("name", "html", "client_script", "processing_script", "category", "description"),
                **_b("direct")},
        script_fields=("html", "client_script", "processing_script"),
        required=("name", "html"),
    ),
    ScriptType(
        key="ui_macro", label="UI Macro", table="sys_ui_macro", side="hybrid",
        description="Reusable Jelly UI component.",
        fields={**_s("name", "xml", "description", "category", "media_type"), **_b("active")},
        script_fields=("xml",),
        required=("name", "xml"),
    ),
    ScriptType(
        key="sp_widget", label="Service Portal Widget", table="sp_widget", side="hybrid",
        description="Portal widget: HTML template, client controller, server script, link function, CSS.",
        fields={**_s("name", "id", "template", "css", "client_script", "script", "link", "option_schema",
                     "demo_data", "description", "controller_as", "data_table", "field_list", "roles", "category"),
                **_b("public", "has_preview")},
        script_fields=("script", "client_script", "template", "css", "link"), lookup_fields=("id", "name"),
        required=("name", "id"),
        notes="option_schema and demo_data are JSON strings.",
    ),
]

SCRIPT_TYPES: Dict[str, ScriptType] = {t.key: t for t in _TYPES}


def get_script_type(key: str) -> ScriptType:
    spec = SCRIPT_TYPES.get(key)
    if spec is None:
        from servicenow_mcp.errors import ServiceNowError
        raise ServiceNowError(
            f"Unknown script_type '{key}'.",
            code="INVALID_ARGS",
            detail=f"Use one of: {sorted(SCRIPT_TYPES)}. Call list_script_types for details.",
        )
    return spec


def describe_script_type(spec: ScriptType, include_fields: bool = True) -> Dict[str, object]:
    out: Dict[str, object] = {
        "script_type": spec.key,
        "label": spec.label,
        "table": spec.table,
        "side": spec.side,
        "description": spec.description,
        "script_fields": list(spec.script_fields),
    }
    if include_fields:
        out["fields"] = dict(spec.fields)
        out["required_on_create"] = list(spec.required)
        if spec.implied:
            out["implied_flags"] = {k: dict(v) for k, v in spec.implied.items()}
        out["lookup_fields"] = list(spec.lookup_fields)
        if spec.table_field:
            out["table_filter_field"] = spec.table_field
        if spec.parent_field:
            out["parent_field"] = spec.parent_field
        if spec.choices:
            out["choices"] = {k: list(v) for k, v in spec.choices.items()}
        if spec.references:
            out["references"] = {k: f"{t}.{f}" for k, (t, f) in spec.references.items()}
        if spec.notes:
            out["notes"] = spec.notes
    return out
