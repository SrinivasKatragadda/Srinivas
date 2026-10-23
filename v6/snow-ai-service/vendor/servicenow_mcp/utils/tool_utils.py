"""Tool utility helpers for ServiceNow MCP.

This module centralizes tool registration lookups and dispatch helpers used by
the MCP server. It collects the tool modules in one place so higher-level code
can translate a tool name into the correct handler without duplicating import
or routing logic.
"""

from typing import Any, Callable, Dict, Tuple, Type

import servicenow_mcp.atf_framework.tools as atf_tools
import servicenow_mcp.internal_tools.cmdb_ire_tools as cmdb_ire_tools
import servicenow_mcp.tools.approval_tools as approval_tools
import servicenow_mcp.tools.asset_management_tools as asset_management_tools
import servicenow_mcp.tools.atf_edit_tools as atf_edit_tools
import servicenow_mcp.tools.dashboards_reporting.dashboard_tools as dashboard_tools
import servicenow_mcp.tools.dashboards_reporting.pa_tools as pa_tools
import servicenow_mcp.tools.dashboards_reporting.report_tools as report_tools
import servicenow_mcp.tools.employee_center.esc_announcement_tools as esc_announcement_tools
import servicenow_mcp.tools.employee_center.esc_page_tools as esc_page_tools
import servicenow_mcp.tools.employee_center.esc_topic_tools as esc_topic_tools
import servicenow_mcp.tools.scoped_app_application_tools as scoped_app_application_tools
import servicenow_mcp.tools.scoped_app_metadata_tools as scoped_app_metadata_tools
import servicenow_mcp.tools.scoped_app_reader_tools as scoped_app_reader_tools
import servicenow_mcp.tools.scoped_app_writer_tools as scoped_app_writer_tools
import servicenow_mcp.tools.table_schema_tools as table_schema_tools
from servicenow_mcp.tools.background_job_tools import (
    CreateBackgroundJobParams,
    DeleteBackgroundJobParams,
    GetBackgroundJobParams,
    ListBackgroundJobsParams,
    UpdateBackgroundJobParams,
)
from servicenow_mcp.tools.background_job_tools import (
    create_background_job as create_background_job_tool,
)
from servicenow_mcp.tools.background_job_tools import (
    delete_background_job as delete_background_job_tool,
)
from servicenow_mcp.tools.background_job_tools import (
    get_background_job as get_background_job_tool,
)
from servicenow_mcp.tools.background_job_tools import (
    list_background_jobs as list_background_jobs_tool,
)
from servicenow_mcp.tools.background_job_tools import (
    update_background_job as update_background_job_tool,
)
from servicenow_mcp.tools.background_script_tools import ExecuteBackgroundScriptParams
from servicenow_mcp.tools.background_script_tools import (
    execute_background_script as execute_background_script_tool,
)
from servicenow_mcp.tools.business_rule_tools import (
    BusinessRuleResponse,
    CreateBusinessRuleParams,
    GetBusinessRuleParams,
    ListBusinessRulesParams,
    UpdateBusinessRuleParams,
)
from servicenow_mcp.tools.business_rule_tools import (
    DeleteBusinessRuleParams as DeleteBusinessRuleParamsBusinessRule,
)
from servicenow_mcp.tools.business_rule_tools import (
    create_business_rule as create_business_rule_tool,
)
from servicenow_mcp.tools.business_rule_tools import (
    delete_business_rule as delete_business_rule_tool,
)
from servicenow_mcp.tools.business_rule_tools import (
    get_business_rule as get_business_rule_tool,
)
from servicenow_mcp.tools.business_rule_tools import (
    list_business_rules as list_business_rules_tool,
)
from servicenow_mcp.tools.business_rule_tools import (
    update_business_rule as update_business_rule_tool,
)
from servicenow_mcp.tools.catalog_admin_tools import (
    CreateCatalogClientScriptParams,
    CreateCatalogItemParams,
    CreateCatalogParams,
    GetCatalogClientScriptParams,
    GetCatalogParams,
    ListCatalogClientScriptsParams,
    ListCatalogsParams,
    PublishCatalogItemParams,
    UpdateCatalogClientScriptParams,
    UpdateCatalogParams,
)
from servicenow_mcp.tools.catalog_admin_tools import (
    create_catalog as create_catalog_tool,
)
from servicenow_mcp.tools.catalog_admin_tools import (
    create_catalog_client_script as create_catalog_client_script_tool,
)
from servicenow_mcp.tools.catalog_admin_tools import (
    create_catalog_item as create_catalog_item_tool,
)
from servicenow_mcp.tools.catalog_admin_tools import (
    get_catalog as get_catalog_tool,
)
from servicenow_mcp.tools.catalog_admin_tools import (
    get_catalog_client_script as get_catalog_client_script_tool,
)
from servicenow_mcp.tools.catalog_admin_tools import (
    list_catalog_client_scripts as list_catalog_client_scripts_tool,
)
from servicenow_mcp.tools.catalog_admin_tools import (
    list_catalogs as list_catalogs_tool,
)
from servicenow_mcp.tools.catalog_admin_tools import (
    publish_catalog_item as publish_catalog_item_tool,
)
from servicenow_mcp.tools.catalog_admin_tools import (
    update_catalog as update_catalog_tool,
)
from servicenow_mcp.tools.catalog_admin_tools import (
    update_catalog_client_script as update_catalog_client_script_tool,
)

