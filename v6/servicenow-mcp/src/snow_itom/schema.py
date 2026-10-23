"""
ITOM table, field and feature definitions.

Tables and fields marked "verified" were read from sys_db_object / sys_dictionary of a live instance (Australia
release, CMDB Enterprise + Application Service + Flow Designer active; Event Management, Discovery and Service
Mapping not installed). Plugin-dependent tables are never assumed: every tool checks that its table exists first.
State and choice values are read live from sys_choice, except where noted.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class Needs:
    """What a feature needs on the connected instance; checked on every call by ITOMCore.ensure()."""
    feature: str
    plugin: str = ""
    tables: Tuple[str, ...] = ()
    fields: Dict[str, Tuple[str, ...]] = field(default_factory=dict)     # missing -> INCOMPATIBLE_INSTANCE
    optional: Dict[str, Tuple[str, ...]] = field(default_factory=dict)   # missing -> skipped, reported
    roles: Tuple[str, ...] = ()                                           # any of these (admin always)

# ── fields (verified) ──────────────────────────────────────────────────────────
CI_FIELDS = "sys_id,name,sys_class_name,operational_status,install_status,ip_address"
REL_FIELDS = "sys_id,parent,child,type"
SERVICE_FIELDS = ("sys_id,name,sys_class_name,number,service_classification,busines_criticality,environment,used_for,"
                  "operational_status,owned_by,managed_by,support_group,version,sys_updated_on")
MID_FIELDS = "sys_id,name,status,validated,host_name,ip_address,version,started,stopped,last_refreshed"
DISCOVERY_STATUS_FIELDS = ("sys_id,number,state,description,discover,dscheduler,source,started,completed,progress,"
                           "duration,sys_created_on,sys_updated_on")
DISCOVERY_SCHEDULE_FIELDS = "sys_id,name,discover,disco_run_type,mid_select_method,mid_server,active,max_run"
ALERT_FIELDS = "sys_id,number,description,severity,state,cmdb_ci,source,node,initial_event_time,incident"
FLOW_FIELDS = "sys_id,name,internal_name,type,active,status,run_as,description,sys_scope,sys_updated_on"
WORKFLOW_FIELDS = "sys_id,name,table,description,sys_updated_on"
FLOW_CONTEXT_FIELDS = ("sys_id,name,flow,state,error_state,error_message,source_table,source_record,calling_source,"
                       "is_test_run,run_time,sys_created_on,sys_updated_on")
WF_CONTEXT_FIELDS = "sys_id,name,workflow,state,result,table,id,started,ended,started_by,active"
INCIDENT_FIELDS = "sys_id,number,short_description,priority,state,cmdb_ci,business_service,assignment_group"

# Application service fields create_application_service may set (verified on cmdb_ci_service_discovered and its
# parents). Value: reference table + lookup columns, or "" for a plain / choice value.
SERVICE_WRITABLE = {
    "short_description": ("", ()), "comments": ("", ()), "version": ("", ()),
    "busines_criticality": ("", ()), "environment": ("", ()), "used_for": ("", ()), "operational_status": ("", ()),
    "owned_by": ("sys_user", ("user_name", "email", "name")),
    "managed_by": ("sys_user", ("user_name", "email", "name")),
    "support_group": ("sys_user_group", ("name",)),
}
SERVICE_CHOICE_FIELDS = ("busines_criticality", "environment", "used_for", "operational_status")
# Class chain whose sys_choice rows apply to an application service
SERVICE_CHOICE_TABLES = ("cmdb_ci_service_discovered", "cmdb_ci_service_auto", "cmdb_ci_service", "cmdb_ci")
SERVICE_ROOT = "cmdb_ci_service"
APP_SERVICE_TABLE = "cmdb_ci_service_discovered"
APP_SERVICE_TYPE_DISCOVERED = "2"   # cmdb_ci_service_discovered.type 'Discovered': the API refuses these

# Roles allowed per area (admin always). "service" names verified in sys_user_role; the Event Management roles are
# the documented ones (they exist only once the plugin is active). ServiceNow ACLs still apply afterwards.
ROLES: Dict[str, List[str]] = {
    "service": ["app_service_admin", "sm_admin", "itom_admin", "sn_cmdb_admin", "sn_cmdb_editor"],
    "event_ingest": ["evt_mgmt_admin", "evt_mgmt_integration", "itom_admin"],
    "alert_operate": ["evt_mgmt_admin", "evt_mgmt_operator", "itom_admin"],
    "alert_incident": ["evt_mgmt_admin", "evt_mgmt_operator", "itom_admin", "itil"],
}

# ── Event Management (com.glide.em) — built from ServiceNow's documented em_event / em_alert layout ─────────────
# Not yet verified on a live instance with the plugin (the development instance does not have it). Every call checks
# the tables and fields first, so a release with a different layout gets INCOMPATIBLE_INSTANCE, never a wrong write.
EM_PLUGIN = "com.glide.em"
EVENT_REQUIRED = ("source", "node", "type", "resource", "severity", "description", "state", "time_of_event")
EVENT_OPTIONAL = ("metric_name", "message_key", "additional_info", "event_class", "resolution_state", "alert",
                  "processing_notes", "ci_identifier")
ALERT_REQUIRED = ("number", "state", "severity", "description", "source", "node", "cmdb_ci", "acknowledged",
                  "initial_event_time")
ALERT_OPTIONAL = ("type", "resource", "metric_name", "message_key", "last_event_time", "incident", "parent",
                  "group_source", "is_group_alert", "maintenance")

NEEDS_EVENTS = Needs("Event Management events", EM_PLUGIN, ("em_event",), {"em_event": EVENT_REQUIRED},
                     {"em_event": EVENT_OPTIONAL})
NEEDS_ALERTS = Needs("Event Management alerts", EM_PLUGIN, ("em_alert",), {"em_alert": ALERT_REQUIRED},
                     {"em_alert": ALERT_OPTIONAL})
NEEDS_ALERT_INCIDENT = Needs("Incidents from alerts", EM_PLUGIN, ("em_alert", "incident"),
                             {"em_alert": ALERT_REQUIRED + ("incident",)})
NEEDS_ALERT_GROUPS = Needs("Alert groups", EM_PLUGIN, ("em_alert",), {"em_alert": ALERT_REQUIRED + ("parent",)},
                           {"em_alert": ("group_source", "is_group_alert")})
# Event / alert fields create_event accepts (value -> stored as given; severity validated against sys_choice)
EVENT_WRITABLE = ("source", "node", "type", "resource", "metric_name", "severity", "description", "message_key",
                  "additional_info", "event_class", "time_of_event", "ci_identifier")

# Bounds for graph walks and scans (keep calls to the instance predictable)
MAX_DEPTH = 6
MAX_NODES = 500
MAX_SCAN = 2000
BATCH = 100

# ── features -> what they need ─────────────────────────────────────────────────
# Plugin ids are what the capability report checks; a feature is available only when its tables exist.
FEATURES = [
    {"feature": "CMDB, CI classes, relationships, service topology and impact", "plugin": "com.snc.cmdb",
     "tables": ["cmdb_ci", "cmdb_rel_ci", "cmdb_ci_service"],
     "tools": "list_ci_classes, get_service_topology, get_ci_impact, find_unmapped_cis"},
    {"feature": "Application services (create a manual service with its CIs)",
     "plugin": "com.snc.cmdb.it_service.api", "tables": ["cmdb_ci_service_discovered", "svc_ci_assoc"],
     "tools": "create_application_service"},
    {"feature": "Flow Designer flows and executions", "plugin": "com.glide.hub.flow_engine",
     "tables": ["sys_hub_flow", "sys_flow_context"], "tools": "list_flows, list_flow_executions, get_flow_execution"},
    {"feature": "Legacy workflows and executions", "plugin": "com.glideapp.workflow",
     "tables": ["wf_workflow", "wf_context"], "tools": "list_flows, list_flow_executions, get_flow_execution"},
    {"feature": "MID Servers", "plugin": "", "tables": ["ecc_agent"], "tools": "list_mid_servers"},
    {"feature": "Discovery status", "plugin": "", "tables": ["discovery_status"], "tools": "get_discovery_status"},
    {"feature": "Discovery schedules", "plugin": "com.snc.discovery", "tables": ["discovery_schedule"],
     "tools": "list_discovery_schedules"},
    {"feature": "Event Management alerts: list, get, acknowledge, close, alert groups",
     "plugin": EM_PLUGIN, "tables": ["em_alert"],
     "tools": "list_active_events, get_alert, acknowledge_alert, close_alert, list_alert_groups",
     "verification": "documented layout — not yet verified live"},
    {"feature": "Event Management events: send (ingest), list, get", "plugin": EM_PLUGIN, "tables": ["em_event"],
     "tools": "create_event, list_events, get_event", "verification": "documented layout — not yet verified live"},
    {"feature": "Incident from an alert (linked both ways)", "plugin": EM_PLUGIN, "tables": ["em_alert", "incident"],
     "tools": "create_incident_from_alert", "verification": "documented layout — not yet verified live"},
    {"feature": "Service health from open alerts on the service's CIs", "plugin": EM_PLUGIN,
     "tables": ["em_alert", "cmdb_rel_ci"], "tools": "get_service_health",
     "verification": "documented layout — not yet verified live"},
]
# Not implemented by this server. Listed so the capability report says what to install; nothing is emulated.
NOT_IMPLEMENTED = [
    {"feature": "Run a discovery scan", "requires": "Discovery (com.snc.discovery) and a validated MID Server; "
     "ServiceNow has no documented public REST call to start a schedule that this server could verify",
     "plugin": "com.snc.discovery", "table": "discovery_schedule"},
    {"feature": "Top-down service mapping from an entry point", "requires": "Service Mapping "
     "(com.snc.service-mapping) and a MID Server", "plugin": "com.snc.service-mapping", "table": ""},
    {"feature": "Start / cancel a flow over REST", "requires": "IntegrationHub REST trigger or a scripted REST API "
     "on the instance (not installed by this server)", "plugin": "", "table": ""},
    {"feature": "Anomalies, metric data, capacity forecasts",
     "requires": "Metric Intelligence / Health Log Analytics, ITOM Optimization (licensed); their tables are not "
     "documented publicly in enough detail to build against without an instance that has them",
     "plugin": "", "table": ""},
]
