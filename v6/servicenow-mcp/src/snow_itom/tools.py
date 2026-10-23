"""
The ITOM tool registry: one definition per tool, used by servicenow-mcp (MCPServer) and the Snow Agent (FastMCP and
its tool catalog). The ToolSpec / make_function machinery is shared with snow_itam.
"""

from typing import Any, Dict, List, Optional

from snow_itam.tools import P, ToolSpec

DRY = P("dry_run", bool, False, "Preview the change without writing")
CONFIRM = P("confirm", bool, False, "Required true to create (otherwise returned as requires_confirmation)")
DEPTH = "Relationship levels to follow (max 6)"

TOOLS: List[ToolSpec] = [
    # ── capabilities & operations ──
    ToolSpec("get_itom_capabilities", "get_itom_capabilities", "read",
             "Show which ITOM features this instance supports (plugins, tables, MID Servers, your roles) and what "
             "must be installed for the rest (Event Management, Discovery, Service Mapping ...). Answers 'which "
             "ITOM plugins are installed / what can I use'.",
             (P("include_plugin_state", bool, False,
                "Also read plugin states from v_plugin (admin only; can be slow on a busy instance)"),), "ops"),
    ToolSpec("list_mid_servers", "list_mid_servers", "read",
             "List MID Servers (ecc_agent) with status, validation, host and version.",
             (P("active_only", bool, False, "Only MID Servers with status Up"),
              P("limit", int, 20, "Maximum rows (max 200)")), "ops"),
    ToolSpec("list_discovery_schedules", "list_discovery_schedules", "read",
             "List Discovery schedules (needs the Discovery plugin).",
             (P("active_only", bool, True, "Only active schedules"), P("limit", int, 15, "Maximum rows (max 200)")),
             "ops"),
    ToolSpec("get_discovery_status", "get_discovery_status", "read",
             "Get one Discovery run (by number or sys_id, with its recent log) or list recent runs, optionally by "
             "state (Starting, Active, Completed, Canceled).",
             (P("status", str, "", "Discovery status number (DIS...) or sys_id; empty lists recent runs"),
              P("state", str, "", "Filter by state when listing"), P("limit", int, 10, "Maximum rows (max 100)")),
             "ops"),
    ToolSpec("list_active_events", "list_active_events", "read",
             "List Event Management alerts (em_alert) by state and severity — open / critical alerts and events "
             "from monitoring (needs the Event Management plugin).",
             (P("severity", str, "", "Severity value or label, e.g. 1 or Critical"),
              P("state", str, "Open", "Alert state, e.g. Open, Reopen, Flapping, Closed"),
              P("limit", int, 15, "Maximum rows (max 200)")), "ops"),
    # ── Event Management (com.glide.em) ──
    ToolSpec("list_events", "list_events", "read",
             "List Event Management events (em_event) — raw monitoring events before they become alerts — by "
             "source, node, state, severity, since.",
             (P("source", str, "", "Event source, e.g. SCOM"), P("node", str, "", "Node / host name"),
              P("state", str, "", "Event state, e.g. Ready, Processed, Error"), P("severity", str, "", "Severity"),
              P("since", str, "", "YYYY-MM-DD[ HH:MM:SS]"), P("limit", int, 20, "Maximum rows (max 200)")),
             "events"),
    ToolSpec("get_event", "get_event", "read", "Get one Event Management event with the alert it produced.",
             (P("event", str, desc="em_event sys_id"),), "events"),
    ToolSpec("create_event", "create_event", "write",
             "Send (ingest) an event to Event Management, e.g. from monitoring; its rules may raise an alert. "
             "Refuses an identical event still waiting to be processed. Needs confirm=true; dry_run previews.",
             (P("source", str, desc="Monitoring source, e.g. 'Nagios'"),
              P("node", str, desc="Host / node the event is about"),
              P("severity", str, desc="Severity label or value, e.g. Critical or 1"),
              P("description", str, "", "What happened"), P("type", str, "", "Event type, e.g. 'High CPU'"),
              P("resource", str, "", "Resource, e.g. 'CPU' or 'disk C:'"), P("metric_name", str, "", "Metric"),
              P("message_key", str, "", "De-duplication key (default: built by Event Management)"),
              P("additional_info", Optional[Dict[str, Any]], None, "Extra key/values sent with the event"),
              P("ci", str, "", "CI name or sys_id the event is about (sent in additional_info)"),
              P("allow_duplicate", bool, False, "Send even if an identical event is still pending"), DRY, CONFIRM),
             "events"),
    ToolSpec("get_alert", "get_alert", "read",
             "Get one alert (number or sys_id) with its CI, linked incident and grouped (secondary) alerts.",
             (P("alert", str, desc="Alert number (Alert0001234) or sys_id"),), "events"),
    ToolSpec("acknowledge_alert", "acknowledge_alert", "write",
             "Acknowledge (or un-acknowledge) an alert, with an optional work note. dry_run previews.",
             (P("alert", str, desc="Alert number or sys_id"), P("acknowledged", bool, True, "false to un-acknowledge"),
              P("work_note", str, "", "Work note to add"), DRY), "events"),
    ToolSpec("close_alert", "close_alert", "write",
             "Close an alert (state from the instance's choices). Needs confirm=true; dry_run previews.",
             (P("alert", str, desc="Alert number or sys_id"), P("close_note", str, "", "Work note explaining why"),
              DRY, CONFIRM), "events"),
    ToolSpec("list_alert_groups", "list_alert_groups", "read",
             "List correlated alert groups: primary alerts and how many alerts Event Management grouped under "
             "each (correlate / related alerts).",
             (P("include_closed", bool, False, "Include closed groups"), P("limit", int, 20, "Maximum groups")),
             "events"),
    ToolSpec("create_incident_from_alert", "create_incident_from_alert", "write",
             "Open an incident for an alert (CI, urgency / impact from the alert severity) and link it on the alert. "
             "Returns the existing incident if the alert already has an active one. Needs confirm=true.",
             (P("alert", str, desc="Alert number or sys_id"), P("short_description", str, "", "Default: from alert"),
              P("assignment_group", str, "", "Group name or sys_id"), P("urgency", str, "", "1-3, default from severity"),
              P("impact", str, "", "1-3, default from severity"), DRY, CONFIRM), "events"),
    ToolSpec("get_service_health", "get_service_health", "read",
             "Health of a service: worst open alert severity across the service and the CIs it depends on, with "
             "the alerts behind it (needs Event Management).",
             (P("service", str, desc="Service name, number or sys_id"), P("depth", int, 3, DEPTH),
              P("limit", int, 20, "Alerts to return")), "events"),
    # ── CMDB topology ──
    ToolSpec("list_ci_classes", "list_ci_classes", "read",
             "List CMDB CI classes under a root class (default cmdb_ci) with label, parent class and depth.",
             (P("root", str, "cmdb_ci", "Root class, e.g. cmdb_ci_server or cmdb_ci_service"),
              P("text", str, "", "Filter on class name or label"), P("max_depth", int, 12, "Levels below root"),
              P("limit", int, 200, "Maximum rows (max 1000)"),
              P("include_counts", bool, False, "Add record counts (first 50 classes)")), "topology"),
    ToolSpec("get_service_topology", "get_service_topology", "read",
             "Show the CIs a service depends on (its map: applications, servers, databases it runs on): walks "
             "cmdb_rel_ci down from the service and adds its svc_ci_assoc members. Returns nodes, edges and whether "
             "the walk was cut off.",
             (P("service", str, desc="Service name, number or sys_id (any cmdb_ci_service class)"),
              P("depth", int, 3, DEPTH), P("max_nodes", int, 200, "Maximum CIs (max 500)"),
              P("relationship_type", str, "", "Only this type, e.g. 'Depends on::Used by' (name or sys_id)"),
              P("include_service_content", bool, False,
                "Also return the Application Service API view (manual application services only)")), "topology"),
    ToolSpec("get_ci_impact", "get_ci_impact", "read",
             "Impact of a CI (e.g. a server): what breaks if it goes down — walks cmdb_rel_ci up to everything that "
             "depends on it, lists the services affected (services that use the server) and their open incidents.",
             (P("ci", str, desc="CI name or sys_id"), P("depth", int, 4, DEPTH),
              P("max_nodes", int, 300, "Maximum CIs (max 500)"),
              P("include_incidents", bool, True, "List active incidents on the CI, its dependants and services")),
             "topology"),
    ToolSpec("find_unmapped_cis", "find_unmapped_cis", "read",
             "Find unmapped / orphan CIs, e.g. servers not mapped to anything: CIs of a class with no relationships "
             "that belong to no service (svc_ci_assoc).",
             (P("ci_class", str, "cmdb_ci_server", "CI class (list_ci_classes)"),
              P("scan_limit", int, 500, "CIs to check (max 2000)"),
              P("operational_only", bool, False, "Only operational CIs"),
              P("check_service_membership", bool, True, "Also require no svc_ci_assoc membership"),
              P("limit", int, 50, "Unmapped CIs to return (max 500)")), "topology"),
    # ── services ──
    ToolSpec("create_application_service", "create_application_service", "write",
             "Create a manual application service with its CIs through ServiceNow's Application Service API. "
             "relations: [{child, parent?}] — no parent marks an entry point. Refuses an existing service name. "
             "Needs confirm=true; dry_run previews.",
             (P("name", str, desc="Service name"),
              P("cis", str, "", "Comma-separated CI names or sys_ids to add as entry points (simple form)"),
              P("relations", Optional[List[Dict[str, Any]]], None,
                "CIs with their place in the map: [{'child': CI, 'parent': CI?}]"),
              P("fields", Optional[Dict[str, Any]], None,
                "busines_criticality, environment, used_for, operational_status, owned_by, managed_by, "
                "support_group, version, short_description, comments"), DRY, CONFIRM), "service"),
    # ── flows ──
    ToolSpec("list_flows", "list_flows", "read",
             "List Flow Designer flows / subflows and legacy workflows.",
             (P("kind", str, "flow", "flow | subflow | workflow | all"),
              P("active_only", bool, True, "Only active flows / workflows with an active published version"),
              P("text", str, "", "Name contains"), P("limit", int, 25, "Maximum rows per kind (max 200)")), "flows"),
    ToolSpec("list_flow_executions", "list_flow_executions", "read",
             "List flow (sys_flow_context) or workflow (wf_context) executions — runs that failed, are waiting or "
             "completed — newest first.",
             (P("flow", str, "", "Flow / workflow name or sys_id"),
              P("state", str, "", "State value or label, e.g. Error, Complete, Waiting"),
              P("engine", str, "flow", "flow | workflow"), P("since", str, "", "YYYY-MM-DD[ HH:MM:SS]"),
              P("limit", int, 20, "Maximum rows (max 200)")), "flows"),
    ToolSpec("get_flow_execution", "get_flow_execution", "read",
             "Get one flow or workflow execution (state, error, source record, run time) by sys_id.",
             (P("execution", str, desc="sys_flow_context or wf_context sys_id"),), "flows"),
]