# Import all necessary tool implementation functions and params models
# (This list needs to be kept complete and up-to-date)
from servicenow_mcp.tools.catalog_optimization import (
    OptimizationRecommendationsParams,
    UpdateCatalogItemParams,
)
from servicenow_mcp.tools.catalog_optimization import (
    get_optimization_recommendations as get_optimization_recommendations_tool,
)
from servicenow_mcp.tools.catalog_optimization import (
    update_catalog_item as update_catalog_item_tool,
)
from servicenow_mcp.tools.catalog_tools import (
    CreateCatalogCategoryParams,
    GetCatalogItemParams,
    ListCatalogCategoriesParams,
    ListCatalogItemsParams,
    MoveCatalogItemsParams,
    UpdateCatalogCategoryParams,
)
from servicenow_mcp.tools.catalog_tools import (
    create_catalog_category as create_catalog_category_tool,
)
from servicenow_mcp.tools.catalog_tools import (
    get_catalog_item as get_catalog_item_tool,
)
from servicenow_mcp.tools.catalog_tools import (
    list_catalog_categories as list_catalog_categories_tool,
)
from servicenow_mcp.tools.catalog_tools import (
    list_catalog_items as list_catalog_items_tool,
)
from servicenow_mcp.tools.catalog_tools import (
    move_catalog_items as move_catalog_items_tool,
)
from servicenow_mcp.tools.catalog_tools import (
    update_catalog_category as update_catalog_category_tool,
)
from servicenow_mcp.tools.catalog_variables import (
    AddStandardOrderVariablesParams,
    CreateCatalogItemVariableParams,
    CreateCatalogItemVariablesParams,
    CreateCatalogVariableBehaviorPolicyParams,
    CreateCatalogVariableChoiceParams,
    DeleteCatalogItemVariableParams,
    GetCatalogVariableMetadataParams,
    ListCatalogItemVariablesParams,
    SetCatalogVariableBehaviorParams,
    UpdateCatalogItemVariableParams,
)
from servicenow_mcp.tools.catalog_variables import (
    add_standard_order_variables as add_standard_order_variables_tool,
)
from servicenow_mcp.tools.catalog_variables import (
    create_catalog_item_variable as create_catalog_item_variable_tool,
)
from servicenow_mcp.tools.catalog_variables import (
    create_catalog_item_variables as create_catalog_item_variables_tool,
)
from servicenow_mcp.tools.catalog_variables import (
    create_catalog_variable_behavior_policy as create_catalog_variable_behavior_policy_tool,
)
from servicenow_mcp.tools.catalog_variables import (
    create_catalog_variable_choice as create_catalog_variable_choice_tool,
)
from servicenow_mcp.tools.catalog_variables import (
    delete_catalog_item_variable as delete_catalog_item_variable_tool,
)
from servicenow_mcp.tools.catalog_variables import (
    get_catalog_variable_metadata as get_catalog_variable_metadata_tool,
)
from servicenow_mcp.tools.catalog_variables import (
    list_catalog_item_variables as list_catalog_item_variables_tool,
)
from servicenow_mcp.tools.catalog_variables import (
    set_catalog_variable_behavior as set_catalog_variable_behavior_tool,
)
from servicenow_mcp.tools.catalog_variables import (
    update_catalog_item_variable as update_catalog_item_variable_tool,
)
from servicenow_mcp.tools.change_tools import (
    AddChangeTaskParams,
    ApproveChangeParams,
    CreateChangeRequestParams,
    GetChangeRequestDetailsParams,
    ListChangeRequestsParams,
    RejectChangeParams,
    SubmitChangeForApprovalParams,
    UpdateChangeRequestParams,
)
from servicenow_mcp.tools.change_tools import (
    add_change_task as add_change_task_tool,
)
from servicenow_mcp.tools.change_tools import (
    approve_change as approve_change_tool,
)
from servicenow_mcp.tools.change_tools import (
    create_change_request as create_change_request_tool,
)
from servicenow_mcp.tools.change_tools import (
    get_change_request_details as get_change_request_details_tool,
)
from servicenow_mcp.tools.change_tools import (
    list_change_requests as list_change_requests_tool,
)
from servicenow_mcp.tools.change_tools import (
    reject_change as reject_change_tool,
)
from servicenow_mcp.tools.change_tools import (
    submit_change_for_approval as submit_change_for_approval_tool,
)
from servicenow_mcp.tools.change_tools import (
    update_change_request as update_change_request_tool,
)
from servicenow_mcp.tools.changeset_tools import (
    AddFileToChangesetParams,
    CommitChangesetParams,
    CommitUpdateSetParams,
    CreateChangesetParams,
    CreateUpdateSetParams,
    GetChangesetDetailsParams,
    GetUpdateSetDetailsParams,
    ListChangesetsParams,
    ListUpdateSetsParams,
    PreviewUpdateSetParams,
    PromoteUpdateSetParams,
    PublishChangesetParams,
    SwitchCurrentUpdateSetParams,
    UpdateChangesetParams,
    ValidateUpdateSetParams,
)
from servicenow_mcp.tools.changeset_tools import (
    add_file_to_changeset as add_file_to_changeset_tool,
)
from servicenow_mcp.tools.changeset_tools import (
    commit_changeset as commit_changeset_tool,
)
from servicenow_mcp.tools.changeset_tools import (
    commit_update_set as commit_update_set_tool,
)
from servicenow_mcp.tools.changeset_tools import (
    create_changeset as create_changeset_tool,
)
from servicenow_mcp.tools.changeset_tools import (
    create_update_set as create_update_set_tool,
)
from servicenow_mcp.tools.changeset_tools import (
    get_changeset_details as get_changeset_details_tool,
)
from servicenow_mcp.tools.changeset_tools import (
    get_update_set_details as get_update_set_details_tool,
)
from servicenow_mcp.tools.changeset_tools import (
    list_changesets as list_changesets_tool,
)
from servicenow_mcp.tools.changeset_tools import (
    list_update_sets as list_update_sets_tool,
)
from servicenow_mcp.tools.changeset_tools import (
    preview_update_set as preview_update_set_tool,
)
from servicenow_mcp.tools.changeset_tools import (
    promote_update_set as promote_update_set_tool,
)
from servicenow_mcp.tools.changeset_tools import (
    publish_changeset as publish_changeset_tool,
)
from servicenow_mcp.tools.changeset_tools import (
    switch_current_update_set as switch_current_update_set_tool,
)
from servicenow_mcp.tools.changeset_tools import (
    update_changeset as update_changeset_tool,
)
from servicenow_mcp.tools.changeset_tools import (
    validate_update_set as validate_update_set_tool,
)
from servicenow_mcp.tools.company_tools import (
    CreateCompanyParams,
    DeleteCompanyParams,
    GetCompanyParams,
    ListCompaniesParams,
    UpdateCompanyParams,
)
from servicenow_mcp.tools.company_tools import (
    create_company as create_company_tool,
)
from servicenow_mcp.tools.company_tools import (
    delete_company as delete_company_tool,
)
from servicenow_mcp.tools.company_tools import (
    get_company as get_company_tool,
)
from servicenow_mcp.tools.company_tools import (
    list_companies as list_companies_tool,
)
from servicenow_mcp.tools.company_tools import (
    update_company as update_company_tool,
)
from servicenow_mcp.tools.cost_center_tools import (
    CreateCostCenterParams,
    DeleteCostCenterParams,
    GetCostCenterParams,
    ListCostCentersParams,
    UpdateCostCenterParams,
)
from servicenow_mcp.tools.cost_center_tools import (
    create_cost_center as create_cost_center_tool,
)
from servicenow_mcp.tools.cost_center_tools import (
    delete_cost_center as delete_cost_center_tool,
)
from servicenow_mcp.tools.cost_center_tools import (
    get_cost_center as get_cost_center_tool,
)
from servicenow_mcp.tools.cost_center_tools import (
    list_cost_centers as list_cost_centers_tool,
)
from servicenow_mcp.tools.cost_center_tools import (
    update_cost_center as update_cost_center_tool,
)
from servicenow_mcp.tools.data_policy_tools import (
    ConvertUIPolicyToDataPolicyParams,
    ConvertUIPolicyToDataPolicyResponse,
    CreateDataPolicyParams,
    CreateDataPolicyRuleParams,
    DataPolicyResponse,
    DataPolicyRuleResponse,
    DeleteDataPolicyParams,
    DeleteDataPolicyRuleParams,
    GetDataPolicyParams,
    GetDataPolicyRuleParams,
    ListDataPoliciesParams,
    ListDataPolicyRulesParams,
    SetDataPolicyClientEnforcementParams,
    UpdateDataPolicyParams,
    UpdateDataPolicyRuleParams,
)
from servicenow_mcp.tools.data_policy_tools import (
    convert_ui_policy_to_data_policy as convert_ui_policy_to_data_policy_tool,
)
from servicenow_mcp.tools.data_policy_tools import (
    create_data_policy as create_data_policy_tool,
)
from servicenow_mcp.tools.data_policy_tools import (
    create_data_policy_rule as create_data_policy_rule_tool,
)
from servicenow_mcp.tools.data_policy_tools import (
    delete_data_policy as delete_data_policy_tool,
)
from servicenow_mcp.tools.data_policy_tools import (
    delete_data_policy_rule as delete_data_policy_rule_tool,
)
from servicenow_mcp.tools.data_policy_tools import (
    get_data_policy as get_data_policy_tool,
)
from servicenow_mcp.tools.data_policy_tools import (
    get_data_policy_rule as get_data_policy_rule_tool,
)
from servicenow_mcp.tools.data_policy_tools import (
    list_data_policies as list_data_policies_tool,
)
from servicenow_mcp.tools.data_policy_tools import (
    list_data_policy_rules as list_data_policy_rules_tool,
)
from servicenow_mcp.tools.data_policy_tools import (
    set_data_policy_client_enforcement as set_data_policy_client_enforcement_tool,
)
from servicenow_mcp.tools.data_policy_tools import (
    update_data_policy as update_data_policy_tool,
)
from servicenow_mcp.tools.data_policy_tools import (
    update_data_policy_rule as update_data_policy_rule_tool,
)
from servicenow_mcp.tools.department_tools import (
    CreateDepartmentParams,
    DeleteDepartmentParams,
    GetDepartmentParams,
    ListDepartmentsParams,
    UpdateDepartmentParams,
)
from servicenow_mcp.tools.department_tools import (
    create_department as create_department_tool,
)
from servicenow_mcp.tools.department_tools import (
    delete_department as delete_department_tool,
)
from servicenow_mcp.tools.department_tools import (
    get_department as get_department_tool,
)
from servicenow_mcp.tools.department_tools import (
    list_departments as list_departments_tool,
)
from servicenow_mcp.tools.department_tools import (
    update_department as update_department_tool,
)
from servicenow_mcp.tools.epic_tools import (
    CreateEpicParams,
    ListEpicsParams,
    UpdateEpicParams,
)
from servicenow_mcp.tools.epic_tools import (
    create_epic as create_epic_tool,
)
from servicenow_mcp.tools.epic_tools import (
    list_epics as list_epics_tool,
)
from servicenow_mcp.tools.epic_tools import (
    update_epic as update_epic_tool,
)
from servicenow_mcp.tools.fix_script_tools import (
    CreateFixScriptParams,
    DeleteFixScriptParams,
    GetFixScriptParams,
    ListFixScriptsParams,
    UpdateFixScriptParams,
)
from servicenow_mcp.tools.fix_script_tools import (
    create_fix_script as create_fix_script_tool,
)
from servicenow_mcp.tools.fix_script_tools import (
    delete_fix_script as delete_fix_script_tool,
)
from servicenow_mcp.tools.fix_script_tools import (
    get_fix_script as get_fix_script_tool,
)
from servicenow_mcp.tools.fix_script_tools import (
    list_fix_scripts as list_fix_scripts_tool,
)
from servicenow_mcp.tools.fix_script_tools import (
    update_fix_script as update_fix_script_tool,
)
from servicenow_mcp.tools.flow_designer.tools import (
    CreateFlowParams,
    CreateIncidentAdminNotificationFlowParams,
    DeploySubflowParams,
    EditFlowParams,
    GenerateSubflowSourceParams,
    HintFlowJsonParams,
    HintSubflowJsonParams,
    ListFlowActionsParams,
    ValidateFlowJsonParams,
    ValidateSubflowJsonParams,
    VerifySubflowParams,
)
from servicenow_mcp.tools.flow_designer.tools import create_flow as create_flow_tool
from servicenow_mcp.tools.flow_designer.tools import (
    create_incident_admin_notification_flow as create_incident_admin_notification_flow_tool,
)
from servicenow_mcp.tools.flow_designer.tools import deploy_subflow as deploy_subflow_tool
from servicenow_mcp.tools.flow_designer.tools import edit_flow as edit_flow_tool
from servicenow_mcp.tools.flow_designer.tools import (
    generate_subflow_source as generate_subflow_source_tool,
)
from servicenow_mcp.tools.flow_designer.tools import hint_flow_json as hint_flow_json_tool
from servicenow_mcp.tools.flow_designer.tools import hint_subflow_json as hint_subflow_json_tool
from servicenow_mcp.tools.flow_designer.tools import list_flow_actions as list_flow_actions_tool
from servicenow_mcp.tools.flow_designer.tools import validate_flow_json as validate_flow_json_tool
from servicenow_mcp.tools.flow_designer.tools import (
    validate_subflow_json as validate_subflow_json_tool,
)
from servicenow_mcp.tools.flow_designer.tools import verify_subflow as verify_subflow_tool
from servicenow_mcp.tools.incident_tools import (
    AddCommentParams,
    CreateIncidentParams,
    GetIncidentByNumberParams,
    ListIncidentsParams,
    ResolveIncidentParams,
    UpdateIncidentParams,
)
from servicenow_mcp.tools.incident_tools import (
    add_comment as add_comment_tool,
)
from servicenow_mcp.tools.incident_tools import (
    create_incident as create_incident_tool,
)
from servicenow_mcp.tools.incident_tools import (
    get_incident_by_number as get_incident_by_number_tool,
)
from servicenow_mcp.tools.incident_tools import (
    list_incidents as list_incidents_tool,
)
from servicenow_mcp.tools.incident_tools import (
    resolve_incident as resolve_incident_tool,
)
from servicenow_mcp.tools.incident_tools import (
    update_incident as update_incident_tool,
)
from servicenow_mcp.tools.knowledge_base import (
    CreateArticleParams,
    CreateKnowledgeBaseParams,
    GetArticleParams,
    ListArticlesParams,
    ListKnowledgeBasesParams,
    PublishArticleParams,
    UpdateArticleParams,
)
from servicenow_mcp.tools.knowledge_base import (
    CreateCategoryParams as CreateKBCategoryParams,  # Aliased
)
from servicenow_mcp.tools.knowledge_base import (
    ListCategoriesParams as ListKBCategoriesParams,  # Aliased
)
from servicenow_mcp.tools.knowledge_base import (
    create_article as create_article_tool,
)
from servicenow_mcp.tools.knowledge_base import (
    # create_category aliased in function call
    create_knowledge_base as create_knowledge_base_tool,
)
from servicenow_mcp.tools.knowledge_base import (
    get_article as get_article_tool,
)
from servicenow_mcp.tools.knowledge_base import (
    list_articles as list_articles_tool,
)
from servicenow_mcp.tools.knowledge_base import (
    # list_categories aliased in function call
    list_knowledge_bases as list_knowledge_bases_tool,
)
from servicenow_mcp.tools.knowledge_base import (
    publish_article as publish_article_tool,
)
from servicenow_mcp.tools.knowledge_base import (
    update_article as update_article_tool,
)
from servicenow_mcp.tools.location_tools import (
    CreateLocationParams,
    DeleteLocationParams,
    GetLocationParams,
    ListLocationsParams,
    UpdateLocationParams,
)
from servicenow_mcp.tools.location_tools import (
    create_location as create_location_tool,
)
from servicenow_mcp.tools.location_tools import (
    delete_location as delete_location_tool,
)
from servicenow_mcp.tools.location_tools import (
    get_location as get_location_tool,
)
from servicenow_mcp.tools.location_tools import (
    list_locations as list_locations_tool,
)
from servicenow_mcp.tools.location_tools import (
    update_location as update_location_tool,
)
from servicenow_mcp.tools.notification_tools import (
    CreateEmailTemplateParams,
    CreateEventDefinitionParams,
    CreateNotificationParams,
    CreateScriptActionParams,
    DeleteScriptActionParams,
    GetEmailTemplateParams,
    GetEventDefinitionParams,
    GetNotificationParams,
    GetScriptActionParams,
    ListEmailTemplatesParams,
    ListEventDefinitionsParams,
    ListNotificationsParams,
    ListScriptActionsParams,
    QueueEventParams,
    UpdateEmailTemplateParams,
    UpdateEventDefinitionParams,
    UpdateNotificationParams,
    UpdateScriptActionParams,
)
from servicenow_mcp.tools.notification_tools import (
    create_email_template as create_email_template_tool,
)
from servicenow_mcp.tools.notification_tools import (
    create_event_definition as create_event_definition_tool,
)
from servicenow_mcp.tools.notification_tools import (
    create_notification as create_notification_tool,
)
from servicenow_mcp.tools.notification_tools import (
    create_script_action as create_script_action_tool,
)
from servicenow_mcp.tools.notification_tools import (
    delete_script_action as delete_script_action_tool,
)
from servicenow_mcp.tools.notification_tools import (
    get_email_template as get_email_template_tool,
)
from servicenow_mcp.tools.notification_tools import (
    get_event_definition as get_event_definition_tool,
)
from servicenow_mcp.tools.notification_tools import (
    get_notification as get_notification_tool,
)
from servicenow_mcp.tools.notification_tools import (
    get_script_action as get_script_action_tool,
)
from servicenow_mcp.tools.notification_tools import (
    list_email_templates as list_email_templates_tool,
)
from servicenow_mcp.tools.notification_tools import (
    list_event_definitions as list_event_definitions_tool,
)
from servicenow_mcp.tools.notification_tools import (
    list_notifications as list_notifications_tool,
)
from servicenow_mcp.tools.notification_tools import (
    list_script_actions as list_script_actions_tool,
)
from servicenow_mcp.tools.notification_tools import (
    queue_event as queue_event_tool,
)
from servicenow_mcp.tools.notification_tools import (
    update_email_template as update_email_template_tool,
)
from servicenow_mcp.tools.notification_tools import (
    update_event_definition as update_event_definition_tool,
)
from servicenow_mcp.tools.notification_tools import (
    update_notification as update_notification_tool,
)
from servicenow_mcp.tools.notification_tools import (
    update_script_action as update_script_action_tool,
)
from servicenow_mcp.tools.problem_tools import (
    CreateProblemParams,
    CreateProblemTaskParams,
    GetProblemParams,
    GetProblemTaskParams,
    LinkProblemToIncidentParams,
    LinkProblemToKnownErrorParams,
    ListProblemsParams,
    ListProblemTasksParams,
    ResolveProblemParams,
    ResolveProblemTaskParams,
    UpdateProblemParams,
    UpdateProblemTaskParams,
)
from servicenow_mcp.tools.problem_tools import (
    create_problem as create_problem_tool,
)
from servicenow_mcp.tools.problem_tools import (
    create_problem_task as create_problem_task_tool,
)
from servicenow_mcp.tools.problem_tools import (
    get_problem as get_problem_tool,
)
from servicenow_mcp.tools.problem_tools import (
    get_problem_task as get_problem_task_tool,
)
from servicenow_mcp.tools.problem_tools import (
    link_problem_to_incident as link_problem_to_incident_tool,
)
from servicenow_mcp.tools.problem_tools import (
    link_problem_to_known_error as link_problem_to_known_error_tool,
)
from servicenow_mcp.tools.problem_tools import (
    list_problem_tasks as list_problem_tasks_tool,
)
from servicenow_mcp.tools.problem_tools import (
    list_problems as list_problems_tool,
)
from servicenow_mcp.tools.problem_tools import (
    resolve_problem as resolve_problem_tool,
)
from servicenow_mcp.tools.problem_tools import (
    resolve_problem_task as resolve_problem_task_tool,
)
from servicenow_mcp.tools.problem_tools import (
    update_problem as update_problem_tool,
)
from servicenow_mcp.tools.problem_tools import (
    update_problem_task as update_problem_task_tool,
)
from servicenow_mcp.tools.project_tools import (
    CreateProjectParams,
    ListProjectsParams,
    UpdateProjectParams,
)
from servicenow_mcp.tools.project_tools import (
    create_project as create_project_tool,
)
from servicenow_mcp.tools.project_tools import (
    list_projects as list_projects_tool,
)
from servicenow_mcp.tools.project_tools import (
    update_project as update_project_tool,
)
from servicenow_mcp.tools.release_tools import (
    GetEnabledModuleReleaseBehaviorsParams,
    GetInstanceBuildInfoParams,
    GetReleaseBehaviorParams,
)
from servicenow_mcp.tools.release_tools import (
    get_enabled_module_release_behaviors as get_enabled_module_release_behaviors_tool,
)
from servicenow_mcp.tools.release_tools import (
    get_instance_build_info as get_instance_build_info_tool,
)
from servicenow_mcp.tools.release_tools import (
    get_release_behavior as get_release_behavior_tool,
)
from servicenow_mcp.tools.request_tools import (
    CreateCatalogTaskParams,
    CreateRequestedItemParams,
    CreateRequestParams,
    GetCatalogItemRequestOptionsParams,
    GetRequestedItemParams,
    GetRequestFulfillmentStatusParams,
    GetRequestParams,
    ListCatalogTasksParams,
    ListRequestApprovalsParams,
    ListRequestedItemsParams,
    ListRequestsParams,
    UpdateCatalogTaskParams,
    UpdateRequestApprovalParams,
    UpdateRequestedItemParams,
    UpdateRequestParams,
)
from servicenow_mcp.tools.request_tools import (
    create_catalog_task as create_catalog_task_tool,
)
from servicenow_mcp.tools.request_tools import (
    create_request as create_request_tool,
)
from servicenow_mcp.tools.request_tools import (
    create_requested_item as create_requested_item_tool,
)
from servicenow_mcp.tools.request_tools import (
    get_catalog_item_request_options as get_catalog_item_request_options_tool,
)
from servicenow_mcp.tools.request_tools import (
    get_request as get_request_tool,
)
from servicenow_mcp.tools.request_tools import (
    get_request_fulfillment_status as get_request_fulfillment_status_tool,
)
from servicenow_mcp.tools.request_tools import (
    get_requested_item as get_requested_item_tool,
)
from servicenow_mcp.tools.request_tools import (
    list_catalog_tasks as list_catalog_tasks_tool,
)
from servicenow_mcp.tools.request_tools import (
    list_request_approvals as list_request_approvals_tool,
)
from servicenow_mcp.tools.request_tools import (
    list_requested_items as list_requested_items_tool,
)
from servicenow_mcp.tools.request_tools import (
    list_requests as list_requests_tool,
)
from servicenow_mcp.tools.request_tools import (
    update_catalog_task as update_catalog_task_tool,
)
from servicenow_mcp.tools.request_tools import (
    update_request as update_request_tool,
)
from servicenow_mcp.tools.request_tools import (
    update_request_approval as update_request_approval_tool,
)
from servicenow_mcp.tools.request_tools import (
    update_requested_item as update_requested_item_tool,
)
from servicenow_mcp.tools.scheduled_job_tools import (
    CreateScheduledJobParams,
    DeleteScheduledJobParams,
    GetScheduledJobParams,
    ListScheduledJobsParams,
    UpdateScheduledJobParams,
)
from servicenow_mcp.tools.scheduled_job_tools import (
    create_scheduled_job as create_scheduled_job_tool,
)
from servicenow_mcp.tools.scheduled_job_tools import (
    delete_scheduled_job as delete_scheduled_job_tool,
)
from servicenow_mcp.tools.scheduled_job_tools import (
    get_scheduled_job as get_scheduled_job_tool,
)
from servicenow_mcp.tools.scheduled_job_tools import (
    list_scheduled_jobs as list_scheduled_jobs_tool,
)
from servicenow_mcp.tools.scheduled_job_tools import (
    update_scheduled_job as update_scheduled_job_tool,
)
from servicenow_mcp.tools.scheduled_script_execution_tools import (
    CreateScheduledScriptExecutionParams,
    DeleteScheduledScriptExecutionParams,
    GetScheduledScriptExecutionParams,
    ListScheduledScriptExecutionsParams,
    UpdateScheduledScriptExecutionParams,
)
from servicenow_mcp.tools.scheduled_script_execution_tools import (
    create_scheduled_script_execution as create_scheduled_script_execution_tool,
)
from servicenow_mcp.tools.scheduled_script_execution_tools import (
    delete_scheduled_script_execution as delete_scheduled_script_execution_tool,
)
from servicenow_mcp.tools.scheduled_script_execution_tools import (
    get_scheduled_script_execution as get_scheduled_script_execution_tool,
)
from servicenow_mcp.tools.scheduled_script_execution_tools import (
    list_scheduled_script_executions as list_scheduled_script_executions_tool,
)
from servicenow_mcp.tools.scheduled_script_execution_tools import (
    update_scheduled_script_execution as update_scheduled_script_execution_tool,
)
from servicenow_mcp.tools.script_include_tools import (
    CreateScriptIncludeParams,
    DeleteScriptIncludeParams,
    DetailedUpdateScriptIncludeParams,
    EditableFieldsResponse,
    GetEditableFieldsParams,
    GetScriptIncludeParams,
    ListScriptIncludesParams,
    ScriptIncludeResponse,
    UpdateScriptIncludeParams,
)
from servicenow_mcp.tools.script_include_tools import (
    create_script_include as create_script_include_tool,
)
from servicenow_mcp.tools.script_include_tools import (
    delete_script_include as delete_script_include_tool,
)
from servicenow_mcp.tools.script_include_tools import (
    detailed_update_script_include as detailed_update_script_include_tool,
)
from servicenow_mcp.tools.script_include_tools import (
    get_editable_fields as get_editable_fields_tool,
)
from servicenow_mcp.tools.script_include_tools import (
    get_script_include as get_script_include_tool,
)
from servicenow_mcp.tools.script_include_tools import (
    list_script_includes as list_script_includes_tool,
)
from servicenow_mcp.tools.script_include_tools import (
    update_script_include as update_script_include_tool,
)
from servicenow_mcp.tools.scrum_task_tools import (
    CreateScrumTaskParams,
    ListScrumTasksParams,
    UpdateScrumTaskParams,
)
from servicenow_mcp.tools.scrum_task_tools import (
    create_scrum_task as create_scrum_task_tool,
)
from servicenow_mcp.tools.scrum_task_tools import (
    list_scrum_tasks as list_scrum_tasks_tool,
)
from servicenow_mcp.tools.scrum_task_tools import (
    update_scrum_task as update_scrum_task_tool,
)
from servicenow_mcp.tools.security_admin_tools import (
    CreateACLParams,
    CreateRoleMappingParams,
    CreateRoleParams,
    GetACLParams,
    GetRoleParams,
    ListACLsParams,
    ListRoleMappingsParams,
    ListRolesParams,
    TestACLAccessParams,
    UpdateACLParams,
    UpdateRoleMappingParams,
    UpdateRoleParams,
    ValidateACLParams,
    ValidateRoleMappingParams,
    ValidateRoleParams,
    ValidateSecurityConstraintsParams,
)
from servicenow_mcp.tools.security_admin_tools import (
    create_acl as create_acl_tool,
)
from servicenow_mcp.tools.security_admin_tools import (
    create_role as create_role_tool,
)
from servicenow_mcp.tools.security_admin_tools import (
    create_role_mapping as create_role_mapping_tool,
)
from servicenow_mcp.tools.security_admin_tools import (
    get_acl as get_acl_tool,
)
from servicenow_mcp.tools.security_admin_tools import (
    get_role as get_role_tool,
)
from servicenow_mcp.tools.security_admin_tools import (
    list_acls as list_acls_tool,
)
from servicenow_mcp.tools.security_admin_tools import (
    list_role_mappings as list_role_mappings_tool,
)
from servicenow_mcp.tools.security_admin_tools import (
    list_roles as list_roles_tool,
)
from servicenow_mcp.tools.security_admin_tools import (
    test_acl_access as test_acl_access_tool,
)
from servicenow_mcp.tools.security_admin_tools import (
    update_acl as update_acl_tool,
)
from servicenow_mcp.tools.security_admin_tools import (
    update_role as update_role_tool,
)
from servicenow_mcp.tools.security_admin_tools import (
    update_role_mapping as update_role_mapping_tool,
)
from servicenow_mcp.tools.security_admin_tools import (
    validate_acl as validate_acl_tool,
)
from servicenow_mcp.tools.security_admin_tools import (
    validate_role as validate_role_tool,
)
from servicenow_mcp.tools.security_admin_tools import (
    validate_role_mapping as validate_role_mapping_tool,
)
from servicenow_mcp.tools.security_admin_tools import (
    validate_security_constraints as validate_security_constraints_tool,
)
from servicenow_mcp.tools.server_side_script_tools import (
    CreateServerSideScriptParams,
    DeleteServerSideScriptParams,
    GetServerSideScriptParams,
    GetServerSideScriptTypeMetadataParams,
    ListServerSideScriptsParams,
    ServerSideScriptResponse,
    UpdateServerSideScriptParams,
)
from servicenow_mcp.tools.server_side_script_tools import (
    create_server_side_script as create_server_side_script_tool,
)
from servicenow_mcp.tools.server_side_script_tools import (
    delete_server_side_script as delete_server_side_script_tool,
)
from servicenow_mcp.tools.server_side_script_tools import (
    get_server_side_script as get_server_side_script_tool,
)
from servicenow_mcp.tools.server_side_script_tools import (
    get_server_side_script_type_metadata as get_server_side_script_type_metadata_tool,
)
from servicenow_mcp.tools.server_side_script_tools import (
    list_server_side_scripts as list_server_side_scripts_tool,
)
from servicenow_mcp.tools.server_side_script_tools import (
    update_server_side_script as update_server_side_script_tool,
)
from servicenow_mcp.tools.sla_tools import (
    CreateSLAParams,
    GetSLAParams,
    ListSLASchedulesParams,
    ListSLAsParams,
    UpdateSLAParams,
)
from servicenow_mcp.tools.sla_tools import (
    create_sla as create_sla_tool,
)
from servicenow_mcp.tools.sla_tools import (
    get_sla as get_sla_tool,
)
from servicenow_mcp.tools.sla_tools import (
    list_sla_schedules as list_sla_schedules_tool,
)
from servicenow_mcp.tools.sla_tools import (
    list_slas as list_slas_tool,
)
from servicenow_mcp.tools.sla_tools import (
    update_sla as update_sla_tool,
)
from servicenow_mcp.tools.story_tools import (
    CreateStoryDependencyParams,
    CreateStoryParams,
    DeleteStoryDependencyParams,
    ListStoriesParams,
    ListStoryDependenciesParams,
    UpdateStoryParams,
)
from servicenow_mcp.tools.story_tools import (
    create_story as create_story_tool,
)
from servicenow_mcp.tools.story_tools import (
    create_story_dependency as create_story_dependency_tool,
)
from servicenow_mcp.tools.story_tools import (
    delete_story_dependency as delete_story_dependency_tool,
)
from servicenow_mcp.tools.story_tools import (
    list_stories as list_stories_tool,
)
from servicenow_mcp.tools.story_tools import (
    list_story_dependencies as list_story_dependencies_tool,
)
from servicenow_mcp.tools.story_tools import (
    update_story as update_story_tool,
)
from servicenow_mcp.tools.ui_action_tools import (
    CreateUIActionParams,
    DeleteUIActionParams,
    GetUIActionParams,
    ListUIActionsParams,
    UIActionResponse,
    UpdateUIActionParams,
)
from servicenow_mcp.tools.ui_action_tools import (
    create_ui_action as create_ui_action_tool,
)
from servicenow_mcp.tools.ui_action_tools import (
    delete_ui_action as delete_ui_action_tool,
)
from servicenow_mcp.tools.ui_action_tools import (
    get_ui_action as get_ui_action_tool,
)
from servicenow_mcp.tools.ui_action_tools import (
    list_ui_actions as list_ui_actions_tool,
)
from servicenow_mcp.tools.ui_action_tools import (
    update_ui_action as update_ui_action_tool,
)
from servicenow_mcp.tools.ui_policy_tools import (
    CreateUIPolicyActionParams,
    CreateUIPolicyParams,
    DeleteUIPolicyActionParams,
    DeleteUIPolicyParams,
    GetUIPolicyActionParams,
    GetUIPolicyParams,
    ListUIPoliciesParams,
    ListUIPolicyActionsParams,
    UIPolicyActionResponse,
    UIPolicyResponse,
    UpdateUIPolicyActionParams,
    UpdateUIPolicyParams,
)
from servicenow_mcp.tools.ui_policy_tools import (
    create_ui_policy as create_ui_policy_tool,
)
from servicenow_mcp.tools.ui_policy_tools import (
    create_ui_policy_action as create_ui_policy_action_tool,
)
from servicenow_mcp.tools.ui_policy_tools import (
    delete_ui_policy as delete_ui_policy_tool,
)
from servicenow_mcp.tools.ui_policy_tools import (
    delete_ui_policy_action as delete_ui_policy_action_tool,
)
from servicenow_mcp.tools.ui_policy_tools import (
    get_ui_policy as get_ui_policy_tool,
)
from servicenow_mcp.tools.ui_policy_tools import (
    get_ui_policy_action as get_ui_policy_action_tool,
)
from servicenow_mcp.tools.ui_policy_tools import (
    list_ui_policies as list_ui_policies_tool,
)
from servicenow_mcp.tools.ui_policy_tools import (
    list_ui_policy_actions as list_ui_policy_actions_tool,
)
from servicenow_mcp.tools.ui_policy_tools import (
    update_ui_policy as update_ui_policy_tool,
)
from servicenow_mcp.tools.ui_policy_tools import (
    update_ui_policy_action as update_ui_policy_action_tool,
)
from servicenow_mcp.tools.user_tools import (
    AddGroupMembersParams,
    CreateGroupParams,
    CreateUserParams,
    GetUserParams,
    ListGroupsParams,
    ListUsersParams,
    RemoveGroupMembersParams,
    UpdateGroupParams,
    UpdateUserParams,
)
from servicenow_mcp.tools.user_tools import (
    add_group_members as add_group_members_tool,
)
from servicenow_mcp.tools.user_tools import (
    create_group as create_group_tool,
)
from servicenow_mcp.tools.user_tools import (
    create_user as create_user_tool,
)
from servicenow_mcp.tools.user_tools import (
    get_user as get_user_tool,
)
from servicenow_mcp.tools.user_tools import (
    list_groups as list_groups_tool,
)
from servicenow_mcp.tools.user_tools import (
    list_users as list_users_tool,
)
from servicenow_mcp.tools.user_tools import (
    remove_group_members as remove_group_members_tool,
)
from servicenow_mcp.tools.user_tools import (
    update_group as update_group_tool,
)
from servicenow_mcp.tools.user_tools import (
    update_user as update_user_tool,
)
from servicenow_mcp.tools.workflow_tools import (
    ActivateWorkflowParams,
    AddWorkflowActivityParams,
    CreateWorkflowParams,
    DeactivateWorkflowParams,
    DeleteWorkflowActivityParams,
    DeleteWorkflowParams,
    GetWorkflowActivitiesParams,
    GetWorkflowDetailsParams,
    ListWorkflowsParams,
    ListWorkflowVersionsParams,
    PublishWorkflowParams,
    ReorderWorkflowActivitiesParams,
    UpdateWorkflowActivityParams,
    UpdateWorkflowParams,
)
from servicenow_mcp.tools.workflow_tools import (
    activate_workflow as activate_workflow_tool,
)
from servicenow_mcp.tools.workflow_tools import (
    add_workflow_activity as add_workflow_activity_tool,
)
from servicenow_mcp.tools.workflow_tools import (
    create_workflow as create_workflow_tool,
)
from servicenow_mcp.tools.workflow_tools import (
    deactivate_workflow as deactivate_workflow_tool,
)
from servicenow_mcp.tools.workflow_tools import (
    delete_workflow as delete_workflow_tool,
)
from servicenow_mcp.tools.workflow_tools import (
    delete_workflow_activity as delete_workflow_activity_tool,
)
from servicenow_mcp.tools.workflow_tools import (
    get_workflow_activities as get_workflow_activities_tool,
)
from servicenow_mcp.tools.workflow_tools import (
    get_workflow_details as get_workflow_details_tool,
)
from servicenow_mcp.tools.workflow_tools import (
    list_workflow_versions as list_workflow_versions_tool,
)
from servicenow_mcp.tools.workflow_tools import (
    list_workflows as list_workflows_tool,
)
from servicenow_mcp.tools.workflow_tools import (
    publish_workflow as publish_workflow_tool,
)
from servicenow_mcp.tools.workflow_tools import (
    reorder_workflow_activities as reorder_workflow_activities_tool,
)
from servicenow_mcp.tools.workflow_tools import (
    update_workflow as update_workflow_tool,
)
from servicenow_mcp.tools.workflow_tools import (
    update_workflow_activity as update_workflow_activity_tool,
)

