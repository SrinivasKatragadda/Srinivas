"""
Catalog Item Variables tools for the ServiceNow MCP server.

This module manages catalog item variables, including creation, updates,
ordering, UI policy bindings, and reference/choice metadata. It is the lower-
level companion to the catalog item and request management modules, focusing
on the form field definitions that end users interact with on catalog items.
"""

import logging
import re
from typing import Any, Dict, List, Optional

import requests
from pydantic import BaseModel, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)

VARIABLE_DEFINITION_TABLE = "item_option_new"
CHOICE_TABLE = "sys_choice"
DB_OBJECT_TABLE = "sys_db_object"
CATALOG_UI_POLICY_TABLE = "catalog_ui_policy"
CATALOG_UI_POLICY_ACTION_TABLE = "catalog_ui_policy_action"


class CreateCatalogItemVariableParams(BaseModel):
    """Parameters for creating a catalog item variable."""

    catalog_item_id: str = Field(..., description="The sys_id of the catalog item")
    name: str = Field(..., description="The name of the variable (internal name)")
    type: str = Field(
        ..., description="The type of variable (e.g., string, integer, boolean, reference)"
    )
    label: str = Field(..., description="The display label for the variable")
    mandatory: bool = Field(False, description="Whether the variable is required")
    hidden: Optional[bool] = Field(False, description="Whether the variable is hidden")
    visible: Optional[bool] = Field(
        None, description="Convenience flag to show or hide the variable; inverse of hidden"
    )
    read_only: Optional[bool] = Field(False, description="Whether the variable is read only")
    help_text: Optional[str] = Field(None, description="Help text to display with the variable")
    default_value: Optional[str] = Field(None, description="Default value for the variable")
    description: Optional[str] = Field(None, description="Description of the variable")
    order: Optional[int] = Field(None, description="Display order of the variable")
    reference_table: Optional[str] = Field(
        None, description="For reference fields, the table to reference"
    )
    reference_qualifier: Optional[str] = Field(
        None, description="For reference fields, the query to filter reference options"
    )
    max_length: Optional[int] = Field(None, description="Maximum length for string fields")
    min: Optional[int] = Field(None, description="Minimum value for numeric fields")
    max: Optional[int] = Field(None, description="Maximum value for numeric fields")
    attributes: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Additional item_option_new fields to send directly to ServiceNow so "
            "type-specific variable options can be populated"
        ),
    )


class CatalogItemVariableResponse(BaseModel):
    """Response from catalog item variable operations."""

    success: bool = Field(..., description="Whether the operation was successful")
    message: str = Field(..., description="Message describing the result")
    variable_id: Optional[str] = Field(
        None, description="The sys_id of the created/updated variable"
    )
    details: Optional[Dict[str, Any]] = Field(
        None, description="Additional details about the variable"
    )


class ListCatalogItemVariablesParams(BaseModel):
    """Parameters for listing catalog item variables."""

    catalog_item_id: str = Field(..., description="The sys_id of the catalog item")
    include_details: bool = Field(
        True, description="Whether to include detailed information about each variable"
    )
    limit: Optional[int] = Field(None, description="Maximum number of variables to return")
    offset: Optional[int] = Field(None, description="Offset for pagination")


class ListCatalogItemVariablesResponse(BaseModel):
    """Response from listing catalog item variables."""

    success: bool = Field(..., description="Whether the operation was successful")
    message: str = Field(..., description="Message describing the result")
    variables: List[Dict[str, Any]] = Field([], description="List of variables")
    count: int = Field(0, description="Total number of variables found")


class UpdateCatalogItemVariableParams(BaseModel):
    """Parameters for updating a catalog item variable."""

    variable_id: str = Field(..., description="The sys_id of the variable to update")
    replace_on_type_change: bool = Field(
        False,
        description=(
            "Create a replacement variable and deactivate the old one when changing the type, "
            "because ServiceNow often does not reliably update the type in place"
        ),
    )
    name: Optional[str] = Field(None, description="The internal name of the variable")
    type: Optional[str] = Field(
        None, description="The type of variable (e.g., string, integer, boolean, reference)"
    )
    label: Optional[str] = Field(None, description="The display label for the variable")
    mandatory: Optional[bool] = Field(None, description="Whether the variable is required")
    hidden: Optional[bool] = Field(None, description="Whether the variable is hidden")
    visible: Optional[bool] = Field(
        None, description="Convenience flag to show or hide the variable; inverse of hidden"
    )
    read_only: Optional[bool] = Field(None, description="Whether the variable is read only")
    help_text: Optional[str] = Field(None, description="Help text to display with the variable")
    default_value: Optional[str] = Field(None, description="Default value for the variable")
    description: Optional[str] = Field(None, description="Description of the variable")
    order: Optional[int] = Field(None, description="Display order of the variable")
    reference_table: Optional[str] = Field(
        None, description="For reference fields, the table to reference"
    )
    reference_qualifier: Optional[str] = Field(
        None, description="For reference fields, the query to filter reference options"
    )
    max_length: Optional[int] = Field(None, description="Maximum length for string fields")
    min: Optional[int] = Field(None, description="Minimum value for numeric fields")
    max: Optional[int] = Field(None, description="Maximum value for numeric fields")
    attributes: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Additional item_option_new fields to patch directly in ServiceNow so "
            "type-specific variable options can be populated"
        ),
    )


class GetCatalogVariableMetadataParams(BaseModel):
    """Parameters for inspecting available catalog variable metadata."""

    include_type_choices: bool = Field(
        True, description="Whether to include allowed item_option_new.type choices"
    )
    include_dictionary_fields: bool = Field(
        True, description="Whether to include available item_option_new table fields"
    )
    catalog_item_id: Optional[str] = Field(
        None,
        description=(
            "Optional catalog item sys_id to include current variable definitions for reference"
        ),
    )


class DeleteCatalogItemVariableParams(BaseModel):
    """Parameters for deleting a catalog item variable."""

    variable_id: str = Field(..., description="The sys_id of the variable to delete")


class CreateCatalogVariableChoiceParams(BaseModel):
    """Parameters for creating a choice for a catalog variable."""

    variable_id: str = Field(..., description="The sys_id of the variable/question")
    value: str = Field(..., description="Stored backend value")
    label: str = Field(..., description="Display label shown to users")
    order: Optional[int] = Field(None, description="Display order")
    inactive: Optional[bool] = Field(False, description="Whether the choice is inactive")
    attributes: Optional[Dict[str, Any]] = Field(
        None, description="Additional table fields to set on question_choice"
    )


class CatalogVariableChoiceInput(BaseModel):
    """Choice value to create alongside a catalog variable."""

    value: str = Field(..., description="Stored backend value")
    label: str = Field(..., description="Display label shown to users")
    order: Optional[int] = Field(None, description="Display order")
    inactive: Optional[bool] = Field(False, description="Whether the choice is inactive")
    attributes: Optional[Dict[str, Any]] = Field(
        None, description="Additional table fields to set on question_choice"
    )