# CMDB configuration items — the core dependency of ITOM. servicenow-mcp registers these from its CMDB module
# (tools/cmdb.py), the Snow Agent through itom_bridge; one implementation (snow_itom/cmdb.py).
CMDB_TOOLS: List[ToolSpec] = [
    ToolSpec("search_cmdb_ci", "search_cmdb_ci", "read",
             "Search for Configuration Items (CIs) in ServiceNow CMDB across tables like cmdb_ci, cmdb_ci_server, "
             "cmdb_ci_appl.",
             (P("name", str, "", "Name or partial name of the CI"),
              P("sys_class_name", str, "cmdb_ci", "CMDB class, e.g. cmdb_ci_server, cmdb_ci_win_server, cmdb_ci_appl"),
              P("ip_address", str, "", "IP address to locate"),
              P("operational_status", str, "", "Optional filter: 1=Operational, 2=Non-operational, etc."),
              P("query", str, "", "Custom encoded query string"), P("limit", int, 15, "Maximum CIs to return")),
             "cmdb"),
    ToolSpec("get_cmdb_ci", "get_cmdb_ci", "read", "Retrieve complete details of a Configuration Item by sys_id.",
             (P("ci_sys_id", str, desc="32-character system ID of the CI"),
              P("table", str, "cmdb_ci", "Table name (defaults to cmdb_ci)")), "cmdb"),
    ToolSpec("create_cmdb_ci", "create_cmdb_ci", "write",
             "Create a new Configuration Item (CI) in the CMDB — e.g. a server with IP address, OS and location — "
             "with duplicate checks on name and serial number. Hardware classes also get an asset record from "
             "ServiceNow's asset-CI sync. Pass dry_run=true to preview.",
             (P("name", str, desc="Name of the CI"),
              P("sys_class_name", str, desc="CI class table or label, e.g. cmdb_ci_linux_server / 'Linux Server', "
                                            "cmdb_ci_win_server / 'Windows Server', cmdb_ci_server / 'Server'"),
              P("fields", Optional[Dict[str, Any]], None,
                "Other fields, e.g. {'ip_address': '10.0.0.5', 'os': 'Linux Red Hat', 'location': 'Pune'} "
                "(references by name or sys_id)"),
              DRY), "cmdb"),
    ToolSpec("update_cmdb_ci", "update_cmdb_ci", "write",
             "Update attributes of an existing Configuration Item in ServiceNow CMDB.",
             (P("ci_sys_id", str, desc="System ID of the CI"),
              P("fields", Dict[str, Any], desc="Fields to update (references by name or sys_id)"),
              P("table", str, "cmdb_ci", "Specific class table or generic cmdb_ci"),
              P("dry_run", bool, False, "Return the field diff without updating")), "cmdb"),
]

ALL_TOOLS: List[ToolSpec] = TOOLS + CMDB_TOOLS
TOOLS_BY_NAME: Dict[str, ToolSpec] = {t.name: t for t in ALL_TOOLS}
assert len(TOOLS_BY_NAME) == len(ALL_TOOLS), "duplicate ITOM tool names"