# Define a type alias for the Pydantic models or dataclasses used for params
ParamsModel = Type[Any]  # Use Type[Any] for broader compatibility initially

# Define the structure of the tool definition tuple
ToolDefinition = Tuple[
    Callable,  # Implementation function
    ParamsModel,  # Pydantic model for parameters
    Type,  # Return type annotation (used for hints, not strictly enforced by low-level server)
    str,  # Description
    str,  # Serialization method ('str', 'json', 'dict', 'model_dump', etc.)
]


def get_tool_definitions(
    create_kb_category_tool_impl: Callable | None = None,
    list_kb_categories_tool_impl: Callable | None = None,
) -> Dict[str, ToolDefinition]:
    """
    Returns a dictionary containing definitions for all available ServiceNow tools.

    This centralizes the tool definitions for use in the server implementation.
    Pass aliased functions for KB categories directly.

    Returns:
        Dict[str, ToolDefinition]: A dictionary mapping tool names to their definitions.
    """
    if create_kb_category_tool_impl is None or list_kb_categories_tool_impl is None:
        from servicenow_mcp.tools.knowledge_base import create_category, list_categories

        create_kb_category_tool_impl = create_kb_category_tool_impl or create_category
        list_kb_categories_tool_impl = list_kb_categories_tool_impl or list_categories

    tool_definitions: Dict[str, ToolDefinition] = {
        # Incident Tools
        "create_incident": (
            create_incident_tool,
            CreateIncidentParams,
            str,
            "Create a new incident in ServiceNow",
            "str",
        ),
        "update_incident": (
            update_incident_tool,
            UpdateIncidentParams,
            str,
            "Update an existing incident in ServiceNow",
            "str",
        ),
        "add_comment": (
            add_comment_tool,
            AddCommentParams,
            str,
            "Add a comment to an incident in ServiceNow",
            "str",
        ),
        "resolve_incident": (
            resolve_incident_tool,
            ResolveIncidentParams,
            str,
            "Resolve an incident in ServiceNow",
            "str",
        ),
        "list_incidents": (
            list_incidents_tool,
            ListIncidentsParams,
            str,  # Expects JSON string
            "List incidents from ServiceNow",
            "json",  # Tool returns list/dict, needs JSON dump
        ),
        "get_incident_by_number": (
            get_incident_by_number_tool,
            GetIncidentByNumberParams,
            str,
            "Incident details from ServiceNow",
            "json_dict",
        ),
        # Catalog Tools
        "list_catalogs": (
            list_catalogs_tool,
            ListCatalogsParams,
            Dict[str, Any],
            "List service catalogs.",
            "raw_dict",
        ),
        "get_catalog": (
            get_catalog_tool,
            GetCatalogParams,
            Dict[str, Any],
            "Get a specific service catalog.",
            "raw_dict",
        ),
        "create_catalog": (
            create_catalog_tool,
            CreateCatalogParams,
            Dict[str, Any],
            "Create a new service catalog.",
            "raw_dict",
        ),
        "update_catalog": (
            update_catalog_tool,
            UpdateCatalogParams,
            Dict[str, Any],
            "Update an existing service catalog.",
            "raw_dict",
        ),
        "list_catalog_items": (
            list_catalog_items_tool,
            ListCatalogItemsParams,
            str,  # Expects JSON string
            "List service catalog items.",
            "json",  # Tool returns list/dict
        ),
        "get_catalog_item": (
            get_catalog_item_tool,
            GetCatalogItemParams,
            str,  # Expects JSON string
            "Get a specific service catalog item.",
            "json_dict",  # Tool returns Pydantic model
        ),
        "list_catalog_categories": (
            list_catalog_categories_tool,
            ListCatalogCategoriesParams,
            str,  # Expects JSON string
            "List service catalog categories.",
            "json",  # Tool returns list/dict
        ),
        "create_catalog_category": (
            create_catalog_category_tool,
            CreateCatalogCategoryParams,
            str,  # Expects JSON string
            "Create a new service catalog category.",
            "json_dict",  # Tool returns Pydantic model
        ),
        "update_catalog_category": (
            update_catalog_category_tool,
            UpdateCatalogCategoryParams,
            str,  # Expects JSON string
            "Update an existing service catalog category.",
            "json_dict",  # Tool returns Pydantic model
        ),
        "move_catalog_items": (
            move_catalog_items_tool,
            MoveCatalogItemsParams,
            str,  # Expects JSON string
            "Move catalog items to a different category.",
            "json_dict",  # Tool returns Pydantic model
        ),
        "get_optimization_recommendations": (
            get_optimization_recommendations_tool,
            OptimizationRecommendationsParams,
            str,  # Expects JSON string
            "Get optimization recommendations for the service catalog.",
            "json",  # Tool returns list/dict
        ),
        "update_catalog_item": (
            update_catalog_item_tool,
            UpdateCatalogItemParams,
            str,  # Expects JSON string
            "Update a service catalog item.",
            "json",  # Tool returns Pydantic model
        ),
        "create_catalog_item": (
            create_catalog_item_tool,
            CreateCatalogItemParams,
            Dict[str, Any],
            "Create a new service catalog item.",
            "raw_dict",
        ),
        "publish_catalog_item": (
            publish_catalog_item_tool,
            PublishCatalogItemParams,
            Dict[str, Any],
            (
                "Publish a service catalog item by making it active and optionally "
                "setting extra sc_cat_item fields."
            ),
            "raw_dict",
        ),
        # Catalog Variables
        "add_standard_order_variables": (
            add_standard_order_variables_tool,
            AddStandardOrderVariablesParams,
            Dict[str, Any],
            (
                "Create a standard order form variable set: quantity, size, date, "
                "and special instructions."
            ),
            "raw_dict",
        ),
        "create_catalog_variable_behavior_policy": (
            create_catalog_variable_behavior_policy_tool,
            CreateCatalogVariableBehaviorPolicyParams,
            Dict[str, Any],
            (
                "Create a Catalog UI Policy to control variable show/hide, mandatory, "
                "and read-only behavior."
            ),
            "raw_dict",
        ),
        "create_catalog_item_variable": (
            create_catalog_item_variable_tool,
            CreateCatalogItemVariableParams,
            Dict[str, Any],  # Expects dict
            "Create a new catalog item variable. Use attributes for extra item_option_new fields.",
            "dict",  # Tool returns Pydantic model
        ),
        "create_catalog_item_variables": (
            create_catalog_item_variables_tool,
            CreateCatalogItemVariablesParams,
            Dict[str, Any],
            "Create multiple catalog item variables in one call, optionally with choices.",
            "raw_dict",
        ),
        "get_catalog_variable_metadata": (
            get_catalog_variable_metadata_tool,
            GetCatalogVariableMetadataParams,
            Dict[str, Any],
            (
                "Get item_option_new type choices and field metadata before creating "
                "catalog variables."
            ),
            "raw_dict",
        ),
        "list_catalog_item_variables": (
            list_catalog_item_variables_tool,
            ListCatalogItemVariablesParams,
            Dict[str, Any],  # Expects dict
            "List catalog item variables",
            "dict",  # Tool returns Pydantic model
        ),
        "set_catalog_variable_behavior": (
            set_catalog_variable_behavior_tool,
            SetCatalogVariableBehaviorParams,
            Dict[str, Any],
            "Set simple catalog variable behavior flags such as hidden, mandatory, and read-only.",
            "dict",
        ),
        "update_catalog_item_variable": (
            update_catalog_item_variable_tool,
            UpdateCatalogItemVariableParams,
            Dict[str, Any],  # Expects dict
            "Update a catalog item variable. Use attributes for extra item_option_new fields.",
            "dict",  # Tool returns Pydantic model
        ),
        "delete_catalog_item_variable": (
            delete_catalog_item_variable_tool,
            DeleteCatalogItemVariableParams,
            Dict[str, Any],
            "Delete a catalog item variable",
            "raw_dict",
        ),
        "create_catalog_variable_choice": (
            create_catalog_variable_choice_tool,
            CreateCatalogVariableChoiceParams,
            Dict[str, Any],
            "Create an allowed choice for a catalog variable",
            "raw_dict",
        ),
        "list_catalog_client_scripts": (
            list_catalog_client_scripts_tool,
            ListCatalogClientScriptsParams,
            Dict[str, Any],
            "List catalog client scripts",
            "raw_dict",
        ),
        "get_catalog_client_script": (
            get_catalog_client_script_tool,
            GetCatalogClientScriptParams,
            Dict[str, Any],
            "Get a catalog client script",
            "raw_dict",
        ),
        "create_catalog_client_script": (
            create_catalog_client_script_tool,
            CreateCatalogClientScriptParams,
            Dict[str, Any],
            "Create a catalog client script",
            "raw_dict",
        ),
        "update_catalog_client_script": (
            update_catalog_client_script_tool,
            UpdateCatalogClientScriptParams,
            Dict[str, Any],
            "Update a catalog client script",
            "raw_dict",
        ),
        # Change Management Tools
        "create_change_request": (
            create_change_request_tool,
            CreateChangeRequestParams,
            str,
            "Create a new change request in ServiceNow",
            "str",
        ),
        "update_change_request": (
            update_change_request_tool,
            UpdateChangeRequestParams,
            str,
            "Update an existing change request in ServiceNow",
            "str",
        ),
        "list_change_requests": (
            list_change_requests_tool,
            ListChangeRequestsParams,
            str,  # Expects JSON string
            "List change requests from ServiceNow",
            "json",  # Tool returns list/dict
        ),
        "get_change_request_details": (
            get_change_request_details_tool,
            GetChangeRequestDetailsParams,
            str,  # Expects JSON string
            "Get detailed information about a specific change request",
            "json",  # Tool returns list/dict
        ),
        "add_change_task": (
            add_change_task_tool,
            AddChangeTaskParams,
            str,  # Expects JSON string
            "Add a task to a change request",
            "json_dict",  # Tool returns Pydantic model
        ),
        "submit_change_for_approval": (
            submit_change_for_approval_tool,
            SubmitChangeForApprovalParams,
            str,
            "Submit a change request for approval",
            "str",  # Tool returns simple message
        ),
        "approve_change": (
            approve_change_tool,
            ApproveChangeParams,
            str,
            "Approve a change request",
            "str",  # Tool returns simple message
        ),
        "reject_change": (
            reject_change_tool,
            RejectChangeParams,
            str,
            "Reject a change request",
            "str",  # Tool returns simple message
        ),
        # Request Management Tools
        "list_requests": (
            list_requests_tool,
            ListRequestsParams,
            Dict[str, Any],
            "List Service Catalog requests",
            "raw_dict",
        ),
        "get_request": (
            get_request_tool,
            GetRequestParams,
            Dict[str, Any],
            "Get a request with requested items, catalog tasks, and approvals",
            "raw_dict",
        ),
        "get_catalog_item_request_options": (
            get_catalog_item_request_options_tool,
            GetCatalogItemRequestOptionsParams,
            Dict[str, Any],
            (
                "Fetch the existing request form variables and allowed options for a "
                "catalog item before submitting a request"
            ),
            "raw_dict",
        ),
        "create_request": (
            create_request_tool,
            CreateRequestParams,
            Dict[str, Any],
            (
                "Create a Service Catalog request with nested requested items, variable "
                "answers, and catalog tasks, similar to a service portal order"
            ),
            "raw_dict",
        ),
        "update_request": (
            update_request_tool,
            UpdateRequestParams,
            Dict[str, Any],
            (
                "Update a Service Catalog request and optionally append requested items, "
                "variable answers, and catalog tasks"
            ),
            "raw_dict",
        ),
        "list_requested_items": (
            list_requested_items_tool,
            ListRequestedItemsParams,
            Dict[str, Any],
            "List requested items",
            "raw_dict",
        ),
        "get_requested_item": (
            get_requested_item_tool,
            GetRequestedItemParams,
            Dict[str, Any],
            "Get a requested item with variable answers, tasks, and approvals",
            "raw_dict",
        ),
        "create_requested_item": (
            create_requested_item_tool,
            CreateRequestedItemParams,
            Dict[str, Any],
            (
                "Create a requested item under a request, submit variable answers, "
                "and optionally create its catalog tasks"
            ),
            "raw_dict",
        ),
        "update_requested_item": (
            update_requested_item_tool,
            UpdateRequestedItemParams,
            Dict[str, Any],
            "Update a requested item, add variable answers, and optionally add more catalog tasks",
            "raw_dict",
        ),
        "list_catalog_tasks": (
            list_catalog_tasks_tool,
            ListCatalogTasksParams,
            Dict[str, Any],
            "List catalog tasks for requests or requested items",
            "raw_dict",
        ),
        "create_catalog_task": (
            create_catalog_task_tool,
            CreateCatalogTaskParams,
            Dict[str, Any],
            "Create a catalog task for a requested item",
            "raw_dict",
        ),
        "update_catalog_task": (
            update_catalog_task_tool,
            UpdateCatalogTaskParams,
            Dict[str, Any],
            "Update a catalog task",
            "raw_dict",
        ),
        "list_request_approvals": (
            list_request_approvals_tool,
            ListRequestApprovalsParams,
            Dict[str, Any],
            "List request, requested item, and catalog task approvals",
            "raw_dict",
        ),
        "update_request_approval": (
            update_request_approval_tool,
            UpdateRequestApprovalParams,
            Dict[str, Any],
            "Update a request approval record",
            "raw_dict",
        ),
        "get_request_fulfillment_status": (
            get_request_fulfillment_status_tool,
            GetRequestFulfillmentStatusParams,
            Dict[str, Any],
            "Track approvals and fulfillment status for a request end to end",
            "raw_dict",
        ),
        # Problem Management Tools
        "list_problems": (
            list_problems_tool,
            ListProblemsParams,
            Dict[str, Any],
            "List problems from ServiceNow",
            "raw_dict",
        ),
        "get_problem": (
            get_problem_tool,
            GetProblemParams,
            Dict[str, Any],
            "Get a problem with tasks and linked incidents",
            "raw_dict",
        ),
        "create_problem": (
            create_problem_tool,
            CreateProblemParams,
            Dict[str, Any],
            "Create a problem in ServiceNow",
            "raw_dict",
        ),
        "update_problem": (
            update_problem_tool,
            UpdateProblemParams,
            Dict[str, Any],
            "Update a problem in ServiceNow",
            "raw_dict",
        ),
        "resolve_problem": (
            resolve_problem_tool,
            ResolveProblemParams,
            Dict[str, Any],
            "Resolve a problem in ServiceNow",
            "raw_dict",
        ),
        "list_problem_tasks": (
            list_problem_tasks_tool,
            ListProblemTasksParams,
            Dict[str, Any],
            "List problem tasks",
            "raw_dict",
        ),
        "get_problem_task": (
            get_problem_task_tool,
            GetProblemTaskParams,
            Dict[str, Any],
            "Get a problem task",
            "raw_dict",
        ),
        "create_problem_task": (
            create_problem_task_tool,
            CreateProblemTaskParams,
            Dict[str, Any],
            "Create a problem task",
            "raw_dict",
        ),
        "update_problem_task": (
            update_problem_task_tool,
            UpdateProblemTaskParams,
            Dict[str, Any],
            "Update a problem task",
            "raw_dict",
        ),
        "resolve_problem_task": (
            resolve_problem_task_tool,
            ResolveProblemTaskParams,
            Dict[str, Any],
            "Resolve a problem task",
            "raw_dict",
        ),
        "link_problem_to_incident": (
            link_problem_to_incident_tool,
            LinkProblemToIncidentParams,
            Dict[str, Any],
            "Link a problem to an incident",
            "raw_dict",
        ),
        "link_problem_to_known_error": (
            link_problem_to_known_error_tool,
            LinkProblemToKnownErrorParams,
            Dict[str, Any],
            "Mark or link a problem to a known error record",
            "raw_dict",
        ),
        # Notification, Event, and Script Action Tools
        "list_notifications": (
            list_notifications_tool,
            ListNotificationsParams,
            Dict[str, Any],
            "List email notifications from ServiceNow",
            "raw_dict",
        ),
        "get_notification": (
            get_notification_tool,
            GetNotificationParams,
            Dict[str, Any],
            "Get an email notification from ServiceNow",
            "raw_dict",
        ),
        "create_notification": (
            create_notification_tool,
            CreateNotificationParams,
            Dict[str, Any],
            "Create an email notification in ServiceNow",
            "raw_dict",
        ),
        "update_notification": (
            update_notification_tool,
            UpdateNotificationParams,
            Dict[str, Any],
            "Update an email notification in ServiceNow",
            "raw_dict",
        ),
        "list_email_templates": (
            list_email_templates_tool,
            ListEmailTemplatesParams,
            Dict[str, Any],
            "List email templates from ServiceNow",
            "raw_dict",
        ),
        "get_email_template": (
            get_email_template_tool,
            GetEmailTemplateParams,
            Dict[str, Any],
            "Get an email template from ServiceNow",
            "raw_dict",
        ),
        "create_email_template": (
            create_email_template_tool,
            CreateEmailTemplateParams,
            Dict[str, Any],
            "Create an email template in ServiceNow",
            "raw_dict",
        ),
        "update_email_template": (
            update_email_template_tool,
            UpdateEmailTemplateParams,
            Dict[str, Any],
            "Update an email template in ServiceNow",
            "raw_dict",
        ),
        "list_event_definitions": (
            list_event_definitions_tool,
            ListEventDefinitionsParams,
            Dict[str, Any],
            "List event registry definitions from ServiceNow",
            "raw_dict",
        ),
        "get_event_definition": (
            get_event_definition_tool,
            GetEventDefinitionParams,
            Dict[str, Any],
            "Get an event registry definition from ServiceNow",
            "raw_dict",
        ),
        "create_event_definition": (
            create_event_definition_tool,
            CreateEventDefinitionParams,
            Dict[str, Any],
            "Create an event registry definition in ServiceNow",
            "raw_dict",
        ),
        "update_event_definition": (
            update_event_definition_tool,
            UpdateEventDefinitionParams,
            Dict[str, Any],
            "Update an event registry definition in ServiceNow",
            "raw_dict",
        ),
        "queue_event": (
            queue_event_tool,
            QueueEventParams,
            Dict[str, Any],
            "Queue a ServiceNow event",
            "raw_dict",
        ),
        "list_script_actions": (
            list_script_actions_tool,
            ListScriptActionsParams,
            Dict[str, Any],
            "List script actions from ServiceNow",
            "raw_dict",
        ),
        "get_script_action": (
            get_script_action_tool,
            GetScriptActionParams,
            Dict[str, Any],
            "Get a script action from ServiceNow",
            "raw_dict",
        ),
        "create_script_action": (
            create_script_action_tool,
            CreateScriptActionParams,
            Dict[str, Any],
            "Create a script action in ServiceNow",
            "raw_dict",
        ),
        "update_script_action": (
            update_script_action_tool,
            UpdateScriptActionParams,
            Dict[str, Any],
            "Update a script action in ServiceNow",
            "raw_dict",
        ),
        "delete_script_action": (
            delete_script_action_tool,
            DeleteScriptActionParams,
            Dict[str, Any],
            "Delete a script action from ServiceNow",
            "raw_dict",
        ),
        # SLA Definition Tools
        "list_slas": (
            list_slas_tool,
            ListSLAsParams,
            Dict[str, Any],
            "List SLA definitions from ServiceNow",
            "raw_dict",
        ),
        "list_sla_schedules": (
            list_sla_schedules_tool,
            ListSLASchedulesParams,
            Dict[str, Any],
            "List schedules available for SLA definitions",
            "raw_dict",
        ),
        "get_sla": (
            get_sla_tool,
            GetSLAParams,
            Dict[str, Any],
            "Get a specific SLA definition from ServiceNow",
            "raw_dict",
        ),
        "create_sla": (
            create_sla_tool,
            CreateSLAParams,
            Dict[str, Any],
            "Create an SLA definition in ServiceNow",
            "raw_dict",
        ),
        "update_sla": (
            update_sla_tool,
            UpdateSLAParams,
            Dict[str, Any],
            "Update an SLA definition in ServiceNow",
            "raw_dict",
        ),
        # Workflow Management Tools
        "list_workflows": (
            list_workflows_tool,
            ListWorkflowsParams,
            str,  # Expects JSON string
            "List workflows from ServiceNow",
            "json",  # Tool returns list/dict
        ),
        "get_workflow_details": (
            get_workflow_details_tool,
            GetWorkflowDetailsParams,
            str,  # Expects JSON string
            "Get detailed information about a specific workflow",
            "json",  # Tool returns list/dict
        ),
        "list_workflow_versions": (
            list_workflow_versions_tool,
            ListWorkflowVersionsParams,
            str,  # Expects JSON string
            "List workflow versions from ServiceNow",
            "json",  # Tool returns list/dict
        ),
        "get_workflow_activities": (
            get_workflow_activities_tool,
            GetWorkflowActivitiesParams,
            str,  # Expects JSON string
            "Get activities for a specific workflow",
            "json",  # Tool returns list/dict
        ),
        "create_flow": (
            create_flow_tool,
            CreateFlowParams,
            str,
            (
                "Deploy a Flow Designer flow by sending the same top-level JSON shape "
                "as the Flow Server request examples to the /deploy-flow endpoint. "
                "This is the deploy/build/install/verify path for Flow Designer flows; "
                "do not follow it with activate_workflow or publish_workflow. Supports "
                "the action types returned by list_flow_actions from the Flow Server "
                "/available-actions catalog."
            ),
            "json_dict",
        ),
        "edit_flow": (
            edit_flow_tool,
            EditFlowParams,
            str,
            (
                "Edit an existing Flow Designer Flow Server JSON body, validate the edited "
                "flowSpec, and optionally redeploy it through /deploy-flow."
            ),
            "json_dict",
        ),
        "list_flow_actions": (
            list_flow_actions_tool,
            ListFlowActionsParams,
            str,
            (
                "List Flow Server SDK action types from /available-actions, including "
                "input keys and optional bounded snippets."
            ),
            "json_dict",
        ),
        "create_incident_admin_notification_flow": (
            create_incident_admin_notification_flow_tool,
            CreateIncidentAdminNotificationFlowParams,
            str,
            (
                "One-call Flow Designer deployment for: when an incident is created, "
                "log the event and email the admin. Defaults to scopeId=global, "
                "scopeName=Global, recipient=admin@example.com, and does not run "
                "post-deployment incident tests unless the user explicitly asks."
            ),
            "json_dict",
        ),
        "validate_flow_json": (
            validate_flow_json_tool,
            ValidateFlowJsonParams,
            str,
            "Validate a top-level Flow Server JSON body without deploying it.",
            "json_dict",
        ),
        "hint_flow_json": (
            hint_flow_json_tool,
            HintFlowJsonParams,
            str,
            "Return a ready-to-fill top-level Flow Server JSON body.",
            "json_dict",
        ),
        "validate_subflow_json": (
            validate_subflow_json_tool,
            ValidateSubflowJsonParams,
            str,
            "Validate a top-level Flow Server Subflow JSON body without deploying it.",
            "json_dict",
        ),
        "hint_subflow_json": (
            hint_subflow_json_tool,
            HintSubflowJsonParams,
            str,
            "Return a ready-to-fill top-level Flow Server Subflow JSON body.",
            "json_dict",
        ),
        "generate_subflow_source": (
            generate_subflow_source_tool,
            GenerateSubflowSourceParams,
            str,
            "Generate bounded ServiceNow SDK source for a Flow Designer Subflow.",
            "json_dict",
        ),
        "deploy_subflow": (
            deploy_subflow_tool,
            DeploySubflowParams,
            str,
            ("Deploy a Flow Designer Subflow through the Flow Server /deploy-subflow endpoint."),
            "json_dict",
        ),
        "verify_subflow": (
            verify_subflow_tool,
            VerifySubflowParams,
            str,
            "Verify that a Flow Designer Subflow exists through the Flow Server.",
            "json_dict",
        ),
        "create_workflow": (
            create_workflow_tool,
            CreateWorkflowParams,
            str,  # Expects JSON string
            "Create a new workflow in ServiceNow",
            "json_dict",  # Tool returns Pydantic model
        ),
        "update_workflow": (
            update_workflow_tool,
            UpdateWorkflowParams,
            str,  # Expects JSON string
            "Update an existing workflow in ServiceNow",
            "json_dict",  # Tool returns Pydantic model
        ),
        "activate_workflow": (
            activate_workflow_tool,
            ActivateWorkflowParams,
            str,
            "Activate a classic ServiceNow workflow; do not use for Flow Designer flows.",
            "str",  # Tool returns simple message
        ),
        "deactivate_workflow": (
            deactivate_workflow_tool,
            DeactivateWorkflowParams,
            str,
            "Deactivate a workflow in ServiceNow",
            "str",  # Tool returns simple message
        ),
        "publish_workflow": (
            publish_workflow_tool,
            PublishWorkflowParams,
            str,
            "Publish a classic workflow; do not use for Flow Designer flows from create_flow.",
            "str",  # Tool returns simple message
        ),
        "add_workflow_activity": (
            add_workflow_activity_tool,
            AddWorkflowActivityParams,
            str,  # Expects JSON string
            "Add a new activity to a workflow in ServiceNow",
            "json_dict",  # Tool returns Pydantic model
        ),
        "update_workflow_activity": (
            update_workflow_activity_tool,
            UpdateWorkflowActivityParams,
            str,  # Expects JSON string
            "Update an existing activity in a workflow",
            "json_dict",  # Tool returns Pydantic model
        ),
        "delete_workflow_activity": (
            delete_workflow_activity_tool,
            DeleteWorkflowActivityParams,
            str,
            "Delete an activity from a workflow",
            "str",  # Tool returns simple message
        ),
        "reorder_workflow_activities": (
            reorder_workflow_activities_tool,
            ReorderWorkflowActivitiesParams,
            str,
            "Reorder activities in a workflow",
            "str",  # Tool returns simple message
        ),
        "delete_workflow": (
            delete_workflow_tool,
            DeleteWorkflowParams,
            str,
            "Delete a workflow from ServiceNow",
            "str",  # Tool returns simple message
        ),
        # Changeset Management Tools
        "list_changesets": (
            list_changesets_tool,
            ListChangesetsParams,
            str,  # Expects JSON string
            "List changesets from ServiceNow",
            "json",  # Tool returns list/dict
        ),
        "get_changeset_details": (
            get_changeset_details_tool,
            GetChangesetDetailsParams,
            str,  # Expects JSON string
            "Get detailed information about a specific changeset",
            "json",  # Tool returns list/dict
        ),
        "create_changeset": (
            create_changeset_tool,
            CreateChangesetParams,
            str,  # Expects JSON string
            "Create a new changeset in ServiceNow",
            "json_dict",  # Tool returns Pydantic model
        ),
        "update_changeset": (
            update_changeset_tool,
            UpdateChangesetParams,
            str,  # Expects JSON string
            "Update an existing changeset in ServiceNow",
            "json_dict",  # Tool returns Pydantic model
        ),
        "commit_changeset": (
            commit_changeset_tool,
            CommitChangesetParams,
            str,
            "Commit a changeset in ServiceNow",
            "str",  # Tool returns simple message
        ),
        "publish_changeset": (
            publish_changeset_tool,
            PublishChangesetParams,
            str,
            "Publish a changeset in ServiceNow",
            "str",  # Tool returns simple message
        ),
        "add_file_to_changeset": (
            add_file_to_changeset_tool,
            AddFileToChangesetParams,
            str,
            "Add a file to a changeset in ServiceNow",
            "str",  # Tool returns simple message
        ),
        "list_update_sets": (
            list_update_sets_tool,
            ListUpdateSetsParams,
            Dict[str, Any],
            "List update sets from ServiceNow",
            "raw_dict",
        ),
        "get_update_set_details": (
            get_update_set_details_tool,
            GetUpdateSetDetailsParams,
            Dict[str, Any],
            "Get detailed information about a specific update set",
            "raw_dict",
        ),
        "create_update_set": (
            create_update_set_tool,
            CreateUpdateSetParams,
            Dict[str, Any],
            "Create a new update set in ServiceNow",
            "raw_dict",
        ),
        "switch_current_update_set": (
            switch_current_update_set_tool,
            SwitchCurrentUpdateSetParams,
            Dict[str, Any],
            "Switch the current update set for the active user",
            "raw_dict",
        ),
        "commit_update_set": (
            commit_update_set_tool,
            CommitUpdateSetParams,
            Dict[str, Any],
            "Commit an update set in ServiceNow",
            "raw_dict",
        ),
        "preview_update_set": (
            preview_update_set_tool,
            PreviewUpdateSetParams,
            Dict[str, Any],
            "Preview update set promotion details and preview problems",
            "raw_dict",
        ),
        "validate_update_set": (
            validate_update_set_tool,
            ValidateUpdateSetParams,
            Dict[str, Any],
            "Validate whether an update set is ready for promotion",
            "raw_dict",
        ),
        "promote_update_set": (
            promote_update_set_tool,
            PromoteUpdateSetParams,
            Dict[str, Any],
            "Prepare and validate an update set promotion workflow",
            "raw_dict",
        ),
        "get_instance_build_info": (
            get_instance_build_info_tool,
            GetInstanceBuildInfoParams,
            Dict[str, Any],
            "Detect the connected ServiceNow instance build and release family",
            "raw_dict",
        ),
        "get_release_behavior": (
            get_release_behavior_tool,
            GetReleaseBehaviorParams,
            Dict[str, Any],
            "Return release-aware behavior hints for a module or feature area",
            "raw_dict",
        ),
        "get_enabled_module_release_behaviors": (
            get_enabled_module_release_behaviors_tool,
            GetEnabledModuleReleaseBehaviorsParams,
            Dict[str, Any],
            "Return release-aware details for all modules represented by the enabled tools",
            "raw_dict",
        ),
        # Script Include Tools
        "list_script_includes": (
            list_script_includes_tool,
            ListScriptIncludesParams,
            Dict[str, Any],  # Expects dict
            "List script includes from ServiceNow",
            "raw_dict",  # Tool returns raw dict
        ),
        "get_script_include": (
            get_script_include_tool,
            GetScriptIncludeParams,
            Dict[str, Any],  # Expects dict
            "Get a specific script include from ServiceNow",
            "raw_dict",  # Tool returns raw dict
        ),
        "create_script_include": (
            create_script_include_tool,
            CreateScriptIncludeParams,
            ScriptIncludeResponse,  # Expects Pydantic model
            "Create a new script include in ServiceNow",
            "raw_pydantic",  # Tool returns Pydantic model
        ),
        "update_script_include": (
            update_script_include_tool,
            UpdateScriptIncludeParams,
            ScriptIncludeResponse,  # Expects Pydantic model
            "Update an existing script include in ServiceNow",
            "raw_pydantic",  # Tool returns Pydantic model
        ),
        "delete_script_include": (
            delete_script_include_tool,
            DeleteScriptIncludeParams,
            str,  # Expects JSON string
            "Delete a script include in ServiceNow",
            "json_dict",  # Tool returns Pydantic model
        ),
        "get_editable_fields": (
            get_editable_fields_tool,
            GetEditableFieldsParams,
            EditableFieldsResponse,  # Expects Pydantic model
            "Get information about editable fields for script includes",
            "raw_pydantic",  # Tool returns Pydantic model
        ),
        "detailed_update_script_include": (
            detailed_update_script_include_tool,
            DetailedUpdateScriptIncludeParams,
            ScriptIncludeResponse,  # Expects Pydantic model
            "Update a script include with detailed validation and additional fields",
            "raw_pydantic",  # Tool returns Pydantic model
        ),
        # Generic Server-side Script and Scheduled Job Tools
        "list_server_side_scripts": (
            list_server_side_scripts_tool,
            ListServerSideScriptsParams,
            Dict[str, Any],
            (
                "List server-side scripts, scheduled script executions, scheduled jobs, "
                "or custom script tables"
            ),
            "raw_dict",
        ),
        "get_server_side_script": (
            get_server_side_script_tool,
            GetServerSideScriptParams,
            Dict[str, Any],
            (
                "Get a server-side script, scheduled script execution, scheduled job, "
                "or custom script table record"
            ),
            "raw_dict",
        ),
        "create_server_side_script": (
            create_server_side_script_tool,
            CreateServerSideScriptParams,
            ServerSideScriptResponse,
            (
                "Create a server-side script, scheduled script execution, scheduled job, "
                "or custom script table record"
            ),
            "raw_pydantic",
        ),
        "update_server_side_script": (
            update_server_side_script_tool,
            UpdateServerSideScriptParams,
            ServerSideScriptResponse,
            (
                "Update a server-side script, scheduled script execution, scheduled job, "
                "or custom script table record"
            ),
            "raw_pydantic",
        ),
        "delete_server_side_script": (
            delete_server_side_script_tool,
            DeleteServerSideScriptParams,
            ServerSideScriptResponse,
            "Delete a server-side script, scheduled job, or custom script table record",
            "raw_pydantic",
        ),
        "get_server_side_script_type_metadata": (
            get_server_side_script_type_metadata_tool,
            GetServerSideScriptTypeMetadataParams,
            Dict[str, Any],
            "Get trigger, table, field, and tool guidance for server-side script types",
            "raw_dict",
        ),
        # Scheduled Script Execution Tools
        "list_scheduled_script_executions": (
            list_scheduled_script_executions_tool,
            ListScheduledScriptExecutionsParams,
            Dict[str, Any],
            "List Scheduled Script Executions from sysauto_script",
            "raw_dict",
        ),
        "get_scheduled_script_execution": (
            get_scheduled_script_execution_tool,
            GetScheduledScriptExecutionParams,
            Dict[str, Any],
            "Get a Scheduled Script Execution from sysauto_script",
            "raw_dict",
        ),
        "create_scheduled_script_execution": (
            create_scheduled_script_execution_tool,
            CreateScheduledScriptExecutionParams,
            ServerSideScriptResponse,
            "Create a Scheduled Script Execution in sysauto_script",
            "raw_pydantic",
        ),
        "update_scheduled_script_execution": (
            update_scheduled_script_execution_tool,
            UpdateScheduledScriptExecutionParams,
            ServerSideScriptResponse,
            "Update a Scheduled Script Execution in sysauto_script",
            "raw_pydantic",
        ),
        "delete_scheduled_script_execution": (
            delete_scheduled_script_execution_tool,
            DeleteScheduledScriptExecutionParams,
            ServerSideScriptResponse,
            "Delete a Scheduled Script Execution from sysauto_script",
            "raw_pydantic",
        ),
        # Scheduled Job Tools
        "list_scheduled_jobs": (
            list_scheduled_jobs_tool,
            ListScheduledJobsParams,
            Dict[str, Any],
            "List Scheduled Jobs from sysauto",
            "raw_dict",
        ),
        "get_scheduled_job": (
            get_scheduled_job_tool,
            GetScheduledJobParams,
            Dict[str, Any],
            "Get a Scheduled Job from sysauto",
            "raw_dict",
        ),
        "create_scheduled_job": (
            create_scheduled_job_tool,
            CreateScheduledJobParams,
            ServerSideScriptResponse,
            "Create a Scheduled Job in sysauto",
            "raw_pydantic",
        ),
        "update_scheduled_job": (
            update_scheduled_job_tool,
            UpdateScheduledJobParams,
            ServerSideScriptResponse,
            "Update a Scheduled Job in sysauto",
            "raw_pydantic",
        ),
        "delete_scheduled_job": (
            delete_scheduled_job_tool,
            DeleteScheduledJobParams,
            ServerSideScriptResponse,
            "Delete a Scheduled Job from sysauto",
            "raw_pydantic",
        ),
        # Background Job Tools
        "list_background_jobs": (
            list_background_jobs_tool,
            ListBackgroundJobsParams,
            Dict[str, Any],
            "List Background Jobs from sys_trigger",
            "raw_dict",
        ),
        "get_background_job": (
            get_background_job_tool,
            GetBackgroundJobParams,
            Dict[str, Any],
            "Get a Background Job from sys_trigger",
            "raw_dict",
        ),
        "create_background_job": (
            create_background_job_tool,
            CreateBackgroundJobParams,
            ServerSideScriptResponse,
            "Create a Background Job in sys_trigger",
            "raw_pydantic",
        ),
        "update_background_job": (
            update_background_job_tool,
            UpdateBackgroundJobParams,
            ServerSideScriptResponse,
            "Update a Background Job in sys_trigger",
            "raw_pydantic",
        ),
        "delete_background_job": (
            delete_background_job_tool,
            DeleteBackgroundJobParams,
            ServerSideScriptResponse,
            "Delete a Background Job from sys_trigger",
            "raw_pydantic",
        ),
        "execute_background_script": (
            execute_background_script_tool,
            ExecuteBackgroundScriptParams,
            Dict[str, Any],
            "Execute a manual ServiceNow Scripts - Background script",
            "raw_dict",
        ),
        # Fix Script Tools
        "list_fix_scripts": (
            list_fix_scripts_tool,
            ListFixScriptsParams,
            Dict[str, Any],
            "List Fix Scripts from sys_script_fix",
            "raw_dict",
        ),
        "get_fix_script": (
            get_fix_script_tool,
            GetFixScriptParams,
            Dict[str, Any],
            "Get a Fix Script from sys_script_fix",
            "raw_dict",
        ),
        "create_fix_script": (
            create_fix_script_tool,
            CreateFixScriptParams,
            ServerSideScriptResponse,
            "Create a Fix Script in sys_script_fix",
            "raw_pydantic",
        ),
        "update_fix_script": (
            update_fix_script_tool,
            UpdateFixScriptParams,
            ServerSideScriptResponse,
            "Update a Fix Script in sys_script_fix",
            "raw_pydantic",
        ),
        "delete_fix_script": (
            delete_fix_script_tool,
            DeleteFixScriptParams,
            ServerSideScriptResponse,
            "Delete a Fix Script from sys_script_fix",
            "raw_pydantic",
        ),
        # Scoped Application Lifecycle Tools
        "get_scoped_app_lifecycle_metadata": (
            scoped_app_metadata_tools.get_scoped_app_lifecycle_metadata,
            scoped_app_metadata_tools.GetScopedAppLifecycleMetadataParams,
            Dict[str, Any],
            "Describe scoped app lifecycle artifact types, tables, and key fields",
            "raw_dict",
        ),
        "list_scoped_app_artifacts": (
            scoped_app_reader_tools.list_scoped_app_artifacts,
            scoped_app_reader_tools.ListScopedAppArtifactsParams,
            Dict[str, Any],
            "List scoped apps, app menus, modules, application files, or app metadata",
            "raw_dict",
        ),
        "get_scoped_app_artifact": (
            scoped_app_reader_tools.get_scoped_app_artifact,
            scoped_app_reader_tools.GetScopedAppArtifactParams,
            Dict[str, Any],
            "Get a scoped app, app menu, module, application file, or app metadata record",
            "raw_dict",
        ),
        "create_scoped_app_artifact": (
            scoped_app_writer_tools.create_scoped_app_artifact,
            scoped_app_writer_tools.CreateScopedAppArtifactParams,
            Dict[str, Any],
            "Create a scoped app, app menu, module, application file, or app metadata record",
            "raw_dict",
        ),
        "update_scoped_app_artifact": (
            scoped_app_writer_tools.update_scoped_app_artifact,
            scoped_app_writer_tools.UpdateScopedAppArtifactParams,
            Dict[str, Any],
            "Update a scoped app, app menu, module, application file, or app metadata record",
            "raw_dict",
        ),
        "delete_scoped_app_artifact": (
            scoped_app_writer_tools.delete_scoped_app_artifact,
            scoped_app_writer_tools.DeleteScopedAppArtifactParams,
            Dict[str, Any],
            "Delete a scoped app, app menu, module, application file, or app metadata record",
            "raw_dict",
        ),
        "ensure_application_scope": (
            scoped_app_application_tools.ensure_application_scope,
            scoped_app_application_tools.EnsureApplicationScopeParams,
            Dict[str, Any],
            "Resolve or create a ServiceNow application scope in sys_scope",
            "raw_dict",
        ),
        "change_artifact_application_scope": (
            scoped_app_application_tools.change_artifact_application_scope,
            scoped_app_application_tools.ChangeArtifactApplicationScopeParams,
            Dict[str, Any],
            "Move a scoped app artifact to a different ServiceNow application scope",
            "raw_dict",
        ),
        "set_application_update_set": (
            scoped_app_application_tools.set_application_update_set,
            scoped_app_application_tools.SetApplicationUpdateSetParams,
            Dict[str, Any],
            "Bind an update set to an application scope and optionally make it current",
            "raw_dict",
        ),
        "switch_application_scope": (
            scoped_app_application_tools.switch_application_scope,
            scoped_app_application_tools.SwitchApplicationScopeParams,
            Dict[str, Any],
            "Switch the current user's working ServiceNow application scope",
            "raw_dict",
        ),
        # Business Rule Tools
        "list_business_rules": (
            list_business_rules_tool,
            ListBusinessRulesParams,
            Dict[str, Any],
            "List business rules from ServiceNow",
            "raw_dict",
        ),
        "get_business_rule": (
            get_business_rule_tool,
            GetBusinessRuleParams,
            Dict[str, Any],
            "Get a specific business rule from ServiceNow",
            "raw_dict",
        ),
        "create_business_rule": (
            create_business_rule_tool,
            CreateBusinessRuleParams,
            BusinessRuleResponse,
            "Create a new business rule in ServiceNow",
            "raw_pydantic",
        ),
        "update_business_rule": (
            update_business_rule_tool,
            UpdateBusinessRuleParams,
            BusinessRuleResponse,
            "Update an existing business rule in ServiceNow",
            "raw_pydantic",
        ),
        "delete_business_rule": (
            delete_business_rule_tool,
            DeleteBusinessRuleParamsBusinessRule,
            BusinessRuleResponse,
            "Delete a business rule in ServiceNow",
            "raw_pydantic",
        ),
        # UI Action Tools
        "list_ui_actions": (
            list_ui_actions_tool,
            ListUIActionsParams,
            Dict[str, Any],
            "List UI Actions from ServiceNow",
            "raw_dict",
        ),
        "get_ui_action": (
            get_ui_action_tool,
            GetUIActionParams,
            Dict[str, Any],
            "Get a specific UI Action from ServiceNow",
            "raw_dict",
        ),
        "create_ui_action": (
            create_ui_action_tool,
            CreateUIActionParams,
            UIActionResponse,
            "Create a new UI Action in ServiceNow",
            "raw_pydantic",
        ),
        "update_ui_action": (
            update_ui_action_tool,
            UpdateUIActionParams,
            UIActionResponse,
            "Update an existing UI Action in ServiceNow",
            "raw_pydantic",
        ),
        "delete_ui_action": (
            delete_ui_action_tool,
            DeleteUIActionParams,
            UIActionResponse,
            "Delete a UI Action from ServiceNow",
            "raw_pydantic",
        ),
        # UI Policy Tools
        "list_ui_policies": (
            list_ui_policies_tool,
            ListUIPoliciesParams,
            Dict[str, Any],
            "List UI Policies from ServiceNow",
            "raw_dict",
        ),
        "get_ui_policy": (
            get_ui_policy_tool,
            GetUIPolicyParams,
            Dict[str, Any],
            "Get a specific UI Policy from ServiceNow",
            "raw_dict",
        ),
        "create_ui_policy": (
            create_ui_policy_tool,
            CreateUIPolicyParams,
            UIPolicyResponse,
            "Create a new UI Policy in ServiceNow",
            "raw_pydantic",
        ),
        "update_ui_policy": (
            update_ui_policy_tool,
            UpdateUIPolicyParams,
            UIPolicyResponse,
            "Update an existing UI Policy in ServiceNow",
            "raw_pydantic",
        ),
        "delete_ui_policy": (
            delete_ui_policy_tool,
            DeleteUIPolicyParams,
            UIPolicyResponse,
            "Delete a UI Policy from ServiceNow",
            "raw_pydantic",
        ),
        "list_ui_policy_actions": (
            list_ui_policy_actions_tool,
            ListUIPolicyActionsParams,
            Dict[str, Any],
            "List UI Policy actions from ServiceNow",
            "raw_dict",
        ),
        "get_ui_policy_action": (
            get_ui_policy_action_tool,
            GetUIPolicyActionParams,
            Dict[str, Any],
            "Get a specific UI Policy action from ServiceNow",
            "raw_dict",
        ),
        "create_ui_policy_action": (
            create_ui_policy_action_tool,
            CreateUIPolicyActionParams,
            UIPolicyActionResponse,
            "Create a UI Policy action for field behavior",
            "raw_pydantic",
        ),
        "update_ui_policy_action": (
            update_ui_policy_action_tool,
            UpdateUIPolicyActionParams,
            UIPolicyActionResponse,
            "Update a UI Policy action for field behavior",
            "raw_pydantic",
        ),
        "delete_ui_policy_action": (
            delete_ui_policy_action_tool,
            DeleteUIPolicyActionParams,
            UIPolicyActionResponse,
            "Delete a UI Policy action from ServiceNow",
            "raw_pydantic",
        ),
        # Data Policy Tools
        "convert_ui_policy_to_data_policy": (
            convert_ui_policy_to_data_policy_tool,
            ConvertUIPolicyToDataPolicyParams,
            ConvertUIPolicyToDataPolicyResponse,
            "Convert an eligible UI Policy and its actions into a Data Policy and rules",
            "raw_pydantic",
        ),
        "list_data_policies": (
            list_data_policies_tool,
            ListDataPoliciesParams,
            Dict[str, Any],
            "List Data Policies from ServiceNow",
            "raw_dict",
        ),
        "get_data_policy": (
            get_data_policy_tool,
            GetDataPolicyParams,
            Dict[str, Any],
            "Get a specific Data Policy from ServiceNow",
            "raw_dict",
        ),
        "create_data_policy": (
            create_data_policy_tool,
            CreateDataPolicyParams,
            DataPolicyResponse,
            "Create a new Data Policy with optional rules",
            "raw_pydantic",
        ),
        "update_data_policy": (
            update_data_policy_tool,
            UpdateDataPolicyParams,
            DataPolicyResponse,
            "Update an existing Data Policy",
            "raw_pydantic",
        ),
        "delete_data_policy": (
            delete_data_policy_tool,
            DeleteDataPolicyParams,
            DataPolicyResponse,
            "Delete a Data Policy from ServiceNow",
            "raw_pydantic",
        ),
        "list_data_policy_rules": (
            list_data_policy_rules_tool,
            ListDataPolicyRulesParams,
            Dict[str, Any],
            "List Data Policy Rules from ServiceNow",
            "raw_dict",
        ),
        "get_data_policy_rule": (
            get_data_policy_rule_tool,
            GetDataPolicyRuleParams,
            Dict[str, Any],
            "Get a specific Data Policy Rule from ServiceNow",
            "raw_dict",
        ),
        "create_data_policy_rule": (
            create_data_policy_rule_tool,
            CreateDataPolicyRuleParams,
            DataPolicyRuleResponse,
            "Create a Data Policy Rule for mandatory or read-only enforcement",
            "raw_pydantic",
        ),
        "update_data_policy_rule": (
            update_data_policy_rule_tool,
            UpdateDataPolicyRuleParams,
            DataPolicyRuleResponse,
            "Update a Data Policy Rule",
            "raw_pydantic",
        ),
        "delete_data_policy_rule": (
            delete_data_policy_rule_tool,
            DeleteDataPolicyRuleParams,
            DataPolicyRuleResponse,
            "Delete a Data Policy Rule from ServiceNow",
            "raw_pydantic",
        ),
        "set_data_policy_client_enforcement": (
            set_data_policy_client_enforcement_tool,
            SetDataPolicyClientEnforcementParams,
            DataPolicyResponse,
            "Enable or disable Data Policy client-side enforcement as a UI Policy",
            "raw_pydantic",
        ),
        # Knowledge Base Tools
        "create_knowledge_base": (
            create_knowledge_base_tool,
            CreateKnowledgeBaseParams,
            str,  # Expects JSON string
            "Create a new knowledge base in ServiceNow",
            "json_dict",  # Tool returns Pydantic model
        ),
        "list_knowledge_bases": (
            list_knowledge_bases_tool,
            ListKnowledgeBasesParams,
            Dict[str, Any],  # Expects dict
            "List knowledge bases from ServiceNow",
            "raw_dict",  # Tool returns raw dict
        ),
        # Use the passed-in implementations for aliased KB category tools
        "create_category": (
            create_kb_category_tool_impl,  # Use passed function
            CreateKBCategoryParams,
            str,  # Expects JSON string
            "Create a new category in a knowledge base",
            "json_dict",  # Tool returns Pydantic model
        ),
        "create_article": (
            create_article_tool,
            CreateArticleParams,
            str,  # Expects JSON string
            "Create a new knowledge article",
            "json_dict",  # Tool returns Pydantic model
        ),
        "update_article": (
            update_article_tool,
            UpdateArticleParams,
            str,  # Expects JSON string
            "Update an existing knowledge article",
            "json_dict",  # Tool returns Pydantic model
        ),
        "publish_article": (
            publish_article_tool,
            PublishArticleParams,
            str,  # Expects JSON string
            "Publish a knowledge article",
            "json_dict",  # Tool returns Pydantic model
        ),
        "list_articles": (
            list_articles_tool,
            ListArticlesParams,
            Dict[str, Any],  # Expects dict
            "List knowledge articles",
            "raw_dict",  # Tool returns raw dict
        ),
        "get_article": (
            get_article_tool,
            GetArticleParams,
            Dict[str, Any],  # Expects dict
            "Get a specific knowledge article by ID",
            "raw_dict",  # Tool returns raw dict
        ),
        # Use the passed-in implementations for aliased KB category tools
        "list_categories": (
            list_kb_categories_tool_impl,  # Use passed function
            ListKBCategoriesParams,
            Dict[str, Any],  # Expects dict
            "List categories in a knowledge base",
            "raw_dict",  # Tool returns raw dict
        ),
        # User Management Tools
        "create_user": (
            create_user_tool,
            CreateUserParams,
            Dict[str, Any],  # Expects dict
            "Create a new user in ServiceNow",
            "raw_dict",  # Tool returns raw dict
        ),
        "update_user": (
            update_user_tool,
            UpdateUserParams,
            Dict[str, Any],  # Expects dict
            "Update an existing user in ServiceNow",
            "raw_dict",
        ),
        "get_user": (
            get_user_tool,
            GetUserParams,
            Dict[str, Any],  # Expects dict
            "Get a specific user in ServiceNow",
            "raw_dict",
        ),
        "list_users": (
            list_users_tool,
            ListUsersParams,
            Dict[str, Any],  # Expects dict
            "List users in ServiceNow",
            "raw_dict",
        ),
        "create_group": (
            create_group_tool,
            CreateGroupParams,
            Dict[str, Any],  # Expects dict
            "Create a new group in ServiceNow",
            "raw_dict",
        ),
        "update_group": (
            update_group_tool,
            UpdateGroupParams,
            Dict[str, Any],  # Expects dict
            "Update an existing group in ServiceNow",
            "raw_dict",
        ),
        "add_group_members": (
            add_group_members_tool,
            AddGroupMembersParams,
            Dict[str, Any],  # Expects dict
            "Add members to an existing group in ServiceNow",
            "raw_dict",
        ),
        "remove_group_members": (
            remove_group_members_tool,
            RemoveGroupMembersParams,
            Dict[str, Any],  # Expects dict
            "Remove members from an existing group in ServiceNow",
            "raw_dict",
        ),
        "list_groups": (
            list_groups_tool,
            ListGroupsParams,
            Dict[str, Any],  # Expects dict
            "List groups from ServiceNow with optional filtering",
            "raw_dict",
        ),
        # Company Management Tools
        "list_companies": (
            list_companies_tool,
            ListCompaniesParams,
            Dict[str, Any],
            "List companies from ServiceNow with optional filtering",
            "raw_dict",
        ),
        "get_company": (
            get_company_tool,
            GetCompanyParams,
            Dict[str, Any],
            "Get a ServiceNow company by sys_id or exact name",
            "raw_dict",
        ),
        "create_company": (
            create_company_tool,
            CreateCompanyParams,
            Dict[str, Any],
            "Create a company in ServiceNow",
            "raw_dict",
        ),
        "update_company": (
            update_company_tool,
            UpdateCompanyParams,
            Dict[str, Any],
            "Update an existing company in ServiceNow",
            "raw_dict",
        ),
        "delete_company": (
            delete_company_tool,
            DeleteCompanyParams,
            Dict[str, Any],
            "Delete a company from ServiceNow",
            "raw_dict",
        ),
        # Location Management Tools
        "list_locations": (
            list_locations_tool,
            ListLocationsParams,
            Dict[str, Any],
            "List locations from ServiceNow with optional filtering",
            "raw_dict",
        ),
        "get_location": (
            get_location_tool,
            GetLocationParams,
            Dict[str, Any],
            "Get a ServiceNow location by sys_id, exact name, or exact full name",
            "raw_dict",
        ),
        "create_location": (
            create_location_tool,
            CreateLocationParams,
            Dict[str, Any],
            "Create a location in ServiceNow",
            "raw_dict",
        ),
        "update_location": (
            update_location_tool,
            UpdateLocationParams,
            Dict[str, Any],
            "Update an existing location in ServiceNow",
            "raw_dict",
        ),
        "delete_location": (
            delete_location_tool,
            DeleteLocationParams,
            Dict[str, Any],
            "Delete a location from ServiceNow",
            "raw_dict",
        ),
        # Department Management Tools
        "list_departments": (
            list_departments_tool,
            ListDepartmentsParams,
            Dict[str, Any],
            "List departments from ServiceNow with optional filtering",
            "raw_dict",
        ),
        "get_department": (
            get_department_tool,
            GetDepartmentParams,
            Dict[str, Any],
            "Get a ServiceNow department by sys_id, exact id, or exact name",
            "raw_dict",
        ),
        "create_department": (
            create_department_tool,
            CreateDepartmentParams,
            Dict[str, Any],
            "Create a department in ServiceNow",
            "raw_dict",
        ),
        "update_department": (
            update_department_tool,
            UpdateDepartmentParams,
            Dict[str, Any],
            "Update an existing department in ServiceNow",
            "raw_dict",
        ),
        "delete_department": (
            delete_department_tool,
            DeleteDepartmentParams,
            Dict[str, Any],
            "Delete a department from ServiceNow",
            "raw_dict",
        ),
        # Cost Center Management Tools
        "list_cost_centers": (
            list_cost_centers_tool,
            ListCostCentersParams,
            Dict[str, Any],
            "List cost centers from ServiceNow with optional filtering",
            "raw_dict",
        ),
        "get_cost_center": (
            get_cost_center_tool,
            GetCostCenterParams,
            Dict[str, Any],
            "Get a ServiceNow cost center by sys_id, exact code, or exact name",
            "raw_dict",
        ),
        "create_cost_center": (
            create_cost_center_tool,
            CreateCostCenterParams,
            Dict[str, Any],
            "Create a cost center in ServiceNow",
            "raw_dict",
        ),
        "update_cost_center": (
            update_cost_center_tool,
            UpdateCostCenterParams,
            Dict[str, Any],
            "Update an existing cost center in ServiceNow",
            "raw_dict",
        ),
        "delete_cost_center": (
            delete_cost_center_tool,
            DeleteCostCenterParams,
            Dict[str, Any],
            "Delete a cost center from ServiceNow",
            "raw_dict",
        ),
        # Asset Management Tools
        "get_asset_management_metadata": (
            asset_management_tools.get_asset_management_metadata,
            asset_management_tools.AssetManagementMetadataParams,
            Dict[str, Any],
            "Describe asset management classes, tables, states, and lifecycle actions",
            "raw_dict",
        ),
        "list_assets": (
            asset_management_tools.list_assets,
            asset_management_tools.ListAssetsParams,
            Dict[str, Any],
            "List hardware, software, consumable, facility, or base asset records with filters "
            "for stockroom, assigned user, company, location, department, and cost center",
            "raw_dict",
        ),
        "get_asset": (
            asset_management_tools.get_asset,
            asset_management_tools.GetAssetParams,
            Dict[str, Any],
            "Get a ServiceNow asset by sys_id, tag, serial number, or name",
            "raw_dict",
        ),
        "create_hardware_asset": (
            asset_management_tools.create_hardware_asset,
            asset_management_tools.CreateHardwareAssetParams,
            Dict[str, Any],
            "Create a hardware asset in alm_hardware with references for model, stockroom, "
            "assigned user, company, location, department, and cost center",
            "raw_dict",
        ),
        "create_software_asset": (
            asset_management_tools.create_software_asset,
            asset_management_tools.CreateSoftwareAssetParams,
            Dict[str, Any],
            "Create a software/license asset in alm_license with references for model, assigned "
            "user, company, location, department, and cost center",
            "raw_dict",
        ),
        "update_asset": (
            asset_management_tools.update_asset,
            asset_management_tools.UpdateAssetParams,
            Dict[str, Any],
            "Update a ServiceNow asset record including company, location, department, cost "
            "center, stockroom, model, and assigned user references",
            "raw_dict",
        ),
        "delete_asset": (
            asset_management_tools.delete_asset,
            asset_management_tools.DeleteAssetParams,
            Dict[str, Any],
            "Delete a ServiceNow asset record",
            "raw_dict",
        ),
        "perform_asset_lifecycle_action": (
            asset_management_tools.perform_asset_lifecycle_action,
            asset_management_tools.AssetLifecycleActionParams,
            Dict[str, Any],
            "Perform assignment, reservation, state, retirement, transfer, or consume actions",
            "raw_dict",
        ),
        "assign_asset": (
            asset_management_tools.assign_asset,
            asset_management_tools.AssignAssetParams,
            Dict[str, Any],
            "Assign an asset to a user",
            "raw_dict",
        ),
        "return_asset": (
            asset_management_tools.return_asset,
            asset_management_tools.ReturnAssetParams,
            Dict[str, Any],
            "Return an asset to stock",
            "raw_dict",
        ),
        "reserve_asset": (
            asset_management_tools.reserve_asset,
            asset_management_tools.ReserveAssetParams,
            Dict[str, Any],
            "Reserve an asset for a user",
            "raw_dict",
        ),
        "transfer_asset": (
            asset_management_tools.transfer_asset,
            asset_management_tools.TransferAssetParams,
            Dict[str, Any],
            "Transfer an asset between stockrooms",
            "raw_dict",
        ),
        "list_stockrooms": (
            asset_management_tools.list_stockrooms,
            asset_management_tools.ListStockroomsParams,
            Dict[str, Any],
            "List ServiceNow asset stockrooms",
            "raw_dict",
        ),
        "get_stockroom": (
            asset_management_tools.get_stockroom,
            asset_management_tools.GetStockroomParams,
            Dict[str, Any],
            "Get an asset stockroom by sys_id, name, or code",
            "raw_dict",
        ),
        "create_stockroom": (
            asset_management_tools.create_stockroom,
            asset_management_tools.CreateStockroomParams,
            Dict[str, Any],
            "Create an asset stockroom",
            "raw_dict",
        ),
        "update_stockroom": (
            asset_management_tools.update_stockroom,
            asset_management_tools.UpdateStockroomParams,
            Dict[str, Any],
            "Update an asset stockroom",
            "raw_dict",
        ),
        "delete_stockroom": (
            asset_management_tools.delete_stockroom,
            asset_management_tools.DeleteStockroomParams,
            Dict[str, Any],
            "Delete an asset stockroom",
            "raw_dict",
        ),
        "list_asset_models": (
            asset_management_tools.list_asset_models,
            asset_management_tools.ListAssetModelsParams,
            Dict[str, Any],
            "List asset models from cmdb_model",
            "raw_dict",
        ),
        "create_asset_model": (
            asset_management_tools.create_asset_model,
            asset_management_tools.CreateAssetModelParams,
            Dict[str, Any],
            "Create an asset model in cmdb_model",
            "raw_dict",
        ),
        "update_asset_model": (
            asset_management_tools.update_asset_model,
            asset_management_tools.UpdateAssetModelParams,
            Dict[str, Any],
            "Update an asset model in cmdb_model",
            "raw_dict",
        ),
        "asset_create": (
            asset_management_tools.asset_create,
            asset_management_tools.AssetCreateParams,
            Dict[str, Any],
            "Create a ServiceNow asset with duplicate checks, dry-run, structured output, and "
            "references for model, stockroom, location, department, cost center, user, vendor, "
            "contract, and CI",
            "raw_dict",
        ),
        "asset_create_hardware": (
            asset_management_tools.asset_create_hardware,
            asset_management_tools.AssetCreateParams,
            Dict[str, Any],
            "Create a hardware asset in alm_hardware with serial/asset tag protection and "
            "detailed references including location, department, cost center, assigned user, "
            "vendor, model, contract, and CI",
            "raw_dict",
        ),
        "asset_create_consumable": (
            asset_management_tools.asset_create_consumable,
            asset_management_tools.AssetCreateParams,
            Dict[str, Any],
            "Create a consumable asset in alm_consumable with quantity validation",
            "raw_dict",
        ),
        "asset_create_software_license": (
            asset_management_tools.asset_create_software_license,
            asset_management_tools.AssetCreateParams,
            Dict[str, Any],
            "Create a software license asset in alm_license with plugin-aware table checks",
            "raw_dict",
        ),
        "asset_get": (
            asset_management_tools.asset_get,
            asset_management_tools.AssetUpdateParams,
            Dict[str, Any],
            "Get an asset by sys_id, asset tag, serial number, display name, or exact query",
            "raw_dict",
        ),
        "asset_update": (
            asset_management_tools.asset_update,
            asset_management_tools.AssetUpdateParams,
            Dict[str, Any],
            "Audit-safe asset update for core fields and references including model, stockroom, "
            "location, department, cost center, assigned user, reserved user, vendor, contract, "
            "and CI",
            "raw_dict",
        ),
        "asset_search": (
            asset_management_tools.asset_search,
            asset_management_tools.AssetSearchParams,
            Dict[str, Any],
            "Search assets with sanitized filters for state, model, stockroom, assignment, "
            "location, department, cost center, vendor, cost, warranty, and lease dates",
            "raw_dict",
        ),
        "get_allowed_asset_states": (
            asset_management_tools.get_allowed_asset_states,
            asset_management_tools.AssetManagementMetadataParams,
            Dict[str, Any],
            "List supported asset lifecycle states",
            "raw_dict",
        ),
        "get_allowed_asset_substates": (
            asset_management_tools.get_allowed_asset_substates,
            asset_management_tools.AssetSubstateParams,
            Dict[str, Any],
            "List supported substates for an asset state",
            "raw_dict",
        ),
        "validate_asset_state_transition": (
            asset_management_tools.validate_asset_state_transition,
            asset_management_tools.AssetStateParams,
            Dict[str, Any],
            "Validate an asset state/substate transition without writing to ServiceNow",
            "raw_dict",
        ),
        "asset_update_state": (
            asset_management_tools.asset_update_state,
            asset_management_tools.AssetStateParams,
            Dict[str, Any],
            "Update an asset lifecycle state after reading and validating the current record",
            "raw_dict",
        ),
        "mark_asset_available": (
            asset_management_tools.mark_asset_available,
            asset_management_tools.AssetStateParams,
            Dict[str, Any],
            "Move an asset to In stock / Available",
            "raw_dict",
        ),
        "mark_asset_reserved": (
            asset_management_tools.mark_asset_reserved,
            asset_management_tools.AssetStateParams,
            Dict[str, Any],
            "Move an asset to In stock / Reserved",
            "raw_dict",
        ),
        "mark_asset_retired": (
            asset_management_tools.mark_asset_retired,
            asset_management_tools.AssetStateParams,
            Dict[str, Any],
            "Move an asset to Retired with a valid retired substate",
            "raw_dict",
        ),
        "mark_asset_lost": (
            asset_management_tools.mark_asset_lost,
            asset_management_tools.AssetStateParams,
            Dict[str, Any],
            "Move an asset to Missing / Lost",
            "raw_dict",
        ),
        "mark_asset_stolen": (
            asset_management_tools.mark_asset_stolen,
            asset_management_tools.AssetStateParams,
            Dict[str, Any],
            "Move an asset to Missing / Stolen",
            "raw_dict",
        ),
        "asset_assign_to_user": (
            asset_management_tools.asset_assign_to_user,
            asset_management_tools.AssetAssignmentParams,
            Dict[str, Any],
            "Assign an asset to a user and move it to In use with audit notes",
            "raw_dict",
        ),
        "asset_return_from_user": (
            asset_management_tools.asset_return_from_user,
            asset_management_tools.AssetReturnParams,
            Dict[str, Any],
            "Return an assigned asset to stockroom with a validated stock substate",
            "raw_dict",
        ),
        "asset_adjust_consumable_quantity": (
            asset_management_tools.asset_adjust_consumable_quantity,
            asset_management_tools.ConsumableQuantityParams,
            Dict[str, Any],
            "Adjust consumable quantity without allowing negative inventory",
            "raw_dict",
        ),
        "asset_transfer_create": (
            asset_management_tools.asset_transfer_create,
            asset_management_tools.AssetTransferOrderParams,
            Dict[str, Any],
            "Create an asset transfer order using alm_transfer_order",
            "raw_dict",
        ),
        "asset_transfer_line_create": (
            asset_management_tools.asset_transfer_line_create,
            asset_management_tools.AssetTransferLineParams,
            Dict[str, Any],
            "Create an asset transfer order line using alm_transfer_order_line",
            "raw_dict",
        ),
        "asset_retire": (
            asset_management_tools.asset_retire,
            asset_management_tools.AssetRetireParams,
            Dict[str, Any],
            "Retire an asset without deleting it; disposal requires data wipe confirmation",
            "raw_dict",
        ),
        "asset_stockroom_create": (
            asset_management_tools.asset_stockroom_create,
            asset_management_tools.CreateStockroomParams,
            Dict[str, Any],
            "Create a stockroom with reference validation",
            "raw_dict",
        ),
        "asset_stockroom_inventory": (
            asset_management_tools.asset_stockroom_inventory,
            asset_management_tools.AssetSearchParams,
            Dict[str, Any],
            "Search stockroom inventory with asset report filters",
            "raw_dict",
        ),
        "asset_report_inventory": (
            asset_management_tools.asset_report_inventory,
            asset_management_tools.AssetReportParams,
            Dict[str, Any],
            "Generate a compact asset inventory report with optional records",
            "raw_dict",
        ),
        "asset_repair_create": (
            asset_management_tools.asset_repair_create,
            asset_management_tools.SafeMutationFields,
            Dict[str, Any],
            "Plugin-aware repair creation capability check for asset_task",
            "raw_dict",
        ),
        "asset_repair_close": (
            asset_management_tools.asset_repair_close,
            asset_management_tools.SafeMutationFields,
            Dict[str, Any],
            "Plugin-aware repair close capability check for asset_task",
            "raw_dict",
        ),
        "asset_sam_create_entitlement": (
            asset_management_tools.asset_sam_create_entitlement,
            asset_management_tools.SafeMutationFields,
            Dict[str, Any],
            "Plugin-aware Software Asset Management entitlement capability check",
            "raw_dict",
        ),
        # Security Administration Tools
        "list_acls": (
            list_acls_tool,
            ListACLsParams,
            Dict[str, Any],
            "List access control rules from ServiceNow",
            "raw_dict",
        ),
        "get_acl": (
            get_acl_tool,
            GetACLParams,
            Dict[str, Any],
            "Get a specific access control rule from ServiceNow",
            "raw_dict",
        ),
        "create_acl": (
            create_acl_tool,
            CreateACLParams,
            Dict[str, Any],
            "Create a new access control rule in ServiceNow",
            "raw_dict",
        ),
        "update_acl": (
            update_acl_tool,
            UpdateACLParams,
            Dict[str, Any],
            "Update an existing access control rule in ServiceNow",
            "raw_dict",
        ),
        "test_acl_access": (
            test_acl_access_tool,
            TestACLAccessParams,
            Dict[str, Any],
            (
                "Test a single ACL against a user, group, or role with best-effort "
                "evaluation; does not evaluate the full ACL set"
            ),
            "raw_dict",
        ),
        "validate_acl": (
            validate_acl_tool,
            ValidateACLParams,
            Dict[str, Any],
            "Validate an ACL for empty rules, bad references, and risky constraints",
            "raw_dict",
        ),
        "list_roles": (
            list_roles_tool,
            ListRolesParams,
            Dict[str, Any],
            "List roles from ServiceNow",
            "raw_dict",
        ),
        "get_role": (
            get_role_tool,
            GetRoleParams,
            Dict[str, Any],
            "Get a specific role from ServiceNow",
            "raw_dict",
        ),
        "create_role": (
            create_role_tool,
            CreateRoleParams,
            Dict[str, Any],
            "Create a new role in ServiceNow",
            "raw_dict",
        ),
        "update_role": (
            update_role_tool,
            UpdateRoleParams,
            Dict[str, Any],
            "Update an existing role in ServiceNow",
            "raw_dict",
        ),
        "validate_role": (
            validate_role_tool,
            ValidateRoleParams,
            Dict[str, Any],
            "Validate a role and its inheritance mappings",
            "raw_dict",
        ),
        "list_role_mappings": (
            list_role_mappings_tool,
            ListRoleMappingsParams,
            Dict[str, Any],
            "List user, group, ACL, or containment role mappings from ServiceNow",
            "raw_dict",
        ),
        "create_role_mapping": (
            create_role_mapping_tool,
            CreateRoleMappingParams,
            Dict[str, Any],
            "Create a user-role, group-role, ACL-role, or role-containment mapping",
            "raw_dict",
        ),
        "update_role_mapping": (
            update_role_mapping_tool,
            UpdateRoleMappingParams,
            Dict[str, Any],
            "Update an existing role mapping in ServiceNow",
            "raw_dict",
        ),
        "validate_role_mapping": (
            validate_role_mapping_tool,
            ValidateRoleMappingParams,
            Dict[str, Any],
            "Validate a role mapping for missing references, duplicates, and cycles",
            "raw_dict",
        ),
        "validate_security_constraints": (
            validate_security_constraints_tool,
            ValidateSecurityConstraintsParams,
            Dict[str, Any],
            (
                "Review the configuration of matching ACLs for a table or field; "
                "does not by itself determine effective access"
            ),
            "raw_dict",
        ),
        # Story Management Tools
        "create_story": (
            create_story_tool,
            CreateStoryParams,
            str,
            "Create a new story in ServiceNow",
            "str",
        ),
        "update_story": (
            update_story_tool,
            UpdateStoryParams,
            str,
            "Update an existing story in ServiceNow",
            "str",
        ),
        "list_stories": (
            list_stories_tool,
            ListStoriesParams,
            str,  # Expects JSON string
            "List stories from ServiceNow",
            "json",  # Tool returns list/dict
        ),
        "list_story_dependencies": (
            list_story_dependencies_tool,
            ListStoryDependenciesParams,
            str,  # Expects JSON string
            "List story dependencies from ServiceNow",
            "json",  # Tool returns list/dict
        ),
        "create_story_dependency": (
            create_story_dependency_tool,
            CreateStoryDependencyParams,
            str,
            "Create a dependency between two stories in ServiceNow",
            "str",
        ),
        "delete_story_dependency": (
            delete_story_dependency_tool,
            DeleteStoryDependencyParams,
            str,
            "Delete a story dependency in ServiceNow",
            "str",
        ),
        # Epic Management Tools
        "create_epic": (
            create_epic_tool,
            CreateEpicParams,
            str,
            "Create a new epic in ServiceNow",
            "str",
        ),
        "update_epic": (
            update_epic_tool,
            UpdateEpicParams,
            str,
            "Update an existing epic in ServiceNow",
            "str",
        ),
        "list_epics": (
            list_epics_tool,
            ListEpicsParams,
            str,  # Expects JSON string
            "List epics from ServiceNow",
            "json",  # Tool returns list/dict
        ),
        # Scrum Task Management Tools
        "create_scrum_task": (
            create_scrum_task_tool,
            CreateScrumTaskParams,
            str,
            "Create a new scrum task in ServiceNow",
            "str",
        ),
        "update_scrum_task": (
            update_scrum_task_tool,
            UpdateScrumTaskParams,
            str,
            "Update an existing scrum task in ServiceNow",
            "str",
        ),
        "list_scrum_tasks": (
            list_scrum_tasks_tool,
            ListScrumTasksParams,
            str,  # Expects JSON string
            "List scrum tasks from ServiceNow",
            "json",  # Tool returns list/dict
        ),
        # Project Management Tools
        "create_project": (
            create_project_tool,
            CreateProjectParams,
            str,
            "Create a new project in ServiceNow",
            "str",
        ),
        "update_project": (
            update_project_tool,
            UpdateProjectParams,
            str,
            "Update an existing project in ServiceNow",
            "str",
        ),
        "list_projects": (
            list_projects_tool,
            ListProjectsParams,
            str,  # Expects JSON string
            "List projects from ServiceNow",
            "json",  # Tool returns list/dict
        ),
    }
    tool_definitions.update(
        {
            # Table and Field Schema Tools
            "list_table_definitions": (
                table_schema_tools.list_table_definitions,
                table_schema_tools.ListTableDefinitionsParams,
                Dict[str, Any],
                "List ServiceNow table definitions from sys_db_object with labels and metadata",
                "raw_dict",
            ),
            "get_table_definition": (
                table_schema_tools.get_table_definition,
                table_schema_tools.GetTableDefinitionParams,
                Dict[str, Any],
                "Get a ServiceNow table definition and its sys_dictionary fields",
                "raw_dict",
            ),
            "create_table_definition": (
                table_schema_tools.create_table_definition,
                table_schema_tools.CreateTableDefinitionParams,
                Dict[str, Any],
                "Create a ServiceNow table definition in sys_db_object",
                "raw_dict",
            ),
            "update_table_definition": (
                table_schema_tools.update_table_definition,
                table_schema_tools.UpdateTableDefinitionParams,
                Dict[str, Any],
                "Update a ServiceNow table definition in sys_db_object",
                "raw_dict",
            ),
            "list_field_definitions": (
                table_schema_tools.list_field_definitions,
                table_schema_tools.ListFieldDefinitionsParams,
                Dict[str, Any],
                (
                    "List ServiceNow field definitions from sys_dictionary with type, "
                    "label, and metadata"
                ),
                "raw_dict",
            ),
            "get_field_definition": (
                table_schema_tools.get_field_definition,
                table_schema_tools.GetFieldDefinitionParams,
                Dict[str, Any],
                "Get a ServiceNow field definition from sys_dictionary",
                "raw_dict",
            ),
            "create_field_definition": (
                table_schema_tools.create_field_definition,
                table_schema_tools.CreateFieldDefinitionParams,
                Dict[str, Any],
                "Create a ServiceNow field definition in sys_dictionary",
                "raw_dict",
            ),
            "update_field_definition": (
                table_schema_tools.update_field_definition,
                table_schema_tools.UpdateFieldDefinitionParams,
                Dict[str, Any],
                "Update a ServiceNow field definition in sys_dictionary",
                "raw_dict",
            ),
            # Approval Management Tools
            "get_approval_management_metadata": (
                approval_tools.get_approval_management_metadata,
                approval_tools.GetApprovalManagementMetadataParams,
                Dict[str, Any],
                "Describe approval-management tables, tools, table overrides, and field examples",
                "raw_dict",
            ),
            "list_approval_records": (
                approval_tools.list_approval_records,
                approval_tools.ListApprovalRecordsParams,
                Dict[str, Any],
                "List approval records from sysapproval_approver",
                "raw_dict",
            ),
            "get_approval_record": (
                approval_tools.get_approval_record,
                approval_tools.GetApprovalRecordParams,
                Dict[str, Any],
                "Get a single approval record",
                "raw_dict",
            ),
            "create_approval_record": (
                approval_tools.create_approval_record,
                approval_tools.CreateApprovalRecordParams,
                Dict[str, Any],
                "Create an approval record with support for all sysapproval_approver fields",
                "raw_dict",
            ),
            "update_approval_record": (
                approval_tools.update_approval_record,
                approval_tools.UpdateApprovalRecordParams,
                Dict[str, Any],
                "Update an approval record with support for all sysapproval_approver fields",
                "raw_dict",
            ),
            "delete_approval_record": (
                approval_tools.delete_approval_record,
                approval_tools.DeleteApprovalRecordParams,
                Dict[str, Any],
                "Delete an approval record",
                "raw_dict",
            ),
            "approve_approval": (
                approval_tools.approve_approval,
                approval_tools.ApprovalActionParams,
                Dict[str, Any],
                "Approve an approval record",
                "raw_dict",
            ),
            "reject_approval": (
                approval_tools.reject_approval,
                approval_tools.ApprovalActionParams,
                Dict[str, Any],
                "Reject an approval record",
                "raw_dict",
            ),
            "cancel_approval": (
                approval_tools.cancel_approval,
                approval_tools.ApprovalActionParams,
                Dict[str, Any],
                "Cancel an approval record",
                "raw_dict",
            ),
            "list_approval_definitions": (
                approval_tools.list_approval_definitions,
                approval_tools.ListApprovalDefinitionsParams,
                Dict[str, Any],
                "List approval definition artifacts with table override support",
                "raw_dict",
            ),
            "get_approval_definition": (
                approval_tools.get_approval_definition,
                approval_tools.GetApprovalDefinitionParams,
                Dict[str, Any],
                "Get an approval definition artifact",
                "raw_dict",
            ),
            "create_approval_definition": (
                approval_tools.create_approval_definition,
                approval_tools.CreateApprovalDefinitionParams,
                Dict[str, Any],
                "Create an approval definition with support for all fields",
                "raw_dict",
            ),
            "update_approval_definition": (
                approval_tools.update_approval_definition,
                approval_tools.UpdateApprovalDefinitionParams,
                Dict[str, Any],
                "Update an approval definition with support for all fields",
                "raw_dict",
            ),
            "delete_approval_definition": (
                approval_tools.delete_approval_definition,
                approval_tools.DeleteApprovalDefinitionParams,
                Dict[str, Any],
                "Delete an approval definition",
                "raw_dict",
            ),
            "list_approval_rules": (
                approval_tools.list_approval_rules,
                approval_tools.ListApprovalRulesParams,
                Dict[str, Any],
                "List approval rules with table override support",
                "raw_dict",
            ),
            "get_approval_rule": (
                approval_tools.get_approval_rule,
                approval_tools.GetApprovalRuleParams,
                Dict[str, Any],
                "Get an approval rule",
                "raw_dict",
            ),
            "create_approval_rule": (
                approval_tools.create_approval_rule,
                approval_tools.CreateApprovalRuleParams,
                Dict[str, Any],
                "Create an approval rule with support for all fields",
                "raw_dict",
            ),
            "update_approval_rule": (
                approval_tools.update_approval_rule,
                approval_tools.UpdateApprovalRuleParams,
                Dict[str, Any],
                "Update an approval rule with support for all fields",
                "raw_dict",
            ),
            "delete_approval_rule": (
                approval_tools.delete_approval_rule,
                approval_tools.DeleteApprovalRuleParams,
                Dict[str, Any],
                "Delete an approval rule",
                "raw_dict",
            ),
            "list_approver_groups": (
                approval_tools.list_approver_groups,
                approval_tools.ListApproverGroupsParams,
                Dict[str, Any],
                "List approver group records",
                "raw_dict",
            ),
            "get_approver_group": (
                approval_tools.get_approver_group,
                approval_tools.GetApproverGroupParams,
                Dict[str, Any],
                "Get an approver group record",
                "raw_dict",
            ),
            "create_approver_group": (
                approval_tools.create_approver_group,
                approval_tools.CreateApproverGroupParams,
                Dict[str, Any],
                "Create an approver group record with support for all fields",
                "raw_dict",
            ),
            "update_approver_group": (
                approval_tools.update_approver_group,
                approval_tools.UpdateApproverGroupParams,
                Dict[str, Any],
                "Update an approver group record with support for all fields",
                "raw_dict",
            ),
            "delete_approver_group": (
                approval_tools.delete_approver_group,
                approval_tools.DeleteApproverGroupParams,
                Dict[str, Any],
                "Delete an approver group record",
                "raw_dict",
            ),
            "list_approval_delegations": (
                approval_tools.list_approval_delegations,
                approval_tools.ListApprovalDelegationsParams,
                Dict[str, Any],
                "List approval delegations",
                "raw_dict",
            ),
            "get_approval_delegation": (
                approval_tools.get_approval_delegation,
                approval_tools.GetApprovalDelegationParams,
                Dict[str, Any],
                "Get an approval delegation",
                "raw_dict",
            ),
            "create_approval_delegation": (
                approval_tools.create_approval_delegation,
                approval_tools.CreateApprovalDelegationParams,
                Dict[str, Any],
                "Create an approval delegation with support for all sys_user_delegate fields",
                "raw_dict",
            ),
            "update_approval_delegation": (
                approval_tools.update_approval_delegation,
                approval_tools.UpdateApprovalDelegationParams,
                Dict[str, Any],
                "Update an approval delegation with support for all sys_user_delegate fields",
                "raw_dict",
            ),
            "delete_approval_delegation": (
                approval_tools.delete_approval_delegation,
                approval_tools.DeleteApprovalDelegationParams,
                Dict[str, Any],
                "Delete an approval delegation",
                "raw_dict",
            ),
            "list_approval_history": (
                approval_tools.list_approval_history,
                approval_tools.ListApprovalHistoryParams,
                Dict[str, Any],
                "List approval history from sysapproval_approver",
                "raw_dict",
            ),
        }
    )
    asset_aliases: Dict[str, ToolDefinition] = {
        # Foundation aliases
        "search_asset_models": (
            asset_management_tools.list_asset_models,
            asset_management_tools.ListAssetModelsParams,
            Dict[str, Any],
            "Search asset models from cmdb_model",
            "raw_dict",
        ),
        "search_stockrooms": (
            asset_management_tools.list_stockrooms,
            asset_management_tools.ListStockroomsParams,
            Dict[str, Any],
            "Search asset stockrooms",
            "raw_dict",
        ),
        # Core CRUD/search aliases
        "create_asset": (
            asset_management_tools.asset_create,
            asset_management_tools.AssetCreateParams,
            Dict[str, Any],
            "Create a generic, hardware, consumable, or software license asset safely",
            "raw_dict",
        ),
        "create_consumable_asset": (
            asset_management_tools.asset_create_consumable,
            asset_management_tools.AssetCreateParams,
            Dict[str, Any],
            "Create a consumable asset with quantity validation",
            "raw_dict",
        ),
        "create_software_license_asset": (
            asset_management_tools.asset_create_software_license,
            asset_management_tools.AssetCreateParams,
            Dict[str, Any],
            "Create a software license asset",
            "raw_dict",
        ),
        "search_assets": (
            asset_management_tools.asset_search,
            asset_management_tools.AssetSearchParams,
            Dict[str, Any],
            "Search assets with sanitized filters",
            "raw_dict",
        ),
        "upsert_asset": (
            asset_management_tools.asset_create,
            asset_management_tools.AssetCreateParams,
            Dict[str, Any],
            "Create an asset after stable-key duplicate detection",
            "raw_dict",
        ),
        "detect_duplicate_assets": (
            asset_management_tools.asset_search,
            asset_management_tools.AssetSearchParams,
            Dict[str, Any],
            "Search for possible duplicate assets",
            "raw_dict",
        ),
        # Lifecycle aliases
        "mark_asset_on_order": (
            asset_management_tools.mark_asset_on_order,
            asset_management_tools.AssetStateParams,
            Dict[str, Any],
            "Mark an asset as On order",
            "raw_dict",
        ),
        "mark_asset_in_use": (
            asset_management_tools.mark_asset_in_use,
            asset_management_tools.AssetStateParams,
            Dict[str, Any],
            "Mark an asset as In use",
            "raw_dict",
        ),
        "mark_asset_in_transit": (
            asset_management_tools.mark_asset_in_transit,
            asset_management_tools.AssetStateParams,
            Dict[str, Any],
            "Mark an asset as In transit",
            "raw_dict",
        ),
        "mark_asset_in_maintenance": (
            asset_management_tools.mark_asset_in_maintenance,
            asset_management_tools.AssetStateParams,
            Dict[str, Any],
            "Mark an asset as In maintenance",
            "raw_dict",
        ),
        "mark_asset_missing": (
            asset_management_tools.mark_asset_missing,
            asset_management_tools.AssetStateParams,
            Dict[str, Any],
            "Mark an asset as Missing",
            "raw_dict",
        ),
        "mark_asset_quarantined": (
            asset_management_tools.mark_asset_quarantined,
            asset_management_tools.AssetStateParams,
            Dict[str, Any],
            "Mark an asset as quarantined",
            "raw_dict",
        ),
        "mark_asset_legal_hold": (
            asset_management_tools.mark_asset_legal_hold,
            asset_management_tools.AssetStateParams,
            Dict[str, Any],
            "Place an asset on legal hold",
            "raw_dict",
        ),
        "mark_asset_pending_repair": (
            asset_management_tools.mark_asset_pending_repair,
            asset_management_tools.AssetStateParams,
            Dict[str, Any],
            "Mark an asset pending repair",
            "raw_dict",
        ),
        "mark_asset_pending_disposal": (
            asset_management_tools.mark_asset_pending_disposal,
            asset_management_tools.AssetStateParams,
            Dict[str, Any],
            "Mark an asset pending disposal",
            "raw_dict",
        ),
        "mark_asset_pending_transfer": (
            asset_management_tools.mark_asset_pending_transfer,
            asset_management_tools.AssetStateParams,
            Dict[str, Any],
            "Mark an asset pending transfer",
            "raw_dict",
        ),
        # Inventory and assignment aliases
        "receive_asset_into_stockroom": (
            asset_management_tools.asset_return_from_user,
            asset_management_tools.AssetReturnParams,
            Dict[str, Any],
            "Receive an asset into a stockroom",
            "raw_dict",
        ),
        "reserve_asset": (
            asset_management_tools.reserve_asset,
            asset_management_tools.ReserveAssetParams,
            Dict[str, Any],
            "Reserve an asset for a user",
            "raw_dict",
        ),
        "release_asset_reservation": (
            asset_management_tools.asset_update_state,
            asset_management_tools.AssetStateParams,
            Dict[str, Any],
            "Release an asset reservation",
            "raw_dict",
        ),
        "adjust_consumable_quantity": (
            asset_management_tools.asset_adjust_consumable_quantity,
            asset_management_tools.ConsumableQuantityParams,
            Dict[str, Any],
            "Adjust consumable quantity",
            "raw_dict",
        ),
        "move_asset_to_stockroom": (
            asset_management_tools.asset_return_from_user,
            asset_management_tools.AssetReturnParams,
            Dict[str, Any],
            "Move an asset to a stockroom",
            "raw_dict",
        ),
        "search_stockroom_inventory": (
            asset_management_tools.asset_stockroom_inventory,
            asset_management_tools.AssetSearchParams,
            Dict[str, Any],
            "Search stockroom inventory",
            "raw_dict",
        ),
        "quarantine_asset": (
            asset_management_tools.asset_update_state,
            asset_management_tools.AssetStateParams,
            Dict[str, Any],
            "Quarantine an asset",
            "raw_dict",
        ),
        "place_asset_on_hold": (
            asset_management_tools.asset_update_state,
            asset_management_tools.AssetStateParams,
            Dict[str, Any],
            "Place an asset on hold",
            "raw_dict",
        ),
        "assign_asset_to_user": (
            asset_management_tools.asset_assign_to_user,
            asset_management_tools.AssetAssignmentParams,
            Dict[str, Any],
            "Assign an asset to a user",
            "raw_dict",
        ),
        "return_asset_from_user": (
            asset_management_tools.asset_return_from_user,
            asset_management_tools.AssetReturnParams,
            Dict[str, Any],
            "Return an asset from a user",
            "raw_dict",
        ),
        # Transfer and retirement aliases
        "create_transfer_order": (
            asset_management_tools.asset_transfer_create,
            asset_management_tools.AssetTransferOrderParams,
            Dict[str, Any],
            "Create an asset transfer order",
            "raw_dict",
        ),
        "create_transfer_order_line": (
            asset_management_tools.asset_transfer_line_create,
            asset_management_tools.AssetTransferLineParams,
            Dict[str, Any],
            "Create an asset transfer order line",
            "raw_dict",
        ),
        "add_assets_to_transfer_order": (
            asset_management_tools.asset_transfer_line_create,
            asset_management_tools.AssetTransferLineParams,
            Dict[str, Any],
            "Add assets to a transfer order",
            "raw_dict",
        ),
        "retire_asset": (
            asset_management_tools.asset_retire,
            asset_management_tools.AssetRetireParams,
            Dict[str, Any],
            "Retire an asset without physical deletion",
            "raw_dict",
        ),
        "sell_asset": (
            asset_management_tools.asset_retire,
            asset_management_tools.AssetRetireParams,
            Dict[str, Any],
            "Retire an asset as sold",
            "raw_dict",
        ),
        "donate_asset": (
            asset_management_tools.asset_retire,
            asset_management_tools.AssetRetireParams,
            Dict[str, Any],
            "Retire an asset as donated",
            "raw_dict",
        ),
        "lease_return_asset": (
            asset_management_tools.asset_retire,
            asset_management_tools.AssetRetireParams,
            Dict[str, Any],
            "Retire an asset as lease return",
            "raw_dict",
        ),
        "mark_asset_obsolete": (
            asset_management_tools.asset_retire,
            asset_management_tools.AssetRetireParams,
            Dict[str, Any],
            "Retire an obsolete asset",
            "raw_dict",
        ),
        # Reporting aliases
        "generate_asset_inventory_report": (
            asset_management_tools.asset_report_inventory,
            asset_management_tools.AssetReportParams,
            Dict[str, Any],
            "Generate an asset inventory report",
            "raw_dict",
        ),
        "generate_stockroom_inventory_report": (
            asset_management_tools.asset_report_inventory,
            asset_management_tools.AssetReportParams,
            Dict[str, Any],
            "Generate a stockroom inventory report",
            "raw_dict",
        ),
        "generate_lifecycle_report": (
            asset_management_tools.asset_report_inventory,
            asset_management_tools.AssetReportParams,
            Dict[str, Any],
            "Generate an asset lifecycle report",
            "raw_dict",
        ),
        "generate_asset_assignment_report": (
            asset_management_tools.asset_report_inventory,
            asset_management_tools.AssetReportParams,
            Dict[str, Any],
            "Generate an asset assignment report",
            "raw_dict",
        ),
        "generate_missing_asset_report": (
            asset_management_tools.asset_report_inventory,
            asset_management_tools.AssetReportParams,
            Dict[str, Any],
            "Generate a missing asset report",
            "raw_dict",
        ),
        "generate_warranty_expiry_report": (
            asset_management_tools.asset_report_inventory,
            asset_management_tools.AssetReportParams,
            Dict[str, Any],
            "Generate a warranty expiry report",
            "raw_dict",
        ),
        "generate_duplicate_asset_report": (
            asset_management_tools.asset_report_inventory,
            asset_management_tools.AssetReportParams,
            Dict[str, Any],
            "Generate a duplicate asset report",
            "raw_dict",
        ),
    }
    tool_definitions.update(asset_aliases)
    tool_definitions.update(
        {
            "servicenow_internal_cmdb.instance_preflight": (
                cmdb_ire_tools.instance_preflight,
                cmdb_ire_tools.InstancePreflightParams,
                Dict[str, Any],
                "Validate IRE access before ServiceNow CMDB/CI work.",
                "raw_dict",
            ),
            "servicenow_internal_cmdb.validate_ci_payload": (
                cmdb_ire_tools.validate_ci_payload,
                cmdb_ire_tools.ValidateCiPayloadParams,
                Dict[str, Any],
                "Dry-run validate CMDB/CI payloads through ServiceNow IRE.",
                "raw_dict",
            ),
            "servicenow_internal_cmdb.upsert_ci": (
                cmdb_ire_tools.upsert_ci,
                cmdb_ire_tools.UpsertCiParams,
                Dict[str, Any],
                "Create or update ServiceNow CIs through IRE only.",
                "raw_dict",
            ),
            "servicenow_internal_cmdb.create_csdm_relationship": (
                cmdb_ire_tools.create_csdm_relationship,
                cmdb_ire_tools.CreateCsdmRelationshipParams,
                Dict[str, Any],
                "Create CSDM-aware CI relationships through IRE relations only.",
                "raw_dict",
            ),
            "servicenow_internal_cmdb.managed_ingestion": (
                cmdb_ire_tools.managed_ingestion,
                cmdb_ire_tools.ManagedIngestionParams,
                Dict[str, Any],
                "Bulk CMDB ingestion through governed IRE dry-run and commit chunks.",
                "raw_dict",
            ),
            "servicenow_internal_cmdb.governance_insight": (
                cmdb_ire_tools.governance_insight,
                cmdb_ire_tools.GovernanceInsightParams,
                Dict[str, Any],
                "Compute CMDB ingestion completeness, correctness, compliance, and relationship "
                "health.",
                "raw_dict",
            ),
            "servicenow_internal_cmdb.health_dashboard": (
                cmdb_ire_tools.health_dashboard,
                cmdb_ire_tools.HealthDashboardParams,
                Dict[str, Any],
                "Build dashboard-ready CMDB ingestion and governance KPIs.",
                "raw_dict",
            ),
            "atf_framework.discover_atf_capabilities": (
                atf_tools.discover_atf_capabilities,
                atf_tools.DiscoverATFCapabilitiesParams,
                Dict[str, Any],
                "Discover native ServiceNow ATF tables, fields, runners, and result capabilities.",
                "raw_dict",
            ),
            "servicenow_atf_find_test": (
                atf_edit_tools.servicenow_atf_find_test,
                atf_edit_tools.ATFFindTestParams,
                Dict[str, Any],
                "Find native ServiceNow ATF tests by sys_id or name before editing.",
                "raw_dict",
            ),
            "servicenow_atf_find_step": (
                atf_edit_tools.servicenow_atf_find_step,
                atf_edit_tools.ATFFindStepParams,
                Dict[str, Any],
                "Find native ServiceNow ATF steps by sys_id, order, or label and block ambiguous "
                "matches.",
                "raw_dict",
            ),
            "servicenow_atf_get_step_inputs": (
                atf_edit_tools.servicenow_atf_get_step_inputs,
                atf_edit_tools.ATFGetStepInputsParams,
                Dict[str, Any],
                "Inspect ATF step input storage records; do not assume inputs are direct sys_atf_"
                "step fields.",
                "raw_dict",
            ),
            "servicenow_atf_debug_step_storage": (
                atf_edit_tools.servicenow_atf_debug_step_storage,
                atf_edit_tools.ATFDebugStepStorageParams,
                Dict[str, Any],
                "Diagnose where a specific ATF step stores input values using compact read-only "
                "evidence.",
                "raw_dict",
            ),
            "servicenow_atf_preview_step_input_update": (
                atf_edit_tools.servicenow_atf_preview_step_input_update,
                atf_edit_tools.ATFPreviewStepInputUpdateParams,
                Dict[str, Any],
                "Dry-run an ATF step input update with reference resolution and before/after diff.",
                "raw_dict",
            ),
            "servicenow_atf_update_step_input": (
                atf_edit_tools.servicenow_atf_update_step_input,
                atf_edit_tools.ATFUpdateStepInputParams,
                Dict[str, Any],
                "Safely update one ATF step input value after discovering storage, resolving "
                "references to sys_id, validating by re-read, and storing rollback metadata. "
                "Defaults to dry_run=true.",
                "raw_dict",
            ),
            "servicenow_atf_validate_step_input": (
                atf_edit_tools.servicenow_atf_validate_step_input,
                atf_edit_tools.ATFValidateStepInputParams,
                Dict[str, Any],
                "Re-read and validate a native ATF step input value.",
                "raw_dict",
            ),
            "servicenow_atf_rollback_step_input_update": (
                atf_edit_tools.servicenow_atf_rollback_step_input_update,
                atf_edit_tools.ATFRollbackStepInputUpdateParams,
                Dict[str, Any],
                "Rollback a previous ATF step input update using stored rollback metadata.",
                "raw_dict",
            ),
            "servicenow_resolve_reference": (
                atf_edit_tools.servicenow_resolve_reference,
                atf_edit_tools.ServiceNowResolveReferenceParams,
                Dict[str, Any],
                "Resolve one ServiceNow reference by exact lookup and block not-found or multiple "
                "matches.",
                "raw_dict",
            ),
            "servicenow_atf_update_test_metadata": (
                atf_edit_tools.servicenow_atf_update_test_metadata,
                atf_edit_tools.ATFUpdateTestMetadataParams,
                Dict[str, Any],
                "Safely preview or update native ATF test metadata with validation.",
                "raw_dict",
            ),
            "servicenow_atf_update_step_metadata": (
                atf_edit_tools.servicenow_atf_update_step_metadata,
                atf_edit_tools.ATFUpdateStepMetadataParams,
                Dict[str, Any],
                "Safely preview or update native ATF step metadata with validation.",
                "raw_dict",
            ),
            "servicenow_atf_reorder_steps": (
                atf_edit_tools.servicenow_atf_reorder_steps,
                atf_edit_tools.ATFReorderStepsParams,
                Dict[str, Any],
                "Safely preview or reorder ATF steps within a test, blocking duplicates and "
                "missing steps.",
                "raw_dict",
            ),
            "atf_framework.analyze_atf_coverage": (
                atf_tools.analyze_atf_coverage,
                atf_tools.AnalyzeATFCoverageParams,
                Dict[str, Any],
                "Analyze ATF coverage for releases, update sets, scopes, or artifact filters.",
                "raw_dict",
            ),
            "atf_framework.list_atf_tests": (
                atf_tools.list_atf_tests,
                atf_tools.ATFTestListParams,
                Dict[str, Any],
                "List native ServiceNow ATF tests with optional steps, suite membership, and "
                "filters.",
                "raw_dict",
            ),
            "atf_framework.get_atf_test": (
                atf_tools.get_atf_test,
                atf_tools.ATFTestGetParams,
                Dict[str, Any],
                "Get one native ServiceNow ATF test with steps, suite membership, and result "
                "details.",
                "raw_dict",
            ),
            "atf_framework.update_atf_test": (
                atf_tools.update_atf_test,
                atf_tools.ATFTestUpdateParams,
                Dict[str, Any],
                "Update native ServiceNow ATF test metadata with rollback details.",
                "raw_dict",
            ),
            "atf_framework.delete_atf_test": (
                atf_tools.delete_atf_test,
                atf_tools.ATFTestDeleteParams,
                Dict[str, Any],
                "Delete a native ServiceNow ATF test and optionally its steps; requires "
                "force=true.",
                "raw_dict",
            ),
            "atf_framework.clone_atf_test": (
                atf_tools.clone_atf_test,
                atf_tools.ATFTestCloneParams,
                Dict[str, Any],
                "Clone a native ServiceNow ATF test and optionally copy its ATF steps.",
                "raw_dict",
            ),
            "atf_framework.list_atf_test_steps": (
                atf_tools.list_atf_test_steps,
                atf_tools.ATFStepListParams,
                Dict[str, Any],
                "List native ServiceNow ATF steps for a test with config status details.",
                "raw_dict",
            ),
            "atf_framework.get_atf_test_step": (
                atf_tools.get_atf_test_step,
                atf_tools.ATFStepGetParams,
                Dict[str, Any],
                "Get one native ServiceNow ATF step with optional parent test details.",
                "raw_dict",
            ),
            "atf_framework.add_atf_test_step": (
                atf_tools.add_atf_test_step,
                atf_tools.ATFStepCreateParams,
                Dict[str, Any],
                "Add one native ServiceNow ATF step to an existing test.",
                "raw_dict",
            ),
            "atf_framework.create_atf_test_step": (
                atf_tools.create_atf_test_step,
                atf_tools.ATFStepCreateParams,
                Dict[str, Any],
                "Create one native ServiceNow sys_atf_step record for an existing ATF test.",
                "raw_dict",
            ),
            "atf_framework.add_atf_test_steps": (
                atf_tools.add_atf_test_steps,
                atf_tools.ATFStepBulkCreateParams,
                Dict[str, Any],
                "Add multiple native ServiceNow ATF steps to an existing test, optionally "
                "replacing current steps.",
                "raw_dict",
            ),
            "atf_framework.create_atf_test_steps": (
                atf_tools.create_atf_test_steps,
                atf_tools.ATFStepBulkCreateParams,
                Dict[str, Any],
                "Create multiple native ServiceNow sys_atf_step records for an existing ATF test.",
                "raw_dict",
            ),
            "atf_framework.update_atf_test_step": (
                atf_tools.update_atf_test_step,
                atf_tools.ATFStepUpdateParams,
                Dict[str, Any],
                "Update one native ServiceNow ATF step's order, description, expected result, "
                "config, or step type.",
                "raw_dict",
            ),
            "atf_framework.update_atf_test_steps": (
                atf_tools.update_atf_test_steps,
                atf_tools.ATFStepBulkUpdateParams,
                Dict[str, Any],
                "Bulk edit native ServiceNow sys_atf_step records for one ATF test. Use after "
                "listing steps when the user asks to edit existing ATF steps; pass dry_run=false "
                "to actually update ServiceNow.",
                "raw_dict",
            ),
            "atf_framework.delete_atf_test_step": (
                atf_tools.delete_atf_test_step,
                atf_tools.ATFStepDeleteParams,
                Dict[str, Any],
                "Delete one native ServiceNow ATF step; requires force=true.",
                "raw_dict",
            ),
            "atf_framework.clone_atf_test_step": (
                atf_tools.clone_atf_test_step,
                atf_tools.ATFStepCloneParams,
                Dict[str, Any],
                "Clone one native ServiceNow ATF step to the same or another test.",
                "raw_dict",
            ),
            "atf_framework.reorder_atf_test_steps": (
                atf_tools.reorder_atf_test_steps,
                atf_tools.ATFStepReorderParams,
                Dict[str, Any],
                "Reorder native ServiceNow ATF steps for a test.",
                "raw_dict",
            ),
            "atf_framework.list_atf_suites": (
                atf_tools.list_atf_suites,
                atf_tools.ATFSuiteListParams,
                Dict[str, Any],
                "List native ServiceNow ATF suites with optional test membership and results.",
                "raw_dict",
            ),
            "atf_framework.get_atf_suite": (
                atf_tools.get_atf_suite,
                atf_tools.ATFSuiteGetParams,
                Dict[str, Any],
                "Get one native ServiceNow ATF suite with test membership, steps, and results.",
                "raw_dict",
            ),
            "atf_framework.route_atf_request": (
                atf_tools.route_atf_request,
                atf_tools.ATFRequestRouterParams,
                Dict[str, Any],
                "Route a natural-language ATF request to the right native ATF tool workflow. For "
                "edit requests, it can discover matching ATF steps and execute updates when auto_"
                "execute=true and dry_run=false.",
                "raw_dict",
            ),
            "atf_framework.validate_atf_readiness": (
                atf_tools.validate_atf_readiness,
                atf_tools.ATFReadinessParams,
                Dict[str, Any],
                "Validate whether an ATF test or suite is ready to run, including step config and "
                "runner checks.",
                "raw_dict",
            ),
            "atf_framework.configure_atf_test_steps": (
                atf_tools.configure_atf_test_steps,
                atf_tools.ATFConfigureStepsParams,
                Dict[str, Any],
                "Auto-configure ATF step records by discovering native step configs where "
                "possible.",
                "raw_dict",
            ),
            "atf_framework.create_incident_atf_suite_and_run": (
                atf_tools.create_incident_atf_suite_and_run,
                atf_tools.IncidentATFSuiteRunParams,
                Dict[str, Any],
                "Create a native ATF incident creation test, add it to a suite, and run or report "
                "runner requirements.",
                "raw_dict",
            ),
            "atf_framework.create_incident_flow_atf_suite_and_run": (
                atf_tools.create_incident_flow_atf_suite_and_run,
                atf_tools.IncidentFlowATFSuiteRunParams,
                Dict[str, Any],
                "Create a native ATF system-admin Incident lifecycle test, add it to a suite, and "
                "run or return runner requirements.",
                "raw_dict",
            ),
            "atf_framework.generate_atf_test_plan": (
                atf_tools.generate_atf_test_plan,
                atf_tools.GenerateATFTestPlanParams,
                Dict[str, Any],
                "Generate a deterministic ATF test plan before creating records.",
                "raw_dict",
            ),
            "atf_framework.create_atf_test": (
                atf_tools.create_atf_test,
                atf_tools.ATFTestCreateRequest,
                Dict[str, Any],
                "Create or reuse one native ServiceNow ATF test and ordered steps.",
                "raw_dict",
            ),
            "atf_framework.create_atf_test_suite": (
                atf_tools.create_atf_test_suite,
                atf_tools.ATFSuiteCreateRequest,
                Dict[str, Any],
                "Create or reuse a native ServiceNow ATF test suite.",
                "raw_dict",
            ),
            "atf_framework.update_atf_suite": (
                atf_tools.update_atf_suite,
                atf_tools.ATFSuiteUpdateParams,
                Dict[str, Any],
                "Update native ServiceNow ATF suite metadata with rollback details.",
                "raw_dict",
            ),
            "atf_framework.delete_atf_suite": (
                atf_tools.delete_atf_suite,
                atf_tools.ATFSuiteDeleteParams,
                Dict[str, Any],
                "Delete a native ServiceNow ATF suite and optionally its test mappings; requires "
                "force=true.",
                "raw_dict",
            ),
            "atf_framework.clone_atf_suite": (
                atf_tools.clone_atf_suite,
                atf_tools.ATFSuiteCloneParams,
                Dict[str, Any],
                "Clone a native ServiceNow ATF suite and link or clone member tests.",
                "raw_dict",
            ),
            "atf_framework.add_tests_to_atf_suite": (
                atf_tools.add_tests_to_atf_suite,
                atf_tools.AddTestsToATFSuiteParams,
                Dict[str, Any],
                "Add ATF tests to an existing suite without duplicate mappings.",
                "raw_dict",
            ),
            "atf_framework.remove_tests_from_atf_suite": (
                atf_tools.remove_tests_from_atf_suite,
                atf_tools.RemoveTestsFromATFSuiteParams,
                Dict[str, Any],
                "Remove specific tests from an ATF suite by deleting their suite-test mappings.",
                "raw_dict",
            ),
            "atf_framework.reorder_tests_in_atf_suite": (
                atf_tools.reorder_tests_in_atf_suite,
                atf_tools.ReorderTestsInATFSuiteParams,
                Dict[str, Any],
                "Reorder the tests within an ATF suite by supplying the desired test sequence.",
                "raw_dict",
            ),
            "atf_framework.create_atf_four_suite_pack": (
                atf_tools.create_atf_four_suite_pack,
                atf_tools.ATFFourSuitePackParams,
                Dict[str, Any],
                "Create the standard ATF Smoke, Configuration, End-to-End, and Security & "
                "Regression suite structure.",
                "raw_dict",
            ),
            "atf_framework.generate_atf_tests_for_update_set": (
                atf_tools.generate_atf_tests_for_update_set,
                atf_tools.UpdateSetGenerationParams,
                Dict[str, Any],
                "Generate ATF tests for artifacts discovered in a ServiceNow update set.",
                "raw_dict",
            ),
            "atf_framework.generate_atf_tests_for_catalog_item": (
                atf_tools.generate_atf_tests_for_catalog_item,
                atf_tools.CatalogItemGenerationParams,
                Dict[str, Any],
                "Generate ATF catalog item tests for variable rendering, mandatory checks, "
                "submission, RITM, and task validation.",
                "raw_dict",
            ),
            "atf_framework.generate_atf_tests_for_record_producer": (
                atf_tools.generate_atf_tests_for_record_producer,
                atf_tools.RecordProducerGenerationParams,
                Dict[str, Any],
                "Generate ATF record producer tests that validate target table record creation.",
                "raw_dict",
            ),
            "atf_framework.generate_atf_tests_for_flow": (
                atf_tools.generate_atf_tests_for_flow,
                atf_tools.FlowGenerationParams,
                Dict[str, Any],
                "Generate ATF Flow Designer tests for trigger records and observable outcomes.",
                "raw_dict",
            ),
            "atf_framework.generate_atf_tests_for_table": (
                atf_tools.generate_atf_tests_for_table,
                atf_tools.TableGenerationParams,
                Dict[str, Any],
                "Generate ATF tests for table CRUD, dictionary, choices, references, and ACL "
                "behavior.",
                "raw_dict",
            ),
            "atf_framework.generate_atf_tests_for_business_rule": (
                atf_tools.generate_atf_tests_for_business_rule,
                atf_tools.BusinessRuleGenerationParams,
                Dict[str, Any],
                "Generate ATF business rule tests with assertion recommendations from script "
                "analysis.",
                "raw_dict",
            ),
            "atf_framework.generate_atf_tests_for_script_include": (
                atf_tools.generate_atf_tests_for_script_include,
                atf_tools.ScriptIncludeGenerationParams,
                Dict[str, Any],
                "Generate ATF Script Include contract, negative, and side-effect tests.",
                "raw_dict",
            ),
            "atf_framework.generate_atf_tests_for_client_script": (
                atf_tools.generate_atf_tests_for_client_script,
                atf_tools.ClientScriptGenerationParams,
                Dict[str, Any],
                "Generate ATF or manual fallback validation for client script behavior.",
                "raw_dict",
            ),
            "atf_framework.generate_atf_tests_for_ui_policy": (
                atf_tools.generate_atf_tests_for_ui_policy,
                atf_tools.UIPolicyGenerationParams,
                Dict[str, Any],
                "Generate ATF tests for table and catalog UI Policy behavior.",
                "raw_dict",
            ),
            "atf_framework.generate_atf_tests_for_notification": (
                atf_tools.generate_atf_tests_for_notification,
                atf_tools.NotificationGenerationParams,
                Dict[str, Any],
                "Generate ATF notification tests for triggers, recipients, content, and "
                "suppression on unrelated updates.",
                "raw_dict",
            ),
            "atf_framework.generate_atf_tests_for_report": (
                atf_tools.generate_atf_tests_for_report,
                atf_tools.ReportGenerationParams,
                Dict[str, Any],
                "Generate ATF report tests for filters, visibility, grouping, aggregations, "
                "and date filters.",
                "raw_dict",
            ),
            "atf_framework.generate_atf_tests_for_dashboard": (
                atf_tools.generate_atf_tests_for_dashboard,
                atf_tools.DashboardGenerationParams,
                Dict[str, Any],
                "Generate ATF dashboard tests for role visibility and widget filter behavior.",
                "raw_dict",
            ),
            "atf_framework.generate_atf_tests_for_scoped_app": (
                atf_tools.generate_atf_tests_for_scoped_app,
                atf_tools.ScopedAppGenerationParams,
                Dict[str, Any],
                "Generate ATF scoped-app security tests for roles, unauthorized access, "
                "cross-scope access, and dependencies.",
                "raw_dict",
            ),
            "atf_framework.generate_atf_tests_for_acl": (
                atf_tools.generate_atf_tests_for_acl,
                atf_tools.ACLGenerationParams,
                Dict[str, Any],
                "Generate role-based ATF tests for ACL allowed and denied access.",
                "raw_dict",
            ),
            "atf_framework.generate_atf_tests_for_rest_integration": (
                atf_tools.generate_atf_tests_for_rest_integration,
                atf_tools.RestIntegrationGenerationParams,
                Dict[str, Any],
                "Generate native ATF REST/API tests without exposing credentials.",
                "raw_dict",
            ),
            "atf_framework.run_atf_test": (
                atf_tools.run_atf_test,
                atf_tools.ATFExecutionRequest,
                Dict[str, Any],
                "Run or validate runner requirements for a single ATF test and collect result "
                "evidence.",
                "raw_dict",
            ),
            "atf_framework.run_atf_test_suite": (
                atf_tools.run_atf_test_suite,
                atf_tools.ATFExecutionRequest,
                Dict[str, Any],
                "Run or validate runner requirements for an ATF suite and collect result evidence.",
                "raw_dict",
            ),
            "atf_framework.run_atf_and_collect_results": (
                atf_tools.run_atf_and_collect_results,
                atf_tools.ATFRunAndCollectParams,
                Dict[str, Any],
                "Run a native ATF test or suite and collect result summary, failed steps, and "
                "evidence in one call.",
                "raw_dict",
            ),
            "atf_framework.run_existing_atf_and_show_results": (
                atf_tools.run_existing_atf_and_show_results,
                atf_tools.RunExistingATFParams,
                Dict[str, Any],
                "Resolve an existing ATF test by sys_id or name, run it, and return compact "
                "result details.",
                "raw_dict",
            ),
            "atf_framework.collect_atf_results": (
                atf_tools.collect_atf_results,
                atf_tools.CollectATFResultsParams,
                Dict[str, Any],
                "Collect ATF test, suite, and step result details.",
                "raw_dict",
            ),
            "atf_framework.explain_atf_failures": (
                atf_tools.explain_atf_failures,
                atf_tools.ExplainATFFailuresParams,
                Dict[str, Any],
                "Explain ATF failures with probable root cause categories and recommended actions.",
                "raw_dict",
            ),
            "atf_framework.collect_atf_evidence": (
                atf_tools.collect_atf_evidence,
                atf_tools.CollectATFEvidenceParams,
                Dict[str, Any],
                "Create release-ready ATF evidence package records.",
                "raw_dict",
            ),
            "atf_framework.validate_atf_quality_gate": (
                atf_tools.validate_atf_quality_gate,
                atf_tools.ATFQualityGateRequest,
                Dict[str, Any],
                "Validate ATF quality gate status for release go/no-go decisions.",
                "raw_dict",
            ),
            "atf_framework.create_atf_regression_pack": (
                atf_tools.create_atf_regression_pack,
                atf_tools.RegressionPackParams,
                Dict[str, Any],
                "Create a reusable ATF regression suite from existing and generated tests.",
                "raw_dict",
            ),
            "atf_framework.schedule_atf_suite": (
                atf_tools.schedule_atf_suite,
                atf_tools.ScheduleATFSuiteParams,
                Dict[str, Any],
                "Schedule native ATF suite execution.",
                "raw_dict",
            ),
            "atf_framework.generate_manual_test_fallback": (
                atf_tools.generate_manual_test_fallback,
                atf_tools.ManualTestFallbackParams,
                Dict[str, Any],
                "Generate structured manual test cases when ATF cannot automate the scenario.",
                "raw_dict",
            ),
        }
    )
    tool_definitions.update(
        {
            "list_esc_pages": (
                esc_page_tools.list_esc_pages,
                esc_page_tools.ListESCPagesParams,
                Dict[str, Any],
                "List Employee Center pages.",
                "raw_dict",
            ),
            "get_esc_page": (
                esc_page_tools.get_esc_page,
                esc_page_tools.GetESCPageParams,
                Dict[str, Any],
                "Get an Employee Center page.",
                "raw_dict",
            ),
            "create_esc_page": (
                esc_page_tools.create_esc_page,
                esc_page_tools.CreateESCPageParams,
                Dict[str, Any],
                "Create an Employee Center page.",
                "raw_dict",
            ),
            "update_esc_page": (
                esc_page_tools.update_esc_page,
                esc_page_tools.UpdateESCPageParams,
                Dict[str, Any],
                "Update an Employee Center page.",
                "raw_dict",
            ),
            "delete_esc_page": (
                esc_page_tools.delete_esc_page,
                esc_page_tools.DeleteESCPageParams,
                Dict[str, Any],
                "Delete an Employee Center page.",
                "raw_dict",
            ),
            "list_esc_topics": (
                esc_topic_tools.list_esc_topics,
                esc_topic_tools.ListESCTopicsParams,
                Dict[str, Any],
                "List Employee Center topics.",
                "raw_dict",
            ),
            "get_esc_topic": (
                esc_topic_tools.get_esc_topic,
                esc_topic_tools.GetESCTopicParams,
                Dict[str, Any],
                "Get an Employee Center topic.",
                "raw_dict",
            ),
            "create_esc_topic": (
                esc_topic_tools.create_esc_topic,
                esc_topic_tools.CreateESCTopicParams,
                Dict[str, Any],
                "Create an Employee Center topic.",
                "raw_dict",
            ),
            "update_esc_topic": (
                esc_topic_tools.update_esc_topic,
                esc_topic_tools.UpdateESCTopicParams,
                Dict[str, Any],
                "Update an Employee Center topic.",
                "raw_dict",
            ),
            "delete_esc_topic": (
                esc_topic_tools.delete_esc_topic,
                esc_topic_tools.DeleteESCTopicParams,
                Dict[str, Any],
                "Delete an Employee Center topic.",
                "raw_dict",
            ),
            "list_esc_announcements": (
                esc_announcement_tools.list_esc_announcements,
                esc_announcement_tools.ListESCAnnouncementsParams,
                Dict[str, Any],
                "List Employee Center announcements.",
                "raw_dict",
            ),
            "get_esc_announcement": (
                esc_announcement_tools.get_esc_announcement,
                esc_announcement_tools.GetESCAnnouncementParams,
                Dict[str, Any],
                "Get an Employee Center announcement.",
                "raw_dict",
            ),
            "create_esc_announcement": (
                esc_announcement_tools.create_esc_announcement,
                esc_announcement_tools.CreateESCAnnouncementParams,
                Dict[str, Any],
                "Create an Employee Center announcement.",
                "raw_dict",
            ),
            "update_esc_announcement": (
                esc_announcement_tools.update_esc_announcement,
                esc_announcement_tools.UpdateESCAnnouncementParams,
                Dict[str, Any],
                "Update an Employee Center announcement.",
                "raw_dict",
            ),
            "delete_esc_announcement": (
                esc_announcement_tools.delete_esc_announcement,
                esc_announcement_tools.DeleteESCAnnouncementParams,
                Dict[str, Any],
                "Delete an Employee Center announcement.",
                "raw_dict",
            ),
            "list_reports": (
                report_tools.list_reports,
                report_tools.ListReportsParams,
                Dict[str, Any],
                "List ServiceNow reports.",
                "raw_dict",
            ),
            "get_report": (
                report_tools.get_report,
                report_tools.GetReportParams,
                Dict[str, Any],
                "Get a ServiceNow report.",
                "raw_dict",
            ),
            "create_report": (
                report_tools.create_report,
                report_tools.CreateReportParams,
                Dict[str, Any],
                "Create a ServiceNow report.",
                "raw_dict",
            ),
            "update_report": (
                report_tools.update_report,
                report_tools.UpdateReportParams,
                Dict[str, Any],
                "Update a ServiceNow report.",
                "raw_dict",
            ),
            "delete_report": (
                report_tools.delete_report,
                report_tools.DeleteReportParams,
                Dict[str, Any],
                "Delete a ServiceNow report.",
                "raw_dict",
            ),
            "run_report": (
                report_tools.run_report,
                report_tools.RunReportParams,
                Dict[str, Any],
                "Run a report and return its source records.",
                "raw_dict",
            ),
            "list_dashboards": (
                dashboard_tools.list_dashboards,
                dashboard_tools.ListDashboardsParams,
                Dict[str, Any],
                "List Performance Analytics dashboards.",
                "raw_dict",
            ),
            "get_dashboard": (
                dashboard_tools.get_dashboard,
                dashboard_tools.GetDashboardParams,
                Dict[str, Any],
                "Get a dashboard and its tabs.",
                "raw_dict",
            ),
            "create_dashboard": (
                dashboard_tools.create_dashboard,
                dashboard_tools.CreateDashboardParams,
                Dict[str, Any],
                "Create a dashboard.",
                "raw_dict",
            ),
            "update_dashboard": (
                dashboard_tools.update_dashboard,
                dashboard_tools.UpdateDashboardParams,
                Dict[str, Any],
                "Update a dashboard.",
                "raw_dict",
            ),
            "delete_dashboard": (
                dashboard_tools.delete_dashboard,
                dashboard_tools.DeleteDashboardParams,
                Dict[str, Any],
                "Delete a dashboard.",
                "raw_dict",
            ),
            "list_dashboard_tabs": (
                dashboard_tools.list_dashboard_tabs,
                dashboard_tools.ListDashboardTabsParams,
                Dict[str, Any],
                "List dashboard tabs.",
                "raw_dict",
            ),
            "add_dashboard_tab": (
                dashboard_tools.add_dashboard_tab,
                dashboard_tools.AddDashboardTabParams,
                Dict[str, Any],
                "Add a dashboard tab.",
                "raw_dict",
            ),
            "update_dashboard_tab": (
                dashboard_tools.update_dashboard_tab,
                dashboard_tools.UpdateDashboardTabParams,
                Dict[str, Any],
                "Update a dashboard tab.",
                "raw_dict",
            ),
            "delete_dashboard_tab": (
                dashboard_tools.delete_dashboard_tab,
                dashboard_tools.DeleteDashboardTabParams,
                Dict[str, Any],
                "Delete a dashboard tab.",
                "raw_dict",
            ),
            "list_pa_indicators": (
                pa_tools.list_pa_indicators,
                pa_tools.ListPAIndicatorsParams,
                Dict[str, Any],
                "List Performance Analytics indicators.",
                "raw_dict",
            ),
            "get_pa_indicator": (
                pa_tools.get_pa_indicator,
                pa_tools.GetPAIndicatorParams,
                Dict[str, Any],
                "Get a Performance Analytics indicator.",
                "raw_dict",
            ),
            "create_pa_indicator": (
                pa_tools.create_pa_indicator,
                pa_tools.CreatePAIndicatorParams,
                Dict[str, Any],
                "Create a Performance Analytics indicator.",
                "raw_dict",
            ),
            "update_pa_indicator": (
                pa_tools.update_pa_indicator,
                pa_tools.UpdatePAIndicatorParams,
                Dict[str, Any],
                "Update a Performance Analytics indicator.",
                "raw_dict",
            ),
            "delete_pa_indicator": (
                pa_tools.delete_pa_indicator,
                pa_tools.DeletePAIndicatorParams,
                Dict[str, Any],
                "Delete a Performance Analytics indicator.",
                "raw_dict",
            ),
            "list_pa_widget_instances": (
                pa_tools.list_pa_widget_instances,
                pa_tools.ListPAWidgetInstancesParams,
                Dict[str, Any],
                "List widget instances on a dashboard tab.",
                "raw_dict",
            ),
        }
    )
    return tool_definitions