class CatalogVariableDefinitionInput(BaseModel):
    """Definition for a catalog variable in a bulk create request."""

    name: str = Field(..., description="The internal name of the variable")
    type: str = Field(..., description="Variable type label or internal value")
    label: str = Field(..., description="Display label for the variable")
    mandatory: bool = Field(False, description="Whether the variable is required")
    hidden: Optional[bool] = Field(False, description="Whether the variable is hidden")
    visible: Optional[bool] = Field(
        None, description="Convenience flag to show or hide the variable; inverse of hidden"
    )
    read_only: Optional[bool] = Field(False, description="Whether the variable is read only")
    help_text: Optional[str] = Field(None, description="Help text to display with the variable")
    default_value: Optional[str] = Field(None, description="Default value for the variable")
    description: Optional[str] = Field(None, description="Description of the variable")
    order: Optional[int] = Field(None, description="Display order")
    reference_table: Optional[str] = Field(
        None, description="For reference fields, the table to reference"
    )
    reference_qualifier: Optional[str] = Field(
        None, description="For reference fields, the query to filter reference options"
    )
    max_length: Optional[int] = Field(None, description="Maximum length for string fields")
    min: Optional[int] = Field(None, description="Minimum value for numeric fields")
    max: Optional[int] = Field(None, description="Maximum value for numeric fields")
    attributes: Optional[Dict[str, Any]] = Field(
        None,
        description="Additional item_option_new fields to send directly to ServiceNow",
    )
    choices: Optional[List[CatalogVariableChoiceInput]] = Field(
        None,
        description="Optional choices to create after the variable is created",
    )


class CreateCatalogItemVariablesParams(BaseModel):
    """Parameters for creating multiple catalog item variables in one call."""

    catalog_item_id: str = Field(..., description="The sys_id of the catalog item")
    variables: List[CatalogVariableDefinitionInput] = Field(
        ..., description="Variables to create for the catalog item"
    )
    stop_on_error: bool = Field(
        False,
        description="Stop immediately after the first failed variable instead of continuing",
    )


class AddStandardOrderVariablesParams(BaseModel):
    """Parameters for creating a standard set of order-form variables."""

    catalog_item_id: str = Field(..., description="The sys_id of the catalog item")
    quantity_name: str = Field("quantity", description="Internal name for the quantity variable")
    quantity_label: str = Field("Quantity", description="Display label for the quantity variable")
    quantity_default: int = Field(1, description="Default quantity")
    quantity_min: int = Field(1, description="Minimum allowed quantity")
    quantity_max: int = Field(10, description="Maximum allowed quantity")
    size_name: str = Field("size", description="Internal name for the size variable")
    size_label: str = Field("Size", description="Display label for the size variable")
    size_choices: List[str] = Field(
        default_factory=lambda: ["Small", "Medium", "Large"],
        description="Choices to create for the size variable",
    )
    date_name: str = Field("delivery_date", description="Internal name for the date variable")
    date_label: str = Field("Delivery Date", description="Display label for the date variable")
    notes_name: str = Field(
        "special_instructions",
        description="Internal name for the free-text instructions variable",
    )
    notes_label: str = Field(
        "Special Instructions",
        description="Display label for the free-text instructions variable",
    )
    notes_help_text: Optional[str] = Field(
        "Any special requests for this order",
        description="Help text for the instructions variable",
    )
    notes_type: str = Field(
        "multi line text",
        description="Variable type for the instructions field",
    )
    first_order: int = Field(100, description="Display order for the first variable")
    order_step: int = Field(100, description="Gap between variable display orders")
    stop_on_error: bool = Field(
        False,
        description="Stop immediately after the first failed variable instead of continuing",
    )


class SetCatalogVariableBehaviorParams(BaseModel):
    """Parameters for changing simple behavior flags on a catalog variable."""

    variable_id: str = Field(..., description="The sys_id of the variable to update")
    mandatory: Optional[bool] = Field(None, description="Whether the variable is required")
    hidden: Optional[bool] = Field(None, description="Whether the variable is hidden")
    visible: Optional[bool] = Field(
        None, description="Convenience flag to show or hide the variable; inverse of hidden"
    )
    read_only: Optional[bool] = Field(None, description="Whether the variable is read only")


class CatalogVariableBehaviorActionInput(BaseModel):
    """Behavior action to apply to a catalog variable through a catalog UI policy."""

    variable_id: str = Field(..., description="Catalog variable sys_id")
    visible: Optional[bool] = Field(None, description="Whether the variable should be visible")
    mandatory: Optional[bool] = Field(None, description="Whether the variable should be mandatory")
    read_only: Optional[bool] = Field(None, description="Whether the variable should be read only")
    clear_value: Optional[bool] = Field(
        False, description="Whether to clear the variable value when the policy condition is met"
    )
    order: Optional[int] = Field(None, description="Execution order for the action")


class CreateCatalogVariableBehaviorPolicyParams(BaseModel):
    """Parameters for creating a Catalog UI Policy for variable behavior."""

    catalog_item_id: str = Field(..., description="Catalog item sys_id")
    short_description: str = Field(..., description="Short description for the behavior policy")
    catalog_conditions: Optional[str] = Field(
        None,
        description=(
            "Encoded catalog UI policy conditions, for example "
            "'IO:<variable_sys_id>=value^EQ'. Leave empty to apply on load without conditions."
        ),
    )
    active: bool = Field(True, description="Whether the policy is active")
    on_load: bool = Field(True, description="Whether to apply the policy on form load")
    reverse_if_false: bool = Field(
        True, description="Whether to reverse the policy actions when the condition is false"
    )
    order: Optional[int] = Field(None, description="Policy evaluation order")
    run_scripts: bool = Field(False, description="Whether the policy uses client-side scripts")
    script_true: Optional[str] = Field(None, description="Client script to run when true")
    script_false: Optional[str] = Field(None, description="Client script to run when false")
    actions: List[CatalogVariableBehaviorActionInput] = Field(
        ..., description="Variable behavior actions to apply"
    )


def _build_catalog_variable_choice_payload(
    params: CreateCatalogVariableChoiceParams,
) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "question": params.variable_id,
        "value": params.value,
        "text": params.label,
    }
    if params.order is not None:
        data["order"] = params.order
    if params.inactive is not None:
        data["inactive"] = str(params.inactive).lower()
    data.update(params.attributes or {})
    return data


def _normalize_type_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().casefold())


def _extract_error_body(error: requests.RequestException) -> str:
    response = getattr(error, "response", None)
    if response is None:
        return str(error)
    status = response.status_code
    try:
        payload = response.json()
        details = payload.get("error") or payload.get("message") or payload
    except ValueError:
        details = response.text.strip() or str(error)
    return f"HTTP {status}: {details}"


def _coerce_value_mapping(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, dict) and ("value" in value or "display_value" in value):
        return value
    return None


def _display(value: Any) -> Any:
    mapping = _coerce_value_mapping(value)
    if mapping is not None:
        return mapping.get("display_value") or mapping.get("value")
    return value


