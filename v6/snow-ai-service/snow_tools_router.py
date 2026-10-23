"""
snow_tools_router.py — FastAPI router exposing all vendor/servicenow_mcp tools as REST endpoints.
Covers: incidents, changes, changesets, catalog, SLA, KB, users, roles, approvals,
        business rules, UI policies, data policies, workflows, flows, ATF, CMDB,
        scoped apps, dashboards, PA, reports, scripts, scheduled jobs, assets,
        employee center, SPM/Agile, notifications, and more.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "vendor"))

import logging
from typing import Optional, Any, Dict
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from sn_adapter import get_sn_context

# ── tool module imports ──────────────────────────────────────────────────────
from servicenow_mcp.tools import (
    incident_tools, change_tools, changeset_tools, catalog_tools,
    catalog_admin_tools, catalog_variables, sla_tools, knowledge_base,
    user_tools, approval_tools, business_rule_tools, ui_policy_tools,
    data_policy_tools, workflow_tools, flow_tools, script_include_tools,
    background_job_tools, fix_script_tools, scheduled_job_tools,
    scheduled_script_execution_tools, server_side_script_tools,
    asset_management_tools, company_tools, department_tools, location_tools,
    notification_tools, problem_tools, request_tools, story_tools,
    epic_tools, scrum_task_tools, project_tools, release_tools,
    scoped_app_application_tools, scoped_app_writer_tools, scoped_app_reader_tools,
    scoped_app_menu_tools, scoped_app_metadata_tools, security_admin_tools,
    ui_action_tools, table_schema_tools, cost_center_tools,
    background_script_tools, atf_edit_tools,
)
from servicenow_mcp.tools.dashboards_reporting import dashboard_tools, pa_tools, report_tools
from servicenow_mcp.tools.employee_center import esc_announcement_tools, esc_page_tools, esc_topic_tools
from servicenow_mcp.tools.flow_designer import tools as flow_designer_tools
from servicenow_mcp.internal_tools import cmdb_ire_tools

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sn/tools", tags=["ServiceNow Tools"])


def _ctx(session_id: Optional[str]):
    if not session_id:
        raise HTTPException(status_code=401, detail="X-Session-ID header required")
    try:
        return get_sn_context(session_id)
    except KeyError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# Scripting writes must go through POST /snow/tools/run: confirmation (confirm=true), duplicate check and read-back
# (sn_tools). These raw routes have none of that, so they refuse scripting writes.
_SCRIPT_FAMILIES = ("business_rule", "script_include", "server_side_script", "ui_policy", "ui_policy_action",
                    "data_policy", "data_policy_rule", "ui_action", "fix_script", "catalog_client_script",
                    "scheduled_script_execution", "script_action")
GUARDED_WRITES = {f"{verb}_{fam}" for verb in ("create", "update", "delete") for fam in _SCRIPT_FAMILIES} | {
    "execute_background_script", "detailed_update_script_include", "convert_ui_policy_to_data_policy",
    "set_data_policy_client_enforcement"}


def _run(fn, config, auth, params=None):
    if getattr(fn, "__name__", "") in GUARDED_WRITES:
        raise HTTPException(status_code=403, detail=(
            f"'{fn.__name__}' changes ServiceNow configuration and is not available on /sn/tools. Use POST "
            "/snow/tools/run (needs confirm=true; duplicate check and read-back) or the requirement pipeline "
            "(/snow/requirements/plan → deploy)."))
    try:
        if params is not None:
            result = fn(config, auth, params)
        else:
            result = fn(config, auth)
        if hasattr(result, "model_dump"):
            return result.model_dump()
        if hasattr(result, "dict"):
            return result.dict()
        return result
    except Exception as e:
        logger.exception(f"Tool error in {fn.__name__}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── INCIDENTS ────────────────────────────────────────────────────────────────
@router.post("/incidents/create")
def api_create_incident(params: incident_tools.CreateIncidentParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(incident_tools.create_incident, config, auth, params)

@router.post("/incidents/list")
def api_list_incidents(params: incident_tools.ListIncidentsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(incident_tools.list_incidents, config, auth, params)

@router.post("/incidents/get")
def api_get_incident(params: incident_tools.GetIncidentByNumberParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(incident_tools.get_incident_by_number, config, auth, params)

@router.post("/incidents/update")
def api_update_incident(params: incident_tools.UpdateIncidentParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(incident_tools.update_incident, config, auth, params)

@router.post("/incidents/resolve")
def api_resolve_incident(params: incident_tools.ResolveIncidentParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(incident_tools.resolve_incident, config, auth, params)

@router.post("/incidents/comment")
def api_add_comment(params: incident_tools.AddCommentParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(incident_tools.add_comment, config, auth, params)


# ── CHANGES ──────────────────────────────────────────────────────────────────
@router.post("/changes/create")
def api_create_change(params: change_tools.CreateChangeRequestParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(change_tools.create_change_request, config, auth, params)

@router.post("/changes/list")
def api_list_changes(params: change_tools.ListChangeRequestsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(change_tools.list_change_requests, config, auth, params)

@router.post("/changes/get")
def api_get_change(params: change_tools.GetChangeRequestDetailsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(change_tools.get_change_request_details, config, auth, params)

@router.post("/changes/update")
def api_update_change(params: change_tools.UpdateChangeRequestParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(change_tools.update_change_request, config, auth, params)

@router.post("/changes/approve")
def api_approve_change(params: change_tools.ApproveChangeParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(change_tools.approve_change_request, config, auth, params)

@router.post("/changes/reject")
def api_reject_change(params: change_tools.RejectChangeParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(change_tools.reject_change_request, config, auth, params)

@router.post("/changes/submit")
def api_submit_change(params: change_tools.SubmitChangeForApprovalParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(change_tools.submit_change_for_approval, config, auth, params)

@router.post("/changes/add_task")
def api_add_change_task(params: change_tools.AddChangeTaskParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(change_tools.add_change_task, config, auth, params)


# ── CHANGESETS ───────────────────────────────────────────────────────────────
@router.post("/changesets/list")
def api_list_changesets(params: changeset_tools.ListChangesetsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(changeset_tools.list_changesets, config, auth, params)

@router.post("/changesets/get")
def api_get_changeset(params: changeset_tools.GetChangesetDetailsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(changeset_tools.get_changeset_details, config, auth, params)

@router.post("/changesets/create")
def api_create_changeset(params: changeset_tools.CreateChangesetParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(changeset_tools.create_changeset, config, auth, params)

@router.post("/changesets/update")
def api_update_changeset(params: changeset_tools.UpdateChangesetParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(changeset_tools.update_changeset, config, auth, params)

@router.post("/changesets/commit")
def api_commit_changeset(params: changeset_tools.CommitChangesetParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(changeset_tools.commit_changeset, config, auth, params)

@router.post("/changesets/publish")
def api_publish_changeset(params: changeset_tools.PublishChangesetParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(changeset_tools.publish_changeset, config, auth, params)

@router.post("/changesets/add_file")
def api_add_file_to_changeset(params: changeset_tools.AddFileToChangesetParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(changeset_tools.add_file_to_changeset, config, auth, params)


# ── CATALOG (browse) ─────────────────────────────────────────────────────────
@router.post("/catalog/list_items")
def api_list_catalog_items(params: catalog_tools.ListCatalogItemsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(catalog_tools.list_catalog_items, config, auth, params)

@router.post("/catalog/get_item")
def api_get_catalog_item(params: catalog_tools.GetCatalogItemParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(catalog_tools.get_catalog_item, config, auth, params)

@router.post("/catalog/get_item_variables")
def api_get_catalog_item_variables(params: catalog_tools.GetCatalogItemParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(catalog_tools.get_catalog_item_variables, config, auth, params)

@router.post("/catalog/list_categories")
def api_list_catalog_categories(params: catalog_tools.ListCatalogCategoriesParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(catalog_tools.list_catalog_categories, config, auth, params)

@router.post("/catalog/create_category")
def api_create_catalog_category(params: catalog_tools.CreateCatalogCategoryParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(catalog_tools.create_catalog_category, config, auth, params)

@router.post("/catalog/update_category")
def api_update_catalog_category(params: catalog_tools.UpdateCatalogCategoryParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(catalog_tools.update_catalog_category, config, auth, params)

@router.post("/catalog/move_items")
def api_move_catalog_items(params: catalog_tools.MoveCatalogItemsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(catalog_tools.move_catalog_items, config, auth, params)


# ── CATALOG ADMIN ────────────────────────────────────────────────────────────
@router.post("/catalog/admin/list_catalogs")
def api_list_catalogs(params: catalog_admin_tools.ListCatalogsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(catalog_admin_tools.list_catalogs, config, auth, params)

@router.post("/catalog/admin/get_catalog")
def api_get_catalog(params: catalog_admin_tools.GetCatalogParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(catalog_admin_tools.get_catalog, config, auth, params)

@router.post("/catalog/admin/create_catalog")
def api_create_catalog(params: catalog_admin_tools.CreateCatalogParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(catalog_admin_tools.create_catalog, config, auth, params)

@router.post("/catalog/admin/update_catalog")
def api_update_catalog(params: catalog_admin_tools.UpdateCatalogParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(catalog_admin_tools.update_catalog, config, auth, params)

@router.post("/catalog/admin/create_item")
def api_create_catalog_item(params: catalog_admin_tools.CreateCatalogItemParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(catalog_admin_tools.create_catalog_item, config, auth, params)

@router.post("/catalog/admin/publish_item")
def api_publish_catalog_item(params: catalog_admin_tools.PublishCatalogItemParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(catalog_admin_tools.publish_catalog_item, config, auth, params)

@router.post("/catalog/admin/list_client_scripts")
def api_list_catalog_client_scripts(params: catalog_admin_tools.ListCatalogClientScriptsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(catalog_admin_tools.list_catalog_client_scripts, config, auth, params)

@router.post("/catalog/admin/get_client_script")
def api_get_catalog_client_script(params: catalog_admin_tools.GetCatalogClientScriptParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(catalog_admin_tools.get_catalog_client_script, config, auth, params)

@router.post("/catalog/admin/create_client_script")
def api_create_catalog_client_script(params: catalog_admin_tools.CreateCatalogClientScriptParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(catalog_admin_tools.create_catalog_client_script, config, auth, params)

@router.post("/catalog/admin/update_client_script")
def api_update_catalog_client_script(params: catalog_admin_tools.UpdateCatalogClientScriptParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(catalog_admin_tools.update_catalog_client_script, config, auth, params)


# ── CATALOG VARIABLES ────────────────────────────────────────────────────────
@router.post("/catalog/variables/create")
def api_create_catalog_variable(params: catalog_variables.CreateCatalogItemVariableParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(catalog_variables.create_catalog_item_variable, config, auth, params)

@router.post("/catalog/variables/list")
def api_list_catalog_variables(params: catalog_variables.ListCatalogItemVariablesParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(catalog_variables.list_catalog_item_variables, config, auth, params)

@router.post("/catalog/variables/update")
def api_update_catalog_variable(params: catalog_variables.UpdateCatalogItemVariableParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(catalog_variables.update_catalog_item_variable, config, auth, params)

@router.post("/catalog/variables/delete")
def api_delete_catalog_variable(params: catalog_variables.DeleteCatalogItemVariableParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(catalog_variables.delete_catalog_item_variable, config, auth, params)

@router.post("/catalog/variables/get_metadata")
def api_get_catalog_variable_metadata(params: catalog_variables.GetCatalogVariableMetadataParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(catalog_variables.get_catalog_variable_metadata, config, auth, params)

@router.post("/catalog/variables/create_choice")
def api_create_catalog_variable_choice(params: catalog_variables.CreateCatalogVariableChoiceParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(catalog_variables.create_catalog_variable_choice, config, auth, params)

@router.post("/catalog/variables/create_batch")
def api_create_catalog_item_variables(params: catalog_variables.CreateCatalogItemVariablesParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(catalog_variables.create_catalog_item_variables, config, auth, params)


# ── SLA ──────────────────────────────────────────────────────────────────────
@router.post("/sla/list_schedules")
def api_list_sla_schedules(params: sla_tools.ListSLASchedulesParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(sla_tools.list_sla_schedules, config, auth, params)

@router.post("/sla/list")
def api_list_slas(params: sla_tools.ListSLAsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(sla_tools.list_slas, config, auth, params)

@router.post("/sla/get")
def api_get_sla(params: sla_tools.GetSLAParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(sla_tools.get_sla, config, auth, params)

@router.post("/sla/create")
def api_create_sla(params: sla_tools.CreateSLAParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(sla_tools.create_sla, config, auth, params)

@router.post("/sla/update")
def api_update_sla(params: sla_tools.UpdateSLAParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(sla_tools.update_sla, config, auth, params)


# ── KNOWLEDGE BASE ────────────────────────────────────────────────────────────
@router.post("/kb/create_kb")
def api_create_knowledge_base(params: knowledge_base.CreateKnowledgeBaseParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(knowledge_base.create_knowledge_base, config, auth, params)

@router.post("/kb/list_kbs")
def api_list_knowledge_bases(params: knowledge_base.ListKnowledgeBasesParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(knowledge_base.list_knowledge_bases, config, auth, params)

@router.post("/kb/create_category")
def api_create_kb_category(params: knowledge_base.CreateCategoryParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(knowledge_base.create_category, config, auth, params)

@router.post("/kb/list_categories")
def api_list_kb_categories(params: knowledge_base.ListCategoriesParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(knowledge_base.list_categories, config, auth, params)

@router.post("/kb/create_article")
def api_create_article(params: knowledge_base.CreateArticleParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(knowledge_base.create_article, config, auth, params)

@router.post("/kb/list_articles")
def api_list_articles(params: knowledge_base.ListArticlesParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(knowledge_base.list_articles, config, auth, params)

@router.post("/kb/get_article")
def api_get_article(params: knowledge_base.GetArticleParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(knowledge_base.get_article, config, auth, params)

@router.post("/kb/update_article")
def api_update_article(params: knowledge_base.UpdateArticleParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(knowledge_base.update_article, config, auth, params)

@router.post("/kb/publish_article")
def api_publish_article(params: knowledge_base.PublishArticleParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(knowledge_base.publish_article, config, auth, params)


# ── USERS ────────────────────────────────────────────────────────────────────
@router.post("/users/create")
def api_create_user(params: user_tools.CreateUserParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(user_tools.create_user, config, auth, params)

@router.post("/users/list")
def api_list_users(params: user_tools.ListUsersParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(user_tools.list_users, config, auth, params)

@router.post("/users/get")
def api_get_user(params: user_tools.GetUserParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(user_tools.get_user, config, auth, params)

@router.post("/users/update")
def api_update_user(params: user_tools.UpdateUserParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(user_tools.update_user, config, auth, params)

@router.post("/users/assign_roles")
def api_assign_roles(params: user_tools.AddGroupMembersParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(user_tools.assign_roles_to_user, config, auth, params)

@router.post("/users/check_role")
def api_check_role(params: user_tools.RemoveGroupMembersParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(user_tools.check_user_has_role, config, auth, params)

@router.post("/users/create_group")
def api_create_group(params: user_tools.CreateGroupParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(user_tools.create_group, config, auth, params)

@router.post("/users/list_groups")
def api_list_groups(params: user_tools.ListGroupsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(user_tools.list_groups, config, auth, params)

@router.post("/users/update_group")
def api_update_group(params: user_tools.UpdateGroupParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(user_tools.update_group, config, auth, params)

@router.post("/users/add_group_members")
def api_add_group_members(params: user_tools.AddGroupMembersParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(user_tools.add_group_members, config, auth, params)

@router.post("/users/remove_group_members")
def api_remove_group_members(params: user_tools.RemoveGroupMembersParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(user_tools.remove_group_members, config, auth, params)


# ── APPROVALS ────────────────────────────────────────────────────────────────
@router.post("/approvals/list")
def api_list_approvals(params: approval_tools.ListApprovalRecordsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(approval_tools.list_approval_records, config, auth, params)

@router.post("/approvals/get")
def api_get_approval(params: approval_tools.GetApprovalRecordParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(approval_tools.get_approval_record, config, auth, params)

@router.post("/approvals/create")
def api_create_approval(params: approval_tools.CreateApprovalRecordParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(approval_tools.create_approval_record, config, auth, params)

@router.post("/approvals/update")
def api_update_approval(params: approval_tools.UpdateApprovalRecordParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(approval_tools.update_approval_record, config, auth, params)

@router.post("/approvals/approve")
def api_approve(params: approval_tools.ApprovalActionParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(approval_tools.approve_approval, config, auth, params)

@router.post("/approvals/reject")
def api_reject_approval(params: approval_tools.ApprovalActionParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(approval_tools.reject_approval, config, auth, params)

@router.post("/approvals/cancel")
def api_cancel_approval(params: approval_tools.ApprovalActionParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(approval_tools.cancel_approval, config, auth, params)

@router.post("/approvals/delete")
def api_delete_approval(params: approval_tools.DeleteApprovalRecordParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(approval_tools.delete_approval_record, config, auth, params)

@router.post("/approvals/history")
def api_list_approval_history(params: approval_tools.ListApprovalHistoryParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(approval_tools.list_approval_history, config, auth, params)


# ── BUSINESS RULES ────────────────────────────────────────────────────────────
@router.post("/business_rules/create")
def api_create_business_rule(params: business_rule_tools.CreateBusinessRuleParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(business_rule_tools.create_business_rule, config, auth, params)

@router.post("/business_rules/list")
def api_list_business_rules(params: business_rule_tools.ListBusinessRulesParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(business_rule_tools.list_business_rules, config, auth, params)

@router.post("/business_rules/get")
def api_get_business_rule(params: business_rule_tools.GetBusinessRuleParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(business_rule_tools.get_business_rule, config, auth, params)

@router.post("/business_rules/update")
def api_update_business_rule(params: business_rule_tools.UpdateBusinessRuleParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(business_rule_tools.update_business_rule, config, auth, params)

@router.post("/business_rules/delete")
def api_delete_business_rule(params: business_rule_tools.DeleteBusinessRuleParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(business_rule_tools.delete_business_rule, config, auth, params)


# ── UI POLICIES ───────────────────────────────────────────────────────────────
@router.post("/ui_policies/create")
def api_create_ui_policy(params: ui_policy_tools.CreateUIPolicyParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(ui_policy_tools.create_ui_policy, config, auth, params)

@router.post("/ui_policies/list")
def api_list_ui_policies(params: ui_policy_tools.ListUIPoliciesParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(ui_policy_tools.list_ui_policies, config, auth, params)

@router.post("/ui_policies/get")
def api_get_ui_policy(params: ui_policy_tools.GetUIPolicyParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(ui_policy_tools.get_ui_policy, config, auth, params)

@router.post("/ui_policies/update")
def api_update_ui_policy(params: ui_policy_tools.UpdateUIPolicyParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(ui_policy_tools.update_ui_policy, config, auth, params)

@router.post("/ui_policies/delete")
def api_delete_ui_policy(params: ui_policy_tools.DeleteUIPolicyParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(ui_policy_tools.delete_ui_policy, config, auth, params)

@router.post("/ui_policies/actions/create")
def api_create_ui_policy_action(params: ui_policy_tools.CreateUIPolicyActionParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(ui_policy_tools.create_ui_policy_action, config, auth, params)

@router.post("/ui_policies/actions/list")
def api_list_ui_policy_actions(params: ui_policy_tools.ListUIPolicyActionsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(ui_policy_tools.list_ui_policy_actions, config, auth, params)

@router.post("/ui_policies/actions/get")
def api_get_ui_policy_action(params: ui_policy_tools.GetUIPolicyActionParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(ui_policy_tools.get_ui_policy_action, config, auth, params)

@router.post("/ui_policies/actions/update")
def api_update_ui_policy_action(params: ui_policy_tools.UpdateUIPolicyActionParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(ui_policy_tools.update_ui_policy_action, config, auth, params)

@router.post("/ui_policies/actions/delete")
def api_delete_ui_policy_action(params: ui_policy_tools.DeleteUIPolicyActionParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(ui_policy_tools.delete_ui_policy_action, config, auth, params)


# ── DATA POLICIES ─────────────────────────────────────────────────────────────
@router.post("/data_policies/create")
def api_create_data_policy(params: data_policy_tools.CreateDataPolicyParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(data_policy_tools.create_data_policy, config, auth, params)

@router.post("/data_policies/list")
def api_list_data_policies(params: data_policy_tools.ListDataPoliciesParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(data_policy_tools.list_data_policies, config, auth, params)

@router.post("/data_policies/get")
def api_get_data_policy(params: data_policy_tools.GetDataPolicyParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(data_policy_tools.get_data_policy, config, auth, params)

@router.post("/data_policies/update")
def api_update_data_policy(params: data_policy_tools.UpdateDataPolicyParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(data_policy_tools.update_data_policy, config, auth, params)

@router.post("/data_policies/delete")
def api_delete_data_policy(params: data_policy_tools.DeleteDataPolicyParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(data_policy_tools.delete_data_policy, config, auth, params)

@router.post("/data_policies/rules/create")
def api_create_data_policy_rule(params: data_policy_tools.CreateDataPolicyRuleParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(data_policy_tools.create_data_policy_rule, config, auth, params)

@router.post("/data_policies/rules/list")
def api_list_data_policy_rules(params: data_policy_tools.ListDataPolicyRulesParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(data_policy_tools.list_data_policy_rules, config, auth, params)

@router.post("/data_policies/rules/get")
def api_get_data_policy_rule(params: data_policy_tools.GetDataPolicyRuleParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(data_policy_tools.get_data_policy_rule, config, auth, params)

@router.post("/data_policies/rules/update")
def api_update_data_policy_rule(params: data_policy_tools.UpdateDataPolicyRuleParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(data_policy_tools.update_data_policy_rule, config, auth, params)

@router.post("/data_policies/rules/delete")
def api_delete_data_policy_rule(params: data_policy_tools.DeleteDataPolicyRuleParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(data_policy_tools.delete_data_policy_rule, config, auth, params)

@router.post("/data_policies/convert_ui_policy")
def api_convert_ui_policy_to_data_policy(params: data_policy_tools.ConvertUIPolicyToDataPolicyParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(data_policy_tools.convert_ui_policy_to_data_policy, config, auth, params)


# ── WORKFLOWS ─────────────────────────────────────────────────────────────────
@router.post("/workflows/create")
def api_create_workflow(params: workflow_tools.CreateWorkflowParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(workflow_tools.create_workflow, config, auth, params)

@router.post("/workflows/list")
def api_list_workflows(params: workflow_tools.ListWorkflowsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(workflow_tools.list_workflows, config, auth, params)

@router.post("/workflows/get")
def api_get_workflow(params: workflow_tools.GetWorkflowDetailsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(workflow_tools.get_workflow_details, config, auth, params)

@router.post("/workflows/update")
def api_update_workflow(params: workflow_tools.UpdateWorkflowParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(workflow_tools.update_workflow, config, auth, params)

@router.post("/workflows/delete")
def api_delete_workflow(params: workflow_tools.DeleteWorkflowParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(workflow_tools.delete_workflow, config, auth, params)

@router.post("/workflows/activate")
def api_activate_workflow(params: workflow_tools.ActivateWorkflowParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(workflow_tools.activate_workflow, config, auth, params)

@router.post("/workflows/deactivate")
def api_deactivate_workflow(params: workflow_tools.DeactivateWorkflowParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(workflow_tools.deactivate_workflow, config, auth, params)

@router.post("/workflows/publish")
def api_publish_workflow(params: workflow_tools.PublishWorkflowParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(workflow_tools.publish_workflow, config, auth, params)

@router.post("/workflows/list_versions")
def api_list_workflow_versions(params: workflow_tools.ListWorkflowVersionsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(workflow_tools.list_workflow_versions, config, auth, params)

@router.post("/workflows/add_activity")
def api_add_workflow_activity(params: workflow_tools.AddWorkflowActivityParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(workflow_tools.add_workflow_activity, config, auth, params)

@router.post("/workflows/update_activity")
def api_update_workflow_activity(params: workflow_tools.UpdateWorkflowActivityParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(workflow_tools.update_workflow_activity, config, auth, params)

@router.post("/workflows/delete_activity")
def api_delete_workflow_activity(params: workflow_tools.DeleteWorkflowActivityParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(workflow_tools.delete_workflow_activity, config, auth, params)


# ── FLOW TOOLS ────────────────────────────────────────────────────────────────
@router.post("/flows/create")
def api_create_flow(params: flow_tools.CreateFlowParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(flow_tools.create_flow, config, auth, params)

@router.post("/flows/validate_json")
def api_validate_flow_json(params: flow_tools.ValidateFlowJsonParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(flow_tools.validate_flow_json, config, auth, params)

@router.post("/flows/hint_json")
def api_hint_flow_json(params: flow_tools.HintFlowJsonParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(flow_tools.hint_flow_json, config, auth, params)


# ── FLOW DESIGNER TOOLS ───────────────────────────────────────────────────────
@router.post("/flow_designer/create_flow")
def api_fd_create_flow(params: flow_designer_tools.CreateFlowParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(flow_designer_tools.create_flow, config, auth, params)

@router.post("/flow_designer/list_flow_actions")
def api_fd_list_flow_actions(params: flow_designer_tools.ListFlowActionsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(flow_designer_tools.list_flow_actions, config, auth, params)

@router.post("/flow_designer/edit_flow")
def api_fd_edit_flow(params: flow_designer_tools.EditFlowParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(flow_designer_tools.edit_flow, config, auth, params)

@router.post("/flow_designer/validate_flow_json")
def api_fd_validate_flow_json(params: flow_designer_tools.ValidateFlowJsonParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(flow_designer_tools.validate_flow_json, config, auth, params)


# ── SCRIPT INCLUDES ───────────────────────────────────────────────────────────
@router.post("/script_includes/create")
def api_create_script_include(params: script_include_tools.CreateScriptIncludeParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(script_include_tools.create_script_include, config, auth, params)

@router.post("/script_includes/list")
def api_list_script_includes(params: script_include_tools.ListScriptIncludesParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(script_include_tools.list_script_includes, config, auth, params)

@router.post("/script_includes/get")
def api_get_script_include(params: script_include_tools.GetScriptIncludeParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(script_include_tools.get_script_include, config, auth, params)

@router.post("/script_includes/update")
def api_update_script_include(params: script_include_tools.UpdateScriptIncludeParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(script_include_tools.update_script_include, config, auth, params)

@router.post("/script_includes/delete")
def api_delete_script_include(params: script_include_tools.DeleteScriptIncludeParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(script_include_tools.delete_script_include, config, auth, params)


# ── BACKGROUND JOBS ───────────────────────────────────────────────────────────
@router.post("/background_jobs/create")
def api_create_background_job(params: background_job_tools.CreateBackgroundJobParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(background_job_tools.create_background_job, config, auth, params)

@router.post("/background_jobs/list")
def api_list_background_jobs(params: background_job_tools.ListBackgroundJobsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(background_job_tools.list_background_jobs, config, auth, params)

@router.post("/background_jobs/get")
def api_get_background_job(params: background_job_tools.GetBackgroundJobParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(background_job_tools.get_background_job, config, auth, params)

@router.post("/background_jobs/update")
def api_update_background_job(params: background_job_tools.UpdateBackgroundJobParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(background_job_tools.update_background_job, config, auth, params)

@router.post("/background_jobs/delete")
def api_delete_background_job(params: background_job_tools.DeleteBackgroundJobParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(background_job_tools.delete_background_job, config, auth, params)


# ── FIX SCRIPTS ───────────────────────────────────────────────────────────────
@router.post("/fix_scripts/create")
def api_create_fix_script(params: fix_script_tools.CreateFixScriptParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(fix_script_tools.create_fix_script, config, auth, params)

@router.post("/fix_scripts/list")
def api_list_fix_scripts(params: fix_script_tools.ListFixScriptsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(fix_script_tools.list_fix_scripts, config, auth, params)

@router.post("/fix_scripts/get")
def api_get_fix_script(params: fix_script_tools.GetFixScriptParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(fix_script_tools.get_fix_script, config, auth, params)

@router.post("/fix_scripts/update")
def api_update_fix_script(params: fix_script_tools.UpdateFixScriptParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(fix_script_tools.update_fix_script, config, auth, params)

@router.post("/fix_scripts/delete")
def api_delete_fix_script(params: fix_script_tools.DeleteFixScriptParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(fix_script_tools.delete_fix_script, config, auth, params)


# ── SCHEDULED JOBS ────────────────────────────────────────────────────────────
@router.post("/scheduled_jobs/create")
def api_create_scheduled_job(params: scheduled_job_tools.CreateScheduledJobParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(scheduled_job_tools.create_scheduled_job, config, auth, params)

@router.post("/scheduled_jobs/list")
def api_list_scheduled_jobs(params: scheduled_job_tools.ListScheduledJobsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(scheduled_job_tools.list_scheduled_jobs, config, auth, params)

@router.post("/scheduled_jobs/get")
def api_get_scheduled_job(params: scheduled_job_tools.GetScheduledJobParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(scheduled_job_tools.get_scheduled_job, config, auth, params)

@router.post("/scheduled_jobs/update")
def api_update_scheduled_job(params: scheduled_job_tools.UpdateScheduledJobParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(scheduled_job_tools.update_scheduled_job, config, auth, params)

@router.post("/scheduled_jobs/delete")
def api_delete_scheduled_job(params: scheduled_job_tools.DeleteScheduledJobParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(scheduled_job_tools.delete_scheduled_job, config, auth, params)


# ── SCHEDULED SCRIPT EXECUTIONS ───────────────────────────────────────────────
@router.post("/scheduled_scripts/create")
def api_create_scheduled_script(params: scheduled_script_execution_tools.CreateScheduledScriptExecutionParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(scheduled_script_execution_tools.create_scheduled_script_execution, config, auth, params)

@router.post("/scheduled_scripts/list")
def api_list_scheduled_scripts(params: scheduled_script_execution_tools.ListScheduledScriptExecutionsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(scheduled_script_execution_tools.list_scheduled_script_executions, config, auth, params)

@router.post("/scheduled_scripts/get")
def api_get_scheduled_script(params: scheduled_script_execution_tools.GetScheduledScriptExecutionParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(scheduled_script_execution_tools.get_scheduled_script_execution, config, auth, params)

@router.post("/scheduled_scripts/update")
def api_update_scheduled_script(params: scheduled_script_execution_tools.UpdateScheduledScriptExecutionParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(scheduled_script_execution_tools.update_scheduled_script_execution, config, auth, params)

@router.post("/scheduled_scripts/delete")
def api_delete_scheduled_script(params: scheduled_script_execution_tools.DeleteScheduledScriptExecutionParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(scheduled_script_execution_tools.delete_scheduled_script_execution, config, auth, params)


# ── SERVER-SIDE SCRIPTS ───────────────────────────────────────────────────────
@router.post("/server_scripts/create")
def api_create_server_script(params: server_side_script_tools.CreateServerSideScriptParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(server_side_script_tools.create_server_side_script, config, auth, params)

@router.post("/server_scripts/list")
def api_list_server_scripts(params: server_side_script_tools.ListServerSideScriptsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(server_side_script_tools.list_server_side_scripts, config, auth, params)

@router.post("/server_scripts/get")
def api_get_server_script(params: server_side_script_tools.GetServerSideScriptParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(server_side_script_tools.get_server_side_script, config, auth, params)

@router.post("/server_scripts/update")
def api_update_server_script(params: server_side_script_tools.UpdateServerSideScriptParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(server_side_script_tools.update_server_side_script, config, auth, params)

@router.post("/server_scripts/delete")
def api_delete_server_script(params: server_side_script_tools.DeleteServerSideScriptParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(server_side_script_tools.delete_server_side_script, config, auth, params)

@router.post("/server_scripts/type_metadata")
def api_get_server_script_type_metadata(params: server_side_script_tools.GetServerSideScriptTypeMetadataParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(server_side_script_tools.get_server_side_script_type_metadata, config, auth, params)


# ── BACKGROUND SCRIPTS ────────────────────────────────────────────────────────
@router.post("/background_scripts/execute")
def api_execute_background_script(params: background_script_tools.ExecuteBackgroundScriptParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(background_script_tools.execute_background_script, config, auth, params)


# ── ASSETS ────────────────────────────────────────────────────────────────────
@router.post("/assets/create_hardware")
def api_create_hardware_asset(params: asset_management_tools.CreateHardwareAssetParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(asset_management_tools.create_hardware_asset, config, auth, params)

@router.post("/assets/create_software")
def api_create_software_asset(params: asset_management_tools.CreateSoftwareAssetParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(asset_management_tools.create_software_asset, config, auth, params)

@router.post("/assets/list")
def api_list_assets(params: asset_management_tools.ListAssetsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(asset_management_tools.list_assets, config, auth, params)

@router.post("/assets/get")
def api_get_asset(params: asset_management_tools.GetAssetParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(asset_management_tools.get_asset, config, auth, params)

@router.post("/assets/update")
def api_update_asset(params: asset_management_tools.UpdateAssetParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(asset_management_tools.update_asset, config, auth, params)

@router.post("/assets/delete")
def api_delete_asset(params: asset_management_tools.DeleteAssetParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(asset_management_tools.delete_asset, config, auth, params)

@router.post("/assets/assign")
def api_assign_asset(params: asset_management_tools.AssignAssetParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(asset_management_tools.assign_asset, config, auth, params)

@router.post("/assets/return")
def api_return_asset(params: asset_management_tools.ReturnAssetParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(asset_management_tools.return_asset, config, auth, params)

@router.post("/assets/reserve")
def api_reserve_asset(params: asset_management_tools.ReserveAssetParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(asset_management_tools.reserve_asset, config, auth, params)

@router.post("/assets/transfer")
def api_transfer_asset(params: asset_management_tools.TransferAssetParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(asset_management_tools.transfer_asset, config, auth, params)

@router.post("/assets/lifecycle_action")
def api_asset_lifecycle_action(params: asset_management_tools.AssetLifecycleActionParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(asset_management_tools.perform_asset_lifecycle_action, config, auth, params)

@router.post("/assets/stockrooms/list")
def api_list_stockrooms(params: asset_management_tools.ListStockroomsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(asset_management_tools.list_stockrooms, config, auth, params)

@router.post("/assets/stockrooms/get")
def api_get_stockroom(params: asset_management_tools.GetStockroomParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(asset_management_tools.get_stockroom, config, auth, params)

@router.post("/assets/stockrooms/create")
def api_create_stockroom(params: asset_management_tools.CreateStockroomParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(asset_management_tools.create_stockroom, config, auth, params)

@router.post("/assets/models/list")
def api_list_asset_models(params: asset_management_tools.ListAssetModelsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(asset_management_tools.list_asset_models, config, auth, params)

@router.post("/assets/models/create")
def api_create_asset_model(params: asset_management_tools.CreateAssetModelParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(asset_management_tools.create_asset_model, config, auth, params)

@router.post("/assets/models/update")
def api_update_asset_model(params: asset_management_tools.UpdateAssetModelParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(asset_management_tools.update_asset_model, config, auth, params)


# ── COMPANIES ─────────────────────────────────────────────────────────────────
@router.post("/companies/create")
def api_create_company(params: company_tools.CreateCompanyParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(company_tools.create_company, config, auth, params)

@router.post("/companies/list")
def api_list_companies(params: company_tools.ListCompaniesParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(company_tools.list_companies, config, auth, params)

@router.post("/companies/get")
def api_get_company(params: company_tools.GetCompanyParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(company_tools.get_company, config, auth, params)

@router.post("/companies/update")
def api_update_company(params: company_tools.UpdateCompanyParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(company_tools.update_company, config, auth, params)

@router.post("/companies/delete")
def api_delete_company(params: company_tools.DeleteCompanyParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(company_tools.delete_company, config, auth, params)


# ── DEPARTMENTS ───────────────────────────────────────────────────────────────
@router.post("/departments/create")
def api_create_department(params: department_tools.CreateDepartmentParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(department_tools.create_department, config, auth, params)

@router.post("/departments/list")
def api_list_departments(params: department_tools.ListDepartmentsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(department_tools.list_departments, config, auth, params)

@router.post("/departments/get")
def api_get_department(params: department_tools.GetDepartmentParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(department_tools.get_department, config, auth, params)

@router.post("/departments/update")
def api_update_department(params: department_tools.UpdateDepartmentParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(department_tools.update_department, config, auth, params)

@router.post("/departments/delete")
def api_delete_department(params: department_tools.DeleteDepartmentParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(department_tools.delete_department, config, auth, params)


# ── LOCATIONS ─────────────────────────────────────────────────────────────────
@router.post("/locations/create")
def api_create_location(params: location_tools.CreateLocationParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(location_tools.create_location, config, auth, params)

@router.post("/locations/list")
def api_list_locations(params: location_tools.ListLocationsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(location_tools.list_locations, config, auth, params)

@router.post("/locations/get")
def api_get_location(params: location_tools.GetLocationParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(location_tools.get_location, config, auth, params)

@router.post("/locations/update")
def api_update_location(params: location_tools.UpdateLocationParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(location_tools.update_location, config, auth, params)

@router.post("/locations/delete")
def api_delete_location(params: location_tools.DeleteLocationParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(location_tools.delete_location, config, auth, params)


# ── COST CENTERS ──────────────────────────────────────────────────────────────
@router.post("/cost_centers/create")
def api_create_cost_center(params: cost_center_tools.CreateCostCenterParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(cost_center_tools.create_cost_center, config, auth, params)

@router.post("/cost_centers/list")
def api_list_cost_centers(params: cost_center_tools.ListCostCentersParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(cost_center_tools.list_cost_centers, config, auth, params)

@router.post("/cost_centers/get")
def api_get_cost_center(params: cost_center_tools.GetCostCenterParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(cost_center_tools.get_cost_center, config, auth, params)

@router.post("/cost_centers/update")
def api_update_cost_center(params: cost_center_tools.UpdateCostCenterParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(cost_center_tools.update_cost_center, config, auth, params)

@router.post("/cost_centers/delete")
def api_delete_cost_center(params: cost_center_tools.DeleteCostCenterParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(cost_center_tools.delete_cost_center, config, auth, params)


# ── NOTIFICATIONS ─────────────────────────────────────────────────────────────
@router.post("/notifications/create")
def api_create_notification(params: notification_tools.CreateNotificationParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(notification_tools.create_notification, config, auth, params)

@router.post("/notifications/list")
def api_list_notifications(params: notification_tools.ListNotificationsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(notification_tools.list_notifications, config, auth, params)

@router.post("/notifications/get")
def api_get_notification(params: notification_tools.GetNotificationParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(notification_tools.get_notification, config, auth, params)

@router.post("/notifications/update")
def api_update_notification(params: notification_tools.UpdateNotificationParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(notification_tools.update_notification, config, auth, params)

@router.post("/notifications/email_templates/create")
def api_create_email_template(params: notification_tools.CreateEmailTemplateParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(notification_tools.create_email_template, config, auth, params)

@router.post("/notifications/email_templates/list")
def api_list_email_templates(params: notification_tools.ListEmailTemplatesParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(notification_tools.list_email_templates, config, auth, params)

@router.post("/notifications/email_templates/get")
def api_get_email_template(params: notification_tools.GetEmailTemplateParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(notification_tools.get_email_template, config, auth, params)

@router.post("/notifications/email_templates/update")
def api_update_email_template(params: notification_tools.UpdateEmailTemplateParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(notification_tools.update_email_template, config, auth, params)

@router.post("/notifications/events/create")
def api_create_event_definition(params: notification_tools.CreateEventDefinitionParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(notification_tools.create_event_definition, config, auth, params)

@router.post("/notifications/events/list")
def api_list_event_definitions(params: notification_tools.ListEventDefinitionsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(notification_tools.list_event_definitions, config, auth, params)

@router.post("/notifications/events/queue")
def api_queue_event(params: notification_tools.QueueEventParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(notification_tools.queue_event, config, auth, params)

@router.post("/notifications/script_actions/create")
def api_create_script_action(params: notification_tools.CreateScriptActionParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(notification_tools.create_script_action, config, auth, params)

@router.post("/notifications/script_actions/list")
def api_list_script_actions(params: notification_tools.ListScriptActionsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(notification_tools.list_script_actions, config, auth, params)

@router.post("/notifications/script_actions/delete")
def api_delete_script_action(params: notification_tools.DeleteScriptActionParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(notification_tools.delete_script_action, config, auth, params)


# ── PROBLEMS ──────────────────────────────────────────────────────────────────
@router.post("/problems/create")
def api_create_problem(params: problem_tools.CreateProblemParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(problem_tools.create_problem, config, auth, params)

@router.post("/problems/list")
def api_list_problems(params: problem_tools.ListProblemsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(problem_tools.list_problems, config, auth, params)

@router.post("/problems/get")
def api_get_problem(params: problem_tools.GetProblemParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(problem_tools.get_problem, config, auth, params)

@router.post("/problems/update")
def api_update_problem(params: problem_tools.UpdateProblemParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(problem_tools.update_problem, config, auth, params)

@router.post("/problems/resolve")
def api_resolve_problem(params: problem_tools.ResolveProblemParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(problem_tools.resolve_problem, config, auth, params)

@router.post("/problems/link_to_incident")
def api_link_problem_to_incident(params: problem_tools.LinkProblemToIncidentParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(problem_tools.link_problem_to_incident, config, auth, params)

@router.post("/problems/tasks/create")
def api_create_problem_task(params: problem_tools.CreateProblemTaskParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(problem_tools.create_problem_task, config, auth, params)

@router.post("/problems/tasks/list")
def api_list_problem_tasks(params: problem_tools.ListProblemTasksParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(problem_tools.list_problem_tasks, config, auth, params)

@router.post("/problems/tasks/resolve")
def api_resolve_problem_task(params: problem_tools.ResolveProblemTaskParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(problem_tools.resolve_problem_task, config, auth, params)


# ── REQUESTS ──────────────────────────────────────────────────────────────────
@router.post("/requests/create")
def api_create_request(params: request_tools.CreateRequestParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(request_tools.create_request, config, auth, params)

@router.post("/requests/list")
def api_list_requests(params: request_tools.ListRequestsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(request_tools.list_requests, config, auth, params)

@router.post("/requests/get")
def api_get_request(params: request_tools.GetRequestParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(request_tools.get_request, config, auth, params)

@router.post("/requests/update")
def api_update_request(params: request_tools.UpdateRequestParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(request_tools.update_request, config, auth, params)

@router.post("/requests/items/create")
def api_create_requested_item(params: request_tools.CreateRequestedItemParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(request_tools.create_requested_item, config, auth, params)

@router.post("/requests/items/list")
def api_list_requested_items(params: request_tools.ListRequestedItemsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(request_tools.list_requested_items, config, auth, params)

@router.post("/requests/items/get")
def api_get_requested_item(params: request_tools.GetRequestedItemParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(request_tools.get_requested_item, config, auth, params)

@router.post("/requests/fulfillment_status")
def api_get_fulfillment_status(params: request_tools.GetRequestFulfillmentStatusParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(request_tools.get_request_fulfillment_status, config, auth, params)


# ── STORIES (SPM/Agile) ───────────────────────────────────────────────────────
@router.post("/stories/create")
def api_create_story(params: story_tools.CreateStoryParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(story_tools.create_story, config, auth, params)

@router.post("/stories/list")
def api_list_stories(params: story_tools.ListStoriesParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(story_tools.list_stories, config, auth, params)

@router.post("/stories/update")
def api_update_story(params: story_tools.UpdateStoryParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(story_tools.update_story, config, auth, params)

@router.post("/stories/dependencies/list")
def api_list_story_dependencies(params: story_tools.ListStoryDependenciesParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(story_tools.list_story_dependencies, config, auth, params)

@router.post("/stories/dependencies/create")
def api_create_story_dependency(params: story_tools.CreateStoryDependencyParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(story_tools.create_story_dependency, config, auth, params)

@router.post("/stories/dependencies/delete")
def api_delete_story_dependency(params: story_tools.DeleteStoryDependencyParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(story_tools.delete_story_dependency, config, auth, params)


# ── EPICS ─────────────────────────────────────────────────────────────────────
@router.post("/epics/create")
def api_create_epic(params: epic_tools.CreateEpicParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(epic_tools.create_epic, config, auth, params)

@router.post("/epics/list")
def api_list_epics(params: epic_tools.ListEpicsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(epic_tools.list_epics, config, auth, params)

@router.post("/epics/update")
def api_update_epic(params: epic_tools.UpdateEpicParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(epic_tools.update_epic, config, auth, params)


# ── SCRUM TASKS ───────────────────────────────────────────────────────────────
@router.post("/scrum_tasks/create")
def api_create_scrum_task(params: scrum_task_tools.CreateScrumTaskParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(scrum_task_tools.create_scrum_task, config, auth, params)

@router.post("/scrum_tasks/list")
def api_list_scrum_tasks(params: scrum_task_tools.ListScrumTasksParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(scrum_task_tools.list_scrum_tasks, config, auth, params)

@router.post("/scrum_tasks/update")
def api_update_scrum_task(params: scrum_task_tools.UpdateScrumTaskParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(scrum_task_tools.update_scrum_task, config, auth, params)


# ── PROJECTS ──────────────────────────────────────────────────────────────────
@router.post("/projects/create")
def api_create_project(params: project_tools.CreateProjectParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(project_tools.create_project, config, auth, params)

@router.post("/projects/list")
def api_list_projects(params: project_tools.ListProjectsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(project_tools.list_projects, config, auth, params)

@router.post("/projects/update")
def api_update_project(params: project_tools.UpdateProjectParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(project_tools.update_project, config, auth, params)


# ── RELEASES ──────────────────────────────────────────────────────────────────
@router.post("/releases/build_info")
def api_get_instance_build_info(x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(release_tools.get_instance_build_info, config, auth)

@router.post("/releases/behavior")
def api_get_release_behavior(params: release_tools.GetReleaseBehaviorParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(release_tools.get_release_behavior, config, auth, params)

@router.post("/releases/enabled_behaviors")
def api_get_enabled_module_release_behaviors(params: release_tools.GetEnabledModuleReleaseBehaviorsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(release_tools.get_enabled_module_release_behaviors, config, auth, params)


# ── SCOPED APPS ───────────────────────────────────────────────────────────────
@router.post("/scoped_apps/ensure_scope")
def api_ensure_scope(params: scoped_app_application_tools.EnsureApplicationScopeParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(scoped_app_application_tools.ensure_application_scope, config, auth, params)

@router.post("/scoped_apps/switch_scope")
def api_switch_scope(params: scoped_app_application_tools.SwitchApplicationScopeParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(scoped_app_application_tools.switch_application_scope, config, auth, params)

@router.post("/scoped_apps/set_update_set")
def api_set_update_set(params: scoped_app_application_tools.SetApplicationUpdateSetParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(scoped_app_application_tools.set_application_update_set, config, auth, params)

@router.post("/scoped_apps/artifacts/create")
def api_create_scoped_artifact(params: scoped_app_writer_tools.CreateScopedAppArtifactParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(scoped_app_writer_tools.create_scoped_app_artifact, config, auth, params)

@router.post("/scoped_apps/artifacts/list")
def api_list_scoped_artifacts(params: scoped_app_reader_tools.ListScopedAppArtifactsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(scoped_app_reader_tools.list_scoped_app_artifacts, config, auth, params)

@router.post("/scoped_apps/artifacts/get")
def api_get_scoped_artifact(params: scoped_app_reader_tools.GetScopedAppArtifactParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(scoped_app_reader_tools.get_scoped_app_artifact, config, auth, params)

@router.post("/scoped_apps/artifacts/update")
def api_update_scoped_artifact(params: scoped_app_writer_tools.UpdateScopedAppArtifactParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(scoped_app_writer_tools.update_scoped_app_artifact, config, auth, params)

@router.post("/scoped_apps/artifacts/delete")
def api_delete_scoped_artifact(params: scoped_app_writer_tools.DeleteScopedAppArtifactParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(scoped_app_writer_tools.delete_scoped_app_artifact, config, auth, params)

@router.post("/scoped_apps/lifecycle_metadata")
def api_get_scoped_app_lifecycle_metadata(params: scoped_app_metadata_tools.GetScopedAppLifecycleMetadataParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(scoped_app_metadata_tools.get_scoped_app_lifecycle_metadata, config, auth, params)


# ── SECURITY / ACL ────────────────────────────────────────────────────────────
@router.post("/security/acls/create")
def api_create_acl(params: security_admin_tools.CreateACLParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(security_admin_tools.create_acl, config, auth, params)

@router.post("/security/acls/list")
def api_list_acls(params: security_admin_tools.ListACLsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(security_admin_tools.list_acls, config, auth, params)

@router.post("/security/acls/get")
def api_get_acl(params: security_admin_tools.GetACLParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(security_admin_tools.get_acl, config, auth, params)

@router.post("/security/acls/update")
def api_update_acl(params: security_admin_tools.UpdateACLParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(security_admin_tools.update_acl, config, auth, params)

@router.post("/security/acls/validate")
def api_validate_acl(params: security_admin_tools.ValidateACLParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(security_admin_tools.validate_acl, config, auth, params)

@router.post("/security/acls/test_access")
def api_test_acl_access(params: security_admin_tools.TestACLAccessParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(security_admin_tools.test_acl_access, config, auth, params)

@router.post("/security/roles/create")
def api_create_role(params: security_admin_tools.CreateRoleParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(security_admin_tools.create_role, config, auth, params)

@router.post("/security/roles/list")
def api_list_roles(params: security_admin_tools.ListRolesParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(security_admin_tools.list_roles, config, auth, params)

@router.post("/security/roles/get")
def api_get_role(params: security_admin_tools.GetRoleParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(security_admin_tools.get_role, config, auth, params)

@router.post("/security/roles/update")
def api_update_role(params: security_admin_tools.UpdateRoleParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(security_admin_tools.update_role, config, auth, params)

@router.post("/security/roles/validate")
def api_validate_role(params: security_admin_tools.ValidateRoleParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(security_admin_tools.validate_role, config, auth, params)

@router.post("/security/role_mappings/create")
def api_create_role_mapping(params: security_admin_tools.CreateRoleMappingParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(security_admin_tools.create_role_mapping, config, auth, params)

@router.post("/security/role_mappings/list")
def api_list_role_mappings(params: security_admin_tools.ListRoleMappingsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(security_admin_tools.list_role_mappings, config, auth, params)

@router.post("/security/role_mappings/update")
def api_update_role_mapping(params: security_admin_tools.UpdateRoleMappingParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(security_admin_tools.update_role_mapping, config, auth, params)

@router.post("/security/constraints/validate")
def api_validate_security_constraints(params: security_admin_tools.ValidateSecurityConstraintsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(security_admin_tools.validate_security_constraints, config, auth, params)


# ── UI ACTIONS ────────────────────────────────────────────────────────────────
@router.post("/ui_actions/create")
def api_create_ui_action(params: ui_action_tools.CreateUIActionParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(ui_action_tools.create_ui_action, config, auth, params)

@router.post("/ui_actions/list")
def api_list_ui_actions(params: ui_action_tools.ListUIActionsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(ui_action_tools.list_ui_actions, config, auth, params)

@router.post("/ui_actions/get")
def api_get_ui_action(params: ui_action_tools.GetUIActionParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(ui_action_tools.get_ui_action, config, auth, params)

@router.post("/ui_actions/update")
def api_update_ui_action(params: ui_action_tools.UpdateUIActionParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(ui_action_tools.update_ui_action, config, auth, params)

@router.post("/ui_actions/delete")
def api_delete_ui_action(params: ui_action_tools.DeleteUIActionParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(ui_action_tools.delete_ui_action, config, auth, params)


# ── TABLE SCHEMA ──────────────────────────────────────────────────────────────
@router.post("/table_schema/tables/create")
def api_create_table(params: table_schema_tools.CreateTableDefinitionParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(table_schema_tools.create_table_definition, config, auth, params)

@router.post("/table_schema/tables/list")
def api_list_tables(params: table_schema_tools.ListTableDefinitionsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(table_schema_tools.list_table_definitions, config, auth, params)

@router.post("/table_schema/tables/get")
def api_get_table(params: table_schema_tools.GetTableDefinitionParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(table_schema_tools.get_table_definition, config, auth, params)

@router.post("/table_schema/tables/update")
def api_update_table(params: table_schema_tools.UpdateTableDefinitionParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(table_schema_tools.update_table_definition, config, auth, params)

@router.post("/table_schema/fields/create")
def api_create_field(params: table_schema_tools.CreateFieldDefinitionParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(table_schema_tools.create_field_definition, config, auth, params)

@router.post("/table_schema/fields/list")
def api_list_fields(params: table_schema_tools.ListFieldDefinitionsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(table_schema_tools.list_field_definitions, config, auth, params)

@router.post("/table_schema/fields/get")
def api_get_field(params: table_schema_tools.GetFieldDefinitionParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(table_schema_tools.get_field_definition, config, auth, params)

@router.post("/table_schema/fields/update")
def api_update_field(params: table_schema_tools.UpdateFieldDefinitionParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(table_schema_tools.update_field_definition, config, auth, params)


# ── DASHBOARDS ────────────────────────────────────────────────────────────────
@router.post("/dashboards/create")
def api_create_dashboard(params: dashboard_tools.CreateDashboardParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(dashboard_tools.create_dashboard, config, auth, params)

@router.post("/dashboards/list")
def api_list_dashboards(params: dashboard_tools.ListDashboardsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(dashboard_tools.list_dashboards, config, auth, params)

@router.post("/dashboards/get")
def api_get_dashboard(params: dashboard_tools.GetDashboardParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(dashboard_tools.get_dashboard, config, auth, params)

@router.post("/dashboards/update")
def api_update_dashboard(params: dashboard_tools.UpdateDashboardParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(dashboard_tools.update_dashboard, config, auth, params)

@router.post("/dashboards/delete")
def api_delete_dashboard(params: dashboard_tools.DeleteDashboardParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(dashboard_tools.delete_dashboard, config, auth, params)

@router.post("/dashboards/tabs/add")
def api_add_dashboard_tab(params: dashboard_tools.AddDashboardTabParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(dashboard_tools.add_dashboard_tab, config, auth, params)

@router.post("/dashboards/tabs/list")
def api_list_dashboard_tabs(params: dashboard_tools.ListDashboardTabsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(dashboard_tools.list_dashboard_tabs, config, auth, params)

@router.post("/dashboards/tabs/update")
def api_update_dashboard_tab(params: dashboard_tools.UpdateDashboardTabParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(dashboard_tools.update_dashboard_tab, config, auth, params)

@router.post("/dashboards/tabs/delete")
def api_delete_dashboard_tab(params: dashboard_tools.DeleteDashboardTabParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(dashboard_tools.delete_dashboard_tab, config, auth, params)


# ── PA INDICATORS ─────────────────────────────────────────────────────────────
@router.post("/pa/indicators/create")
def api_create_pa_indicator(params: pa_tools.CreatePAIndicatorParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(pa_tools.create_pa_indicator, config, auth, params)

@router.post("/pa/indicators/list")
def api_list_pa_indicators(params: pa_tools.ListPAIndicatorsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(pa_tools.list_pa_indicators, config, auth, params)

@router.post("/pa/indicators/get")
def api_get_pa_indicator(params: pa_tools.GetPAIndicatorParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(pa_tools.get_pa_indicator, config, auth, params)

@router.post("/pa/indicators/update")
def api_update_pa_indicator(params: pa_tools.UpdatePAIndicatorParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(pa_tools.update_pa_indicator, config, auth, params)

@router.post("/pa/indicators/delete")
def api_delete_pa_indicator(params: pa_tools.DeletePAIndicatorParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(pa_tools.delete_pa_indicator, config, auth, params)

@router.post("/pa/widgets/list")
def api_list_pa_widget_instances(params: pa_tools.ListPAWidgetInstancesParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(pa_tools.list_pa_widget_instances, config, auth, params)


# ── REPORTS ───────────────────────────────────────────────────────────────────
@router.post("/reports/create")
def api_create_report(params: report_tools.CreateReportParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(report_tools.create_report, config, auth, params)

@router.post("/reports/list")
def api_list_reports(params: report_tools.ListReportsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(report_tools.list_reports, config, auth, params)

@router.post("/reports/get")
def api_get_report(params: report_tools.GetReportParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(report_tools.get_report, config, auth, params)

@router.post("/reports/update")
def api_update_report(params: report_tools.UpdateReportParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(report_tools.update_report, config, auth, params)

@router.post("/reports/delete")
def api_delete_report(params: report_tools.DeleteReportParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(report_tools.delete_report, config, auth, params)

@router.post("/reports/run")
def api_run_report(params: report_tools.RunReportParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(report_tools.run_report, config, auth, params)


# ── EMPLOYEE SERVICE CENTER ───────────────────────────────────────────────────
@router.post("/esc/announcements/create")
def api_create_esc_announcement(params: esc_announcement_tools.CreateESCAnnouncementParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(esc_announcement_tools.create_esc_announcement, config, auth, params)

@router.post("/esc/announcements/list")
def api_list_esc_announcements(params: esc_announcement_tools.ListESCAnnouncementsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(esc_announcement_tools.list_esc_announcements, config, auth, params)

@router.post("/esc/announcements/get")
def api_get_esc_announcement(params: esc_announcement_tools.GetESCAnnouncementParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(esc_announcement_tools.get_esc_announcement, config, auth, params)

@router.post("/esc/announcements/update")
def api_update_esc_announcement(params: esc_announcement_tools.UpdateESCAnnouncementParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(esc_announcement_tools.update_esc_announcement, config, auth, params)

@router.post("/esc/announcements/delete")
def api_delete_esc_announcement(params: esc_announcement_tools.DeleteESCAnnouncementParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(esc_announcement_tools.delete_esc_announcement, config, auth, params)

@router.post("/esc/pages/create")
def api_create_esc_page(params: esc_page_tools.CreateESCPageParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(esc_page_tools.create_esc_page, config, auth, params)

@router.post("/esc/pages/list")
def api_list_esc_pages(params: esc_page_tools.ListESCPagesParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(esc_page_tools.list_esc_pages, config, auth, params)

@router.post("/esc/pages/get")
def api_get_esc_page(params: esc_page_tools.GetESCPageParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(esc_page_tools.get_esc_page, config, auth, params)

@router.post("/esc/pages/update")
def api_update_esc_page(params: esc_page_tools.UpdateESCPageParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(esc_page_tools.update_esc_page, config, auth, params)

@router.post("/esc/pages/delete")
def api_delete_esc_page(params: esc_page_tools.DeleteESCPageParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(esc_page_tools.delete_esc_page, config, auth, params)

@router.post("/esc/topics/create")
def api_create_esc_topic(params: esc_topic_tools.CreateESCTopicParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(esc_topic_tools.create_esc_topic, config, auth, params)

@router.post("/esc/topics/list")
def api_list_esc_topics(params: esc_topic_tools.ListESCTopicsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(esc_topic_tools.list_esc_topics, config, auth, params)

@router.post("/esc/topics/get")
def api_get_esc_topic(params: esc_topic_tools.GetESCTopicParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(esc_topic_tools.get_esc_topic, config, auth, params)

@router.post("/esc/topics/update")
def api_update_esc_topic(params: esc_topic_tools.UpdateESCTopicParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(esc_topic_tools.update_esc_topic, config, auth, params)

@router.post("/esc/topics/delete")
def api_delete_esc_topic(params: esc_topic_tools.DeleteESCTopicParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(esc_topic_tools.delete_esc_topic, config, auth, params)


# ── ATF EDIT TOOLS ────────────────────────────────────────────────────────────
@router.post("/atf/find_test")
def api_atf_find_test(params: atf_edit_tools.ATFFindTestParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(atf_edit_tools.servicenow_atf_find_test, config, auth, params)

@router.post("/atf/find_step")
def api_atf_find_step(params: atf_edit_tools.ATFFindStepParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(atf_edit_tools.servicenow_atf_find_step, config, auth, params)

@router.post("/atf/get_step_inputs")
def api_atf_get_step_inputs(params: atf_edit_tools.ATFGetStepInputsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(atf_edit_tools.servicenow_atf_get_step_inputs, config, auth, params)

@router.post("/atf/update_step_input")
def api_atf_update_step_input(params: atf_edit_tools.ATFUpdateStepInputParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(atf_edit_tools.servicenow_atf_update_step_input, config, auth, params)

@router.post("/atf/update_test_metadata")
def api_atf_update_test_metadata(params: atf_edit_tools.ATFUpdateTestMetadataParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(atf_edit_tools.servicenow_atf_update_test_metadata, config, auth, params)

@router.post("/atf/update_step_metadata")
def api_atf_update_step_metadata(params: atf_edit_tools.ATFUpdateStepMetadataParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(atf_edit_tools.servicenow_atf_update_step_metadata, config, auth, params)

@router.post("/atf/reorder_steps")
def api_atf_reorder_steps(params: atf_edit_tools.ATFReorderStepsParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(atf_edit_tools.servicenow_atf_reorder_steps, config, auth, params)

@router.post("/atf/validate_step_input")
def api_atf_validate_step_input(params: atf_edit_tools.ATFValidateStepInputParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(atf_edit_tools.servicenow_atf_validate_step_input, config, auth, params)

@router.post("/atf/rollback_step_input")
def api_atf_rollback_step_input(params: atf_edit_tools.ATFRollbackStepInputUpdateParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(atf_edit_tools.servicenow_atf_rollback_step_input_update, config, auth, params)

@router.post("/atf/resolve_reference")
def api_atf_resolve_reference(params: atf_edit_tools.ServiceNowResolveReferenceParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(atf_edit_tools.servicenow_resolve_reference, config, auth, params)


# ── CMDB / IRE ────────────────────────────────────────────────────────────────
@router.post("/cmdb/validate_payload")
def api_cmdb_validate_payload(params: cmdb_ire_tools.ValidateCiPayloadParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(cmdb_ire_tools.validate_ci_payload, config, auth, params)

@router.post("/cmdb/upsert_ci")
def api_cmdb_upsert_ci(params: cmdb_ire_tools.UpsertCiParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(cmdb_ire_tools.upsert_ci, config, auth, params)

@router.post("/cmdb/create_relationship")
def api_cmdb_create_relationship(params: cmdb_ire_tools.CreateCsdmRelationshipParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(cmdb_ire_tools.create_csdm_relationship, config, auth, params)

@router.post("/cmdb/managed_ingestion")
def api_cmdb_managed_ingestion(params: cmdb_ire_tools.ManagedIngestionParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(cmdb_ire_tools.managed_ingestion, config, auth, params)

@router.post("/cmdb/instance_preflight")
def api_cmdb_instance_preflight(params: cmdb_ire_tools.InstancePreflightParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(cmdb_ire_tools.instance_preflight, config, auth, params)

@router.post("/cmdb/governance_insight")
def api_cmdb_governance_insight(params: cmdb_ire_tools.GovernanceInsightParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(cmdb_ire_tools.governance_insight, config, auth, params)

@router.post("/cmdb/health_dashboard")
def api_cmdb_health_dashboard(params: cmdb_ire_tools.HealthDashboardParams, x_session_id: Optional[str] = Header(None)):
    config, auth = _ctx(x_session_id)
    return _run(cmdb_ire_tools.health_dashboard, config, auth, params)