def _extract_value(value: Any) -> Any:
    mapping = _coerce_value_mapping(value)
    if mapping is not None:
        return mapping.get("value") or mapping.get("display_value")
    return value


def _to_bool(value: Any, default: bool = False) -> bool:
    normalized = _display(value)
    if isinstance(normalized, bool):
        return normalized
    if isinstance(normalized, str):
        lowered = normalized.strip().casefold()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return default


def _resolve_hidden_value(*, hidden: Optional[bool], visible: Optional[bool]) -> Optional[bool]:
    if hidden is not None and visible is not None and hidden == visible:
        raise ValueError("hidden and visible conflict; provide only one or make them opposites")
    if visible is not None:
        return not visible
    return hidden


def _catalog_variable_type_aliases() -> Dict[str, List[str]]:
    return {
        "string": ["single line text", "singleline", "single line", "text", "textbox"],
        "textarea": ["multi line text", "multiline", "multi line", "text area", "paragraph"],
        "reference": ["lookup", "reference lookup"],
        "boolean": ["yes/no", "yesno", "checkbox", "true/false", "bool"],
        "integer": ["int", "whole number", "number"],
        "decimal": ["float", "double", "currency decimal"],
        "date": ["date picker"],
        "datetime": ["date time", "date/time"],
        "select": ["choice", "dropdown", "select box"],
        "multiplechoice": ["multiple choice", "radio", "radio buttons"],
    }


def _catalog_variable_type_internal_hints() -> Dict[str, List[str]]:
    return {
        "string": ["string"],
        "textarea": ["textarea"],
        "reference": ["reference"],
        "boolean": ["boolean", "checkbox", "check_box", "yesno", "yes_no"],
        "integer": ["integer", "integer_scale"],
        "decimal": ["decimal", "decimal_scale", "float"],
        "date": ["date", "glide_date"],
        "datetime": ["datetime", "glide_date_time"],
        "select": ["select", "select_box", "choice"],
        "multiplechoice": ["multiplechoice", "multiple_choice", "radio"],
        "listcollector": ["glide_list", "list_collector"],
    }


def _catalog_variable_type_field_hints() -> Dict[str, List[str]]:
    return {
        "single line text": [
            "mandatory",
            "hidden/visible",
            "read_only",
            "default_value",
            "max_length",
            "attributes.placeholder",
        ],
        "wide single line text": [
            "mandatory",
            "hidden/visible",
            "read_only",
            "default_value",
            "max_length",
            "attributes.placeholder",
        ],
        "multi line text": [
            "mandatory",
            "hidden/visible",
            "read_only",
            "default_value",
            "max_length",
            "attributes.placeholder",
        ],
        "date": [
            "mandatory",
            "hidden/visible",
            "read_only",
            "default_value",
            "attributes.placeholder",
        ],
        "date/time": [
            "mandatory",
            "hidden/visible",
            "read_only",
            "default_value",
            "attributes.placeholder",
        ],
        "reference": [
            "mandatory",
            "hidden/visible",
            "read_only",
            "reference_table",
            "reference_qualifier",
            "attributes",
        ],
        "select box": [
            "mandatory",
            "hidden/visible",
            "read_only",
            "attributes.include_none",
            "attributes.choice_table",
            "attributes.choice_field",
        ],
        "multiple choice": [
            "mandatory",
            "hidden/visible",
            "read_only",
            "attributes.include_none",
            "attributes.choice_direction",
        ],
        "lookup select box": [
            "mandatory",
            "hidden/visible",
            "read_only",
            "attributes.lookup_source",
            "attributes.lookup_table",
            "attributes.lookup_value_field",
            "attributes.lookup_label_fields",
            "attributes.include_none",
        ],
        "lookup multiple choice": [
            "mandatory",
            "hidden/visible",
            "read_only",
            "attributes.lookup_source",
            "attributes.lookup_table",
            "attributes.lookup_value_field",
            "attributes.lookup_label_fields",
            "attributes.include_none",
            "attributes.choice_direction",
        ],
        "list collector": [
            "mandatory",
            "hidden/visible",
            "read_only",
            "attributes.list_table",
            "reference_qualifier",
            "attributes",
        ],
        "yes/no": ["mandatory", "hidden/visible", "read_only", "default_value"],
        "check box": [
            "mandatory",
            "hidden/visible",
            "read_only",
            "default_value",
            "attributes.selection_required",
        ],
    }


def _get_catalog_variable_type_choices(
    config: ServerConfig, auth_manager: AuthManager
) -> List[Dict[str, Any]]:
    table_sources = _catalog_variable_type_choice_tables(config, auth_manager)
    try:
        response = requests.get(
            f"{config.instance_url}/api/now/table/{CHOICE_TABLE}",
            headers=auth_manager.get_headers(),
            params={
                "sysparm_query": (
                    f"nameIN{','.join(table_sources)}^element=type^inactive=false^ORDERBYsequence"
                ),
                "sysparm_fields": "name,label,value,sequence,dependent_value",
                "sysparm_display_value": "true",
                "sysparm_exclude_reference_link": "true",
            },
            timeout=config.timeout,
        )
        response.raise_for_status()
        result = response.json().get("result", [])
        if result:
            return result
    except requests.RequestException as error:
        logger.warning(
            "Failed to read ServiceNow variable type choices from sys_choice for %s. %s",
            ", ".join(table_sources),
            _extract_error_body(error),
        )

    observed_choices = _get_observed_catalog_variable_type_choices(config, auth_manager)
    if observed_choices:
        logger.info(
            "Using observed catalog variable types from existing %s records because sys_choice "
            "returned no rows for %s.type",
            VARIABLE_DEFINITION_TABLE,
            ", ".join(table_sources),
        )
        return observed_choices

    raise ValueError(
        "Failed to discover ServiceNow variable type choices. "
        f"No sys_choice rows were found for {', '.join(table_sources)}.type and no existing "
        f"{VARIABLE_DEFINITION_TABLE} records exposed usable type values."
    )


def _catalog_variable_type_choice_tables(
    config: ServerConfig, auth_manager: AuthManager
) -> List[str]:
    tables: List[str] = [VARIABLE_DEFINITION_TABLE]
    current_table = VARIABLE_DEFINITION_TABLE
    seen = {current_table}

    while True:
        try:
            response = requests.get(
                f"{config.instance_url}/api/now/table/{DB_OBJECT_TABLE}",
                headers=auth_manager.get_headers(),
                params={
                    "sysparm_query": f"name={current_table}",
                    "sysparm_fields": "name,super_class",
                    "sysparm_limit": 1,
                    "sysparm_display_value": "all",
                    "sysparm_exclude_reference_link": "true",
                },
                timeout=config.timeout,
            )
            response.raise_for_status()
            records = response.json().get("result", [])
        except requests.RequestException as error:
            logger.debug(
                "Could not read table hierarchy for %s from %s: %s",
                current_table,
                DB_OBJECT_TABLE,
                _extract_error_body(error),
            )
            break

        if not records:
            break

        super_class = _extract_value(records[0].get("super_class"))
        if not super_class:
            break
        super_class_name = str(super_class).strip()
        if not super_class_name or super_class_name in seen:
            break
        tables.append(super_class_name)
        seen.add(super_class_name)
        current_table = super_class_name

    if "question" not in seen:
        tables.append("question")
    return tables


def _get_observed_catalog_variable_type_choices(
    config: ServerConfig, auth_manager: AuthManager
) -> List[Dict[str, Any]]:
    try:
        response = requests.get(
            f"{config.instance_url}/api/now/table/{VARIABLE_DEFINITION_TABLE}",
            headers=auth_manager.get_headers(),
            params={
                "sysparm_query": "typeISNOTEMPTY",
                "sysparm_fields": "type",
                "sysparm_limit": 500,
                "sysparm_display_value": "all",
                "sysparm_exclude_reference_link": "true",
            },
            timeout=config.timeout,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        logger.debug(
            "Could not observe existing %s types: %s",
            VARIABLE_DEFINITION_TABLE,
            _extract_error_body(error),
        )
        return []

    seen: set[tuple[str, str]] = set()
    observed: List[Dict[str, Any]] = []
    for record in response.json().get("result", []):
        type_value = str(_extract_value(record.get("type")) or "").strip()
        type_label = str(_display(record.get("type")) or "").strip()
        key = (type_value, type_label)
        if key in seen or (not type_value and not type_label):
            continue
        seen.add(key)
        observed.append(
            {
                "name": VARIABLE_DEFINITION_TABLE,
                "value": type_value,
                "label": type_label,
            }
        )
    return observed


def _get_catalog_variable_dictionary_fields(
    config: ServerConfig, auth_manager: AuthManager
) -> List[Dict[str, Any]]:
    try:
        response = requests.get(
            f"{config.instance_url}/api/now/table/sys_dictionary",
            headers=auth_manager.get_headers(),
            params={
                "sysparm_query": f"name={VARIABLE_DEFINITION_TABLE}^elementISNOTEMPTY^active=true",
                "sysparm_fields": (
                    "element,column_label,internal_type,mandatory,max_length,reference,choice,"
                    "default_value,attributes"
                ),
                "sysparm_display_value": "true",
                "sysparm_exclude_reference_link": "true",
                "sysparm_limit": 500,
            },
            timeout=config.timeout,
        )
        response.raise_for_status()
        return response.json().get("result", [])
    except requests.RequestException as error:
        raise ValueError(
            "Failed to read item_option_new field metadata from sys_dictionary. "
            f"{_extract_error_body(error)}"
        ) from error


def _resolve_catalog_variable_type(
    config: ServerConfig, auth_manager: AuthManager, requested_type: str
) -> str:
    choices = _get_catalog_variable_type_choices(config, auth_manager)
    return _resolve_catalog_variable_type_from_choices(requested_type, choices)


def _resolve_catalog_variable_type_from_choices(
    requested_type: str, choices: List[Dict[str, Any]]
) -> str:
    requested_key = _normalize_type_key(requested_type)
    if not choices:
        logger.warning(
            "No sys_choice records found for %s.type; sending raw type value '%s'",
            VARIABLE_DEFINITION_TABLE,
            requested_type,
        )
        return requested_type

    indexed_choices: Dict[str, str] = {}
    for choice in choices:
        value = str(choice.get("value", "")).strip()
        label = str(choice.get("label", "")).strip()
        if value:
            indexed_choices[_normalize_type_key(value)] = value
        if label:
            indexed_choices[_normalize_type_key(label)] = value or label

    aliases = _catalog_variable_type_aliases()
    for canonical_key, alias_values in aliases.items():
        canonical_value = indexed_choices.get(_normalize_type_key(canonical_key))
        if not canonical_value:
            continue
        for alias in alias_values:
            indexed_choices.setdefault(_normalize_type_key(alias), canonical_value)

    resolved_value = indexed_choices.get(requested_key)
    if resolved_value:
        return resolved_value

    available_types = sorted(
        {
            str(choice.get("label", "")).strip() or str(choice.get("value", "")).strip()
            for choice in choices
            if str(choice.get("label", "")).strip() or str(choice.get("value", "")).strip()
        }
    )
    raise ValueError(
        "Unsupported catalog variable type "
        f"'{requested_type}'. Available types: {', '.join(available_types)}"
    )


def _catalog_variable_type_family_keys(
    requested_type: str, choices: List[Dict[str, Any]]
) -> set[str]:
    resolved_type = _resolve_catalog_variable_type_from_choices(requested_type, choices)
    family_keys = {
        _normalize_type_key(requested_type),
        _normalize_type_key(resolved_type),
    }

    aliases = _catalog_variable_type_aliases()
    for canonical_key, alias_values in aliases.items():
        alias_keys = {_normalize_type_key(canonical_key)}
        alias_keys.update(_normalize_type_key(alias) for alias in alias_values)
        if family_keys & alias_keys:
            family_keys.update(alias_keys)

    for choice in choices:
        value_key = _normalize_type_key(str(choice.get("value", "")).strip())
        label_key = _normalize_type_key(str(choice.get("label", "")).strip())
        if value_key in family_keys or label_key in family_keys:
            if value_key:
                family_keys.add(value_key)
            if label_key:
                family_keys.add(label_key)
    return family_keys


def _catalog_variable_type_candidates(
    requested_type: str, choices: List[Dict[str, Any]]
) -> List[str]:
    candidates: List[str] = []

    def add_candidate(value: Optional[str]) -> None:
        """Append a non-empty candidate value while preserving insertion order."""
        if value is None:
            return
        candidate = str(value).strip()
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    resolved_type = _resolve_catalog_variable_type_from_choices(requested_type, choices)
    family_keys = _catalog_variable_type_family_keys(requested_type, choices)

    add_candidate(resolved_type)
    add_candidate(requested_type)

    for choice in choices:
        choice_value = str(choice.get("value", "")).strip()
        choice_label = str(choice.get("label", "")).strip()
        if (
            _normalize_type_key(choice_value) in family_keys
            or _normalize_type_key(choice_label) in family_keys
        ):
            add_candidate(choice_value)
            add_candidate(choice_label)

    internal_hints = _catalog_variable_type_internal_hints()
    for canonical_key, hint_values in internal_hints.items():
        canonical_family = {_normalize_type_key(canonical_key)}
        canonical_family.update(
            _normalize_type_key(alias)
            for alias in _catalog_variable_type_aliases().get(canonical_key, [])
        )
        if family_keys & canonical_family:
            for hint in hint_values:
                add_candidate(hint)

    return candidates


def _catalog_variable_type_matches(
    requested_type: str,
    actual_type: Optional[str],
    choices: List[Dict[str, Any]],
) -> bool:
    actual_key = _normalize_type_key(actual_type or "")
    if not actual_key:
        return False

    family_keys = _catalog_variable_type_family_keys(requested_type, choices)
    if actual_key in family_keys:
        return True

    for choice in choices:
        choice_value_key = _normalize_type_key(str(choice.get("value", "")).strip())
        choice_label_key = _normalize_type_key(str(choice.get("label", "")).strip())
        if actual_key in {choice_value_key, choice_label_key} and (
            choice_value_key in family_keys or choice_label_key in family_keys
        ):
            return True

    return False


def _delete_catalog_item_variable_record(
    config: ServerConfig, auth_manager: AuthManager, variable_id: str
) -> None:
    response = requests.delete(
        f"{config.instance_url}/api/now/table/{VARIABLE_DEFINITION_TABLE}/{variable_id}",
        headers=auth_manager.get_headers(),
        timeout=config.timeout,
    )
    response.raise_for_status()


def _create_catalog_item_variable_record(
    config: ServerConfig,
    auth_manager: AuthManager,
    *,
    payload_kwargs: Dict[str, Any],
    requested_type: str,
) -> Dict[str, Any]:
    choices = _get_catalog_variable_type_choices(config, auth_manager)
    candidate_types = _catalog_variable_type_candidates(requested_type, choices)
    last_actual_type = ""

    for candidate_type in candidate_types:
        payload = _build_catalog_item_variable_payload(
            config,
            auth_manager,
            resolved_type_value=candidate_type,
            **payload_kwargs,
        )
        response = requests.post(
            f"{config.instance_url}/api/now/table/{VARIABLE_DEFINITION_TABLE}",
            json=payload,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()

        created_record = response.json().get("result", {})
        variable_id = str(_extract_value(created_record.get("sys_id")) or "").strip()
        if variable_id:
            created_record = _read_catalog_item_variable(config, auth_manager, variable_id)

        actual_type = str(
            _display(created_record.get("type")) or _extract_value(created_record.get("type")) or ""
        ).strip()
        if _catalog_variable_type_matches(requested_type, actual_type, choices):
            return {
                "record": created_record,
                "used_type": candidate_type,
            }

        last_actual_type = actual_type
        if not variable_id:
            break

        try:
            _delete_catalog_item_variable_record(config, auth_manager, variable_id)
        except requests.RequestException as delete_error:
            raise ValueError(
                "ServiceNow created the variable but kept the wrong type "
                f"('{actual_type or 'unknown'}') when '{requested_type}' was requested, "
                f"and the fallback cleanup failed for {variable_id}. "
                f"{_extract_error_body(delete_error)}"
            ) from delete_error

    raise ValueError(
        "ServiceNow did not honor the requested catalog variable type "
        f"'{requested_type}'. Last actual type was '{last_actual_type or 'unknown'}'."
    )


def _read_catalog_item_variable(
    config: ServerConfig, auth_manager: AuthManager, variable_id: str
) -> Dict[str, Any]:
    try:
        response = requests.get(
            f"{config.instance_url}/api/now/table/{VARIABLE_DEFINITION_TABLE}/{variable_id}",
            headers=auth_manager.get_headers(),
            params={
                "sysparm_display_value": "all",
                "sysparm_exclude_reference_link": "true",
            },
            timeout=config.timeout,
        )
        response.raise_for_status()
        return response.json().get("result", {})
    except requests.RequestException as error:
        raise ValueError(
            "Failed to read the existing catalog variable before applying the type change. "
            f"{_extract_error_body(error)}"
        ) from error


def _create_catalog_ui_policy_action(
    config: ServerConfig,
    auth_manager: AuthManager,
    *,
    ui_policy_id: str,
    catalog_item_id: str,
    action: CatalogVariableBehaviorActionInput,
) -> Dict[str, Any]:
    variable_record = _read_catalog_item_variable(config, auth_manager, action.variable_id)
    variable_name = str(_extract_value(variable_record.get("name")) or "").strip()
    if not variable_name:
        raise ValueError(
            f"Catalog variable {action.variable_id} is missing a usable variable name for UI "
            f"policy actions"
        )

    body: Dict[str, Any] = {
        "ui_policy": ui_policy_id,
        "catalog_item": catalog_item_id,
        "catalog_variable": action.variable_id,
        "variable": variable_name,
    }
    if action.visible is not None:
        body["visible"] = str(action.visible).lower()
    if action.mandatory is not None:
        body["mandatory"] = str(action.mandatory).lower()
    if action.read_only is not None:
        body["disabled"] = str(action.read_only).lower()
    if action.clear_value is not None:
        body["cleared"] = str(action.clear_value).lower()
    if action.order is not None:
        body["order"] = action.order

    response = requests.post(
        f"{config.instance_url}/api/now/table/{CATALOG_UI_POLICY_ACTION_TABLE}",
        json=body,
        headers=auth_manager.get_headers(),
        timeout=config.timeout,
    )
    response.raise_for_status()
    return response.json().get("result", {})


def _build_catalog_item_variable_payload(
    config: ServerConfig,
    auth_manager: AuthManager,
    *,
    catalog_item_id: str,
    name: str,
    variable_type: str,
    label: str,
    mandatory: bool,
    hidden: Optional[bool] = None,
    visible: Optional[bool] = None,
    read_only: Optional[bool] = None,
    help_text: Optional[str] = None,
    default_value: Optional[str] = None,
    description: Optional[str] = None,
    order: Optional[int] = None,
    reference_table: Optional[str] = None,
    reference_qualifier: Optional[str] = None,
    max_length: Optional[int] = None,
    min_value: Optional[int] = None,
    max_value: Optional[int] = None,
    attributes: Optional[Dict[str, Any]] = None,
    resolved_type_value: Optional[str] = None,
) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        **(attributes or {}),
        "cat_item": catalog_item_id,
        "name": name,
        "type": (
            resolved_type_value
            if resolved_type_value is not None
            else _resolve_catalog_variable_type(config, auth_manager, variable_type)
        ),
        "question_text": label,
        "mandatory": str(mandatory).lower(),
    }
    resolved_hidden = _resolve_hidden_value(hidden=hidden, visible=visible)
    if resolved_hidden is not None:
        data["hidden"] = str(resolved_hidden).lower()
    if read_only is not None:
        data["read_only"] = str(read_only).lower()
    if help_text:
        data["help_text"] = help_text
        data["tooltip"] = help_text
    if default_value:
        data["default_value"] = default_value
    if description:
        data["description"] = description
    if order is not None:
        data["order"] = order
    if reference_table:
        data["reference"] = reference_table
    if reference_qualifier:
        data["reference_qual"] = reference_qualifier
    if max_length is not None:
        data["max_length"] = max_length
    if min_value is not None:
        data["min"] = min_value
    if max_value is not None:
        data["max"] = max_value
    return data


def _replace_catalog_item_variable_for_type_change(
    config: ServerConfig,
    auth_manager: AuthManager,
    existing_record: Dict[str, Any],
    params: UpdateCatalogItemVariableParams,
) -> CatalogItemVariableResponse:
    catalog_item_id = _extract_value(existing_record.get("cat_item"))
    if not catalog_item_id:
        return CatalogItemVariableResponse(
            success=False,
            message="Unable to replace variable because catalog item reference was missing",
        )

    existing_name = _extract_value(existing_record.get("name"))
    existing_label = _display(existing_record.get("question_text"))
    existing_type = _display(existing_record.get("type")) or _extract_value(
        existing_record.get("type")
    )

    try:
        creation_result = _create_catalog_item_variable_record(
            config,
            auth_manager,
            payload_kwargs={
                "catalog_item_id": str(catalog_item_id),
                "name": str(params.name or existing_name or ""),
                "variable_type": str(params.type or existing_type or ""),
                "label": str(params.label or existing_label or ""),
                "mandatory": (
                    params.mandatory
                    if params.mandatory is not None
                    else _to_bool(existing_record.get("mandatory"))
                ),
                "hidden": (
                    params.hidden
                    if params.hidden is not None or params.visible is not None
                    else _to_bool(existing_record.get("hidden"))
                ),
                "visible": params.visible,
                "read_only": (
                    params.read_only
                    if params.read_only is not None
                    else _to_bool(existing_record.get("read_only"))
                ),
                "help_text": (
                    params.help_text
                    if params.help_text is not None
                    else _display(existing_record.get("help_text"))
                    or _display(existing_record.get("tooltip"))
                ),
                "default_value": (
                    params.default_value
                    if params.default_value is not None
                    else _extract_value(existing_record.get("default_value"))
                ),
                "description": (
                    params.description
                    if params.description is not None
                    else _display(existing_record.get("description"))
                ),
                "order": (
                    params.order
                    if params.order is not None
                    else (
                        int(_extract_value(existing_record.get("order")))
                        if _extract_value(existing_record.get("order")) not in (None, "")
                        else None
                    )
                ),
                "reference_table": (
                    params.reference_table
                    if params.reference_table is not None
                    else _extract_value(existing_record.get("reference"))
                ),
                "reference_qualifier": (
                    params.reference_qualifier
                    if params.reference_qualifier is not None
                    else _display(existing_record.get("reference_qual"))
                ),
                "max_length": (
                    params.max_length
                    if params.max_length is not None
                    else (
                        int(_extract_value(existing_record.get("max_length")))
                        if _extract_value(existing_record.get("max_length")) not in (None, "")
                        else None
                    )
                ),
                "min_value": (
                    params.min
                    if params.min is not None
                    else (
                        int(_extract_value(existing_record.get("min")))
                        if _extract_value(existing_record.get("min")) not in (None, "")
                        else None
                    )
                ),
                "max_value": (
                    params.max
                    if params.max is not None
                    else (
                        int(_extract_value(existing_record.get("max")))
                        if _extract_value(existing_record.get("max")) not in (None, "")
                        else None
                    )
                ),
                "attributes": params.attributes,
            },
            requested_type=str(params.type or existing_type or ""),
        )
        replacement_record = creation_result["record"]
    except requests.RequestException as error:
        return CatalogItemVariableResponse(
            success=False,
            message=(
                "Failed to create the replacement catalog variable for the type change. "
                f"{_extract_error_body(error)}"
            ),
        )
    replacement_id = replacement_record.get("sys_id")
    warnings: List[str] = []

    try:
        requests.patch(
            f"{config.instance_url}/api/now/table/{VARIABLE_DEFINITION_TABLE}/{params.variable_id}",
            json={"active": "false"},
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        ).raise_for_status()
    except requests.RequestException as exc:
        logger.warning(
            "Created replacement variable but could not deactivate old variable: %s", exc
        )
        warnings.append(
            "Replacement variable created, but the original variable could not be deactivated."
        )

    details = {
        "original_variable_id": params.variable_id,
        "replacement_variable": replacement_record,
        "warnings": warnings,
    }
    return CatalogItemVariableResponse(
        success=True,
        message=(
            "ServiceNow did not update the variable type in place, so a replacement variable "
            "was created instead"
        ),
        variable_id=replacement_id,
        details=details,
    )


def create_catalog_item_variable(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateCatalogItemVariableParams,
) -> CatalogItemVariableResponse:
    """
    Create a new variable (form field) for a catalog item.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Parameters for creating a catalog item variable.

    Returns:
        Response with information about the created variable.
    """

    try:
        creation_result = _create_catalog_item_variable_record(
            config,
            auth_manager,
            payload_kwargs={
                "catalog_item_id": params.catalog_item_id,
                "name": params.name,
                "variable_type": params.type,
                "label": params.label,
                "mandatory": params.mandatory,
                "hidden": params.hidden,
                "visible": params.visible,
                "read_only": params.read_only,
                "help_text": params.help_text,
                "default_value": params.default_value,
                "description": params.description,
                "order": params.order,
                "reference_table": params.reference_table,
                "reference_qualifier": params.reference_qualifier,
                "max_length": params.max_length,
                "min_value": params.min,
                "max_value": params.max,
                "attributes": params.attributes,
            },
            requested_type=params.type,
        )
        result = creation_result["record"]

        return CatalogItemVariableResponse(
            success=True,
            message="Catalog item variable created successfully",
            variable_id=_extract_value(result.get("sys_id")) or result.get("sys_id"),
            details={
                **result,
                "requested_type": params.type,
                "used_type": creation_result["used_type"],
            },
        )

    except ValueError as e:
        logger.error(f"Failed to resolve catalog item variable type: {e}")
        return CatalogItemVariableResponse(
            success=False,
            message=str(e),
        )
    except requests.RequestException as e:
        logger.error(f"Failed to create catalog item variable: {e}")
        return CatalogItemVariableResponse(
            success=False,
            message=f"Failed to create catalog item variable: {_extract_error_body(e)}",
        )


def list_catalog_item_variables(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListCatalogItemVariablesParams,
) -> ListCatalogItemVariablesResponse:
    """
    List all variables (form fields) for a catalog item.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Parameters for listing catalog item variables.

    Returns:
        Response with a list of variables for the catalog item.
    """
    # Build query parameters
    query_params = {
        "sysparm_query": f"cat_item={params.catalog_item_id}^ORDERBYorder",
    }

    if params.limit:
        query_params["sysparm_limit"] = params.limit
    if params.offset:
        query_params["sysparm_offset"] = params.offset

    # Include all fields if detailed info is requested
    if params.include_details:
        query_params["sysparm_display_value"] = "true"
        query_params["sysparm_exclude_reference_link"] = "false"
    else:
        query_params["sysparm_fields"] = (
            "sys_id,name,type,question_text,order,mandatory,hidden,read_only"
        )

    api_url = f"{config.instance_url}/api/now/table/item_option_new"

    # Make request
    try:
        response = requests.get(
            api_url,
            params=query_params,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()

        result = response.json().get("result", [])

        return ListCatalogItemVariablesResponse(
            success=True,
            message=f"Retrieved {len(result)} variables for catalog item",
            variables=result,
            count=len(result),
        )

    except requests.RequestException as e:
        logger.error(f"Failed to list catalog item variables: {e}")
        return ListCatalogItemVariablesResponse(
            success=False,
            message=f"Failed to list catalog item variables: {str(e)}",
        )


def update_catalog_item_variable(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateCatalogItemVariableParams,
) -> CatalogItemVariableResponse:
    """
    Update an existing variable (form field) for a catalog item.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Parameters for updating a catalog item variable.

    Returns:
        Response with information about the updated variable.
    """
    api_url = f"{config.instance_url}/api/now/table/item_option_new/{params.variable_id}"

    try:
        existing_record: Optional[Dict[str, Any]] = None
        if params.type is not None or params.replace_on_type_change:
            existing_record = _read_catalog_item_variable(config, auth_manager, params.variable_id)

        if params.type is not None and existing_record:
            requested_type_value = _resolve_catalog_variable_type(config, auth_manager, params.type)
            current_type_value = str(_extract_value(existing_record.get("type")) or "").strip()
            if current_type_value != requested_type_value:
                if params.replace_on_type_change:
                    return _replace_catalog_item_variable_for_type_change(
                        config, auth_manager, existing_record, params
                    )
                return CatalogItemVariableResponse(
                    success=False,
                    message=(
                        "ServiceNow often does not reliably change the type of an existing "
                        "catalog variable in place. Re-run with "
                        "replace_on_type_change=true to create a replacement variable and "
                        "deactivate the old one."
                    ),
                )

        # Build request data with only parameters that are provided
        data = {**(params.attributes or {})}

        if params.name is not None:
            data["name"] = params.name
        if params.type is not None:
            data["type"] = _resolve_catalog_variable_type(config, auth_manager, params.type)
        if params.label is not None:
            data["question_text"] = params.label
        if params.mandatory is not None:
            data["mandatory"] = str(
                params.mandatory
            ).lower()  # ServiceNow expects "true"/"false" strings
        resolved_hidden = _resolve_hidden_value(hidden=params.hidden, visible=params.visible)
        if resolved_hidden is not None:
            data["hidden"] = str(resolved_hidden).lower()
        if params.read_only is not None:
            data["read_only"] = str(params.read_only).lower()
        if params.help_text is not None:
            data["help_text"] = params.help_text
            data["tooltip"] = params.help_text
        if params.default_value is not None:
            data["default_value"] = params.default_value
        if params.description is not None:
            data["description"] = params.description
        if params.order is not None:
            data["order"] = params.order
        if params.reference_table is not None:
            data["reference"] = params.reference_table
        if params.reference_qualifier is not None:
            data["reference_qual"] = params.reference_qualifier
        if params.max_length is not None:
            data["max_length"] = params.max_length
        if params.min is not None:
            data["min"] = params.min
        if params.max is not None:
            data["max"] = params.max

        if not data:
            return CatalogItemVariableResponse(
                success=False,
                message="No update parameters provided",
            )

        response = requests.patch(
            api_url,
            json=data,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()

        result = response.json().get("result", {})

        return CatalogItemVariableResponse(
            success=True,
            message="Catalog item variable updated successfully",
            variable_id=params.variable_id,
            details=result,
        )

    except ValueError as e:
        logger.error(f"Failed to resolve catalog item variable type: {e}")
        return CatalogItemVariableResponse(
            success=False,
            message=str(e),
        )
    except requests.RequestException as e:
        logger.error(f"Failed to update catalog item variable: {e}")
        return CatalogItemVariableResponse(
            success=False,
            message=f"Failed to update catalog item variable: {_extract_error_body(e)}",
        )


def get_catalog_variable_metadata(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetCatalogVariableMetadataParams,
) -> Dict[str, Any]:
    """Return item_option_new metadata so callers can build better catalog variable payloads."""
    result: Dict[str, Any] = {
        "success": True,
        "table": VARIABLE_DEFINITION_TABLE,
        "recommended_workflow": (
            "Fetch metadata first, choose a valid variable type, then pass any extra "
            "item_option_new fields through attributes during create/update."
        ),
        "type_field_hints": _catalog_variable_type_field_hints(),
    }
    warnings: List[str] = []

    try:
        if params.include_type_choices:
            choices = _get_catalog_variable_type_choices(config, auth_manager)
            result["type_choices"] = [
                {
                    "label": str(choice.get("label", "")).strip(),
                    "value": str(choice.get("value", "")).strip(),
                    "normalized_key": _normalize_type_key(
                        str(choice.get("label", "")).strip() or str(choice.get("value", "")).strip()
                    ),
                }
                for choice in choices
            ]
        if params.include_dictionary_fields:
            result["dictionary_fields"] = _get_catalog_variable_dictionary_fields(
                config, auth_manager
            )
        if params.catalog_item_id:
            variable_response = list_catalog_item_variables(
                config,
                auth_manager,
                ListCatalogItemVariablesParams(
                    catalog_item_id=params.catalog_item_id,
                    include_details=True,
                ),
            )
            result["existing_variables"] = variable_response.variables
    except ValueError as error:
        warnings.append(str(error))

    if warnings:
        result["warnings"] = warnings
        if "type_choices" not in result and "dictionary_fields" not in result:
            result["success"] = False
            result["message"] = warnings[0]
        else:
            result["message"] = "Retrieved partial catalog variable metadata"
    else:
        result["message"] = "Retrieved catalog variable metadata"
    return result


def delete_catalog_item_variable(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: DeleteCatalogItemVariableParams,
) -> Dict[str, Any]:
    """Delete an existing catalog item variable."""
    api_url = f"{config.instance_url}/api/now/table/item_option_new/{params.variable_id}"

    try:
        response = requests.delete(
            api_url,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        return {
            "success": True,
            "message": f"Catalog item variable deleted: {params.variable_id}",
            "variable_id": params.variable_id,
        }
    except requests.RequestException as e:
        logger.error(f"Failed to delete catalog item variable: {e}")
        return {
            "success": False,
            "message": f"Failed to delete catalog item variable: {str(e)}",
            "variable_id": params.variable_id,
        }


def create_catalog_variable_choice(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateCatalogVariableChoiceParams,
) -> Dict[str, Any]:
    """Create a choice value for a choice-based catalog variable."""
    api_url = f"{config.instance_url}/api/now/table/question_choice"

    try:
        response = requests.post(
            api_url,
            json=_build_catalog_variable_choice_payload(params),
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        result = response.json().get("result", {})
        return {
            "success": True,
            "message": "Catalog variable choice created successfully",
            "choice": result,
        }
    except requests.RequestException as e:
        logger.error(f"Failed to create catalog variable choice: {e}")
        return {
            "success": False,
            "message": f"Failed to create catalog variable choice: {str(e)}",
        }


def create_catalog_item_variables(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateCatalogItemVariablesParams,
) -> Dict[str, Any]:
    """Create multiple catalog item variables and optional choices in one call."""
    created_variables: List[Dict[str, Any]] = []
    failed_variables: List[Dict[str, Any]] = []

    for index, variable in enumerate(params.variables, start=1):
        create_response = create_catalog_item_variable(
            config,
            auth_manager,
            CreateCatalogItemVariableParams(
                catalog_item_id=params.catalog_item_id,
                name=variable.name,
                type=variable.type,
                label=variable.label,
                mandatory=variable.mandatory,
                hidden=variable.hidden,
                visible=variable.visible,
                read_only=variable.read_only,
                help_text=variable.help_text,
                default_value=variable.default_value,
                description=variable.description,
                order=variable.order,
                reference_table=variable.reference_table,
                reference_qualifier=variable.reference_qualifier,
                max_length=variable.max_length,
                min=variable.min,
                max=variable.max,
                attributes=variable.attributes,
            ),
        )

        if not create_response.success or not create_response.variable_id:
            failed_variables.append(
                {
                    "index": index,
                    "name": variable.name,
                    "label": variable.label,
                    "message": create_response.message,
                }
            )
            if params.stop_on_error:
                break
            continue

        created_entry: Dict[str, Any] = {
            "index": index,
            "name": variable.name,
            "label": variable.label,
            "variable_id": create_response.variable_id,
            "details": create_response.details,
            "choices": [],
        }

        choice_failed = False
        for choice in variable.choices or []:
            choice_response = create_catalog_variable_choice(
                config,
                auth_manager,
                CreateCatalogVariableChoiceParams(
                    variable_id=create_response.variable_id,
                    value=choice.value,
                    label=choice.label,
                    order=choice.order,
                    inactive=choice.inactive,
                    attributes=choice.attributes,
                ),
            )
            if not choice_response.get("success"):
                failed_variables.append(
                    {
                        "index": index,
                        "name": variable.name,
                        "label": variable.label,
                        "message": (
                            f"Variable created but a choice failed: "
                            f"{choice_response.get('message')}"
                        ),
                    }
                )
                choice_failed = True
                if params.stop_on_error:
                    break
            else:
                created_entry["choices"].append(choice_response.get("choice"))

        created_variables.append(created_entry)
        if choice_failed and params.stop_on_error:
            break

    success = not failed_variables
    if created_variables and failed_variables:
        message = (
            f"Created {len(created_variables)} variables, but {len(failed_variables)} variable "
            "operations failed"
        )
    elif created_variables:
        message = f"Created {len(created_variables)} catalog item variables"
    else:
        message = "Failed to create any catalog item variables"

    return {
        "success": success,
        "message": message,
        "catalog_item_id": params.catalog_item_id,
        "created_variables": created_variables,
        "failed_variables": failed_variables,
        "created_count": len(created_variables),
        "failed_count": len(failed_variables),
    }


def add_standard_order_variables(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: AddStandardOrderVariablesParams,
) -> Dict[str, Any]:
    """Create a standard set of order-form variables for a catalog item."""
    size_choices = [
        CatalogVariableChoiceInput(
            value=_normalize_type_key(choice) or choice.strip().casefold().replace(" ", "_"),
            label=choice,
            order=params.first_order + ((index + 1) * 10),
        )
        for index, choice in enumerate(params.size_choices)
        if choice.strip()
    ]

    variable_order = params.first_order
    bulk_params = CreateCatalogItemVariablesParams(
        catalog_item_id=params.catalog_item_id,
        stop_on_error=params.stop_on_error,
        variables=[
            CatalogVariableDefinitionInput(
                name=params.quantity_name,
                type="integer",
                label=params.quantity_label,
                mandatory=True,
                default_value=str(params.quantity_default),
                min=params.quantity_min,
                max=params.quantity_max,
                order=variable_order,
            ),
            CatalogVariableDefinitionInput(
                name=params.size_name,
                type="select box",
                label=params.size_label,
                mandatory=True,
                order=variable_order + params.order_step,
                choices=size_choices,
            ),
            CatalogVariableDefinitionInput(
                name=params.date_name,
                type="date",
                label=params.date_label,
                mandatory=True,
                order=variable_order + (params.order_step * 2),
            ),
            CatalogVariableDefinitionInput(
                name=params.notes_name,
                type=params.notes_type,
                label=params.notes_label,
                mandatory=False,
                help_text=params.notes_help_text,
                order=variable_order + (params.order_step * 3),
            ),
        ],
    )
    result = create_catalog_item_variables(config, auth_manager, bulk_params)
    result["template"] = "standard_order_variables"
    return result


def set_catalog_variable_behavior(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: SetCatalogVariableBehaviorParams,
) -> CatalogItemVariableResponse:
    """Set simple behavior flags for an existing catalog variable."""
    return update_catalog_item_variable(
        config,
        auth_manager,
        UpdateCatalogItemVariableParams(
            variable_id=params.variable_id,
            mandatory=params.mandatory,
            hidden=params.hidden,
            visible=params.visible,
            read_only=params.read_only,
        ),
    )


def create_catalog_variable_behavior_policy(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateCatalogVariableBehaviorPolicyParams,
) -> Dict[str, Any]:
    """Create a Catalog UI Policy for simple variable behavior on a catalog item."""
    policy_body: Dict[str, Any] = {
        "applies_to": "item",
        "catalog_item": params.catalog_item_id,
        "short_description": params.short_description,
        "active": str(params.active).lower(),
        "on_load": str(params.on_load).lower(),
        "reverse_if_false": str(params.reverse_if_false).lower(),
        "run_scripts": str(params.run_scripts).lower(),
    }
    if params.catalog_conditions:
        policy_body["catalog_conditions"] = params.catalog_conditions
    if params.order is not None:
        policy_body["order"] = params.order
    if params.script_true is not None:
        policy_body["script_true"] = params.script_true
    if params.script_false is not None:
        policy_body["script_false"] = params.script_false

    try:
        policy_response = requests.post(
            f"{config.instance_url}/api/now/table/{CATALOG_UI_POLICY_TABLE}",
            json=policy_body,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        policy_response.raise_for_status()
        policy_record = policy_response.json().get("result", {})
        policy_id = str(policy_record.get("sys_id") or "").strip()
        if not policy_id:
            return {
                "success": False,
                "message": "Catalog UI policy was created but no sys_id was returned",
            }

        action_records: List[Dict[str, Any]] = []
        for action in params.actions:
            action_records.append(
                _create_catalog_ui_policy_action(
                    config,
                    auth_manager,
                    ui_policy_id=policy_id,
                    catalog_item_id=params.catalog_item_id,
                    action=action,
                )
            )

        return {
            "success": True,
            "message": f"Created catalog variable behavior policy {policy_id}",
            "catalog_ui_policy": policy_record,
            "catalog_ui_policy_actions": action_records,
        }
    except ValueError as error:
        logger.error("Error creating catalog variable behavior policy: %s", error)
        return {"success": False, "message": str(error)}
    except requests.RequestException as error:
        logger.error("Error creating catalog variable behavior policy: %s", error)
        return {
            "success": False,
            "message": (
                f"Error creating catalog variable behavior policy: {_extract_error_body(error)}"
            ),
        }
