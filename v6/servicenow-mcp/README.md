# ServiceNow MCP Server (Python)

A comprehensive, production-grade Model Context Protocol (MCP) server for **ServiceNow**, implemented in **100% pure Python** (zero Node.js/npm dependencies).

Modeled after and inspired by [aartiq/servicenow-mcp](https://github.com/aartiq/servicenow-mcp) (NowAIKit), this server provides an enterprise-ready interface allowing LLMs (Claude Desktop, Cursor, GitHub Copilot, Continue, and custom MCP clients) to interact with ServiceNow instances securely and intelligently.

---

## 🌟 Key Features

- **100% Pure Python**: Powered by the official Python `mcp` 2.x SDK and `requests`. Zero Node.js or npm dependencies required.
- **150 Domain Tools**: Core CRUD, ITSM (Incident, Problem, Change, Task), CMDB, **IT Asset Management (38 tools)**, **IT Operations Management (22 tools)**, Service Catalog, Knowledge, Users, Scripting, ATF, System Properties.
- **Safety First (Dry-Run & Write Guardrails)**:
  - Core CRUD, CMDB CI, scripting, script-record and all ITAM write tools support `dry_run=True` (validated payload or field diff, no remote change).
  - ITAM changes with real-world consequences (retire, missing, assignment, transfers, deallocation, contract state, purchase orders) also need `confirm=true`, and every ITAM write is read back and verified.
  - Global write lock (`SERVICENOW_WRITE_ENABLED=false`) prevents unintended modifications in production environments.
- **Encoded Query Linting**: Built-in query validator checks ServiceNow encoded queries (`active=true^ORDERBYDESCsys_created_on`) for syntax errors, unmatched parentheses, and invalid operators before execution.
- **MCP Resources & Prompts**:
  - **Resources**: `servicenow://instance/status` and `servicenow://tables/core` for instant context injection.
  - **Prompts**: Reusable prompt workflows such as `investigate_incident`, `create_change_plan`, and `generate_business_rule`.
- **Basic or OAuth 2.0 authentication** (password or client-credentials grant).
- **Multi-Transport Support**:
  - `stdio`: Default transport for desktop clients like Claude Desktop and Cursor.
  - `sse`: Server-Sent Events transport for web-based agents and microservices.
  - `streamable-http`: Modern HTTP streaming transport (`/mcp` endpoint) powered by starlette/uvicorn.

---

## 📂 Project Structure

```
servicenow-mcp/
├── pyproject.toml              # Build & dependency metadata
├── requirements.txt            # Python requirements (mcp, requests, python-dotenv, etc.)
├── .env.example                # Configuration template
├── .env                        # Local instance credentials (gitignored)
├── run.bat                     # Windows CLI runner
├── src/
│   ├── servicenow_mcp/
│   │   ├── __init__.py         # Package version (1.0.0)
│   │   ├── __main__.py         # Module execution entrypoint
│   │   ├── config.py           # Settings and environment loader
│   │   ├── errors.py           # Custom exceptions & diagnostic error messages
│   │   ├── client.py           # ServiceNow REST & Stats API client (Basic / OAuth 2.0)
│   │   ├── server.py           # MCP Server lifecycle and transport runners
│   │   ├── tools/              # 150 ServiceNow Domain Tools
│   │   │   ├── __init__.py     # Tool registry aggregator
│   │   │   ├── core.py         # CRUD, table schema, query linting, stats, diffs
│   │   │   ├── incident.py     # Incident lifecycle, work notes, comments
│   │   │   ├── change.py       # Change requests and approval workflows
│   │   │   ├── problem.py      # Problem investigation & root cause tracking
│   │   │   ├── task.py         # Generic task operations & approval records
│   │   │   ├── cmdb.py         # CI search, relationship mapping, health
│   │   │   ├── itam.py         # Registers the 38 ITAM tools (logic in snow_itam)
│   │   │   ├── catalog.py      # Service catalog, cart ordering, RITMs
│   │   │   ├── knowledge.py    # Knowledge articles, search, retirement
│   │   │   ├── user.py         # Users, groups, memberships, role verification
│   │   │   ├── scripting.py    # Business rules, client scripts, background scripts
│   │   │   ├── script_types.py # Registry of script record types (tables, fields, choices)
│   │   │   ├── script_records.py # Generic list/get/create/update for every script type
│   │   │   ├── atf.py          # Automated Test Framework suites and results
│   │   │   ├── properties.py   # System properties (sys_properties)
│   │   │   ├── updateset.py    # Update sets and custom application scopes
│   │   │   ├── itom.py         # Registers the 22 ITOM tools (logic in snow_itom)
│   │   │   └── health.py       # Instance connection check and latency stats
│   │   ├── resources/          # MCP Resources
│   │   └── prompts/            # MCP Prompts
│   └── snow_itam/              # IT Asset Management logic, framework-independent (shared with the Snow Agent)
│       ├── tools.py            # One registry of the 38 ITAM tools (names, parameters, read/write)
│       ├── core.py             # References, role checks, live lifecycle choices, verified write pipeline
│       ├── assets.py · inventory.py · licenses.py · contracts.py · procurement.py · reports.py
│       ├── schema.py           # Verified tables / fields, features and plugin requirements
│       └── backends.py         # Adapters for servicenow_mcp's client and the agent's sn_client
│   └── snow_itom/              # IT Operations Management logic, framework-independent (shared with the Snow Agent)
│       ├── tools.py            # One registry of the 22 ITOM + 4 CMDB CI tools (uses snow_itam's ToolSpec)
│       ├── core.py             # CI class tree, bounded relationship walks, live-choice checks (extends snow_itam's core)
│       ├── operations.py · topology.py · services.py · flows.py · events.py · cmdb.py
│       ├── schema.py           # Verified tables / fields, features, what needs which plugin
│       └── backends.py         # snow_itam's adapters + scripted REST calls (Application Service API)
└── tests/
    ├── test_server.py · test_script_records.py
    ├── test_itam.py            # ITAM tools on an in-memory fake instance (fake_servicenow.py)
    ├── test_itom.py            # ITOM tools + CMDB / incident extensions on the same fake instance
    └── test_oauth.py           # OAuth 2.0 token handling
```

---

## 🚀 Quickstart

### 1. Requirements
- Python 3.10+ (tested on Python 3.10, 3.11, 3.12, 3.13, 3.14)
- Network access to a ServiceNow instance (PDI or enterprise instance)

### 2. Installation

Clone or copy the directory and install dependencies:

```bash
cd servicenow-mcp
pip install -r requirements.txt
```

Or install in editable mode:
```bash
pip install -e .
```

### 3. Configuration

Copy `.env.example` to `.env` and fill in your ServiceNow instance credentials:

```ini
# ServiceNow Instance URL
SERVICENOW_INSTANCE_URL=https://your-instance.service-now.com

# Basic authentication
SERVICENOW_USERNAME=integration.user
SERVICENOW_PASSWORD=your_password

# Optional OAuth 2.0 (System OAuth > Application Registry > "Create an OAuth API endpoint for external clients")
# SERVICENOW_CLIENT_ID=...
# SERVICENOW_CLIENT_SECRET=...
# SERVICENOW_OAUTH_GRANT=password            # or client_credentials (then no username/password needed)

# Safety & Limits
SERVICENOW_WRITE_ENABLED=false     # Set to true to allow create/update/delete operations
SERVICENOW_TIMEOUT=30              # Request timeout in seconds
```

With a client id and secret the server requests a token from `/oauth_token.do`, caches it until shortly before it
expires, and on a `401` refreshes it once (refresh token, else a new grant). Without them it uses Basic auth.

### 4. Verify Connection & List Tools

Test connectivity to your ServiceNow instance:
```bash
python -m servicenow_mcp --test-connection
```

List all 150 registered tools:
```bash
python -m servicenow_mcp --list-tools
```

---

## 🔌 Running the Server

### Transport: stdio (Default for Claude & Cursor)
```bash
python -m servicenow_mcp
```
*(On Windows, you can also run `run.bat`)*

### Transport: Streamable HTTP
```bash
python -m servicenow_mcp --transport streamable-http --host 127.0.0.1 --port 8000
```
*Accessible at `http://127.0.0.1:8000/mcp`.*

### Transport: SSE (Server-Sent Events)
```bash
python -m servicenow_mcp --transport sse --host 127.0.0.1 --port 8000
```
*SSE endpoint at `http://127.0.0.1:8000/sse`.*

---

## 💻 Client Configuration

### Claude Desktop
Add this to your `claude_desktop_config.json` (keep real credentials in `.env`, not in shared config files):

**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "servicenow": {
      "command": "python",
      "args": ["-m", "servicenow_mcp"],
      "cwd": "<path to>/servicenow-mcp",
      "env": {
        "SERVICENOW_INSTANCE_URL": "https://your-instance.service-now.com",
        "SERVICENOW_USERNAME": "integration.user",
        "SERVICENOW_PASSWORD": "<password>",
        "SERVICENOW_WRITE_ENABLED": "true"
      }
    }
  }
}
```

### Cursor IDE
Add to `.cursor/mcp.json` or through Cursor Settings → Features → MCP:

```json
{
  "mcpServers": {
    "servicenow": {
      "command": "python",
      "args": ["-m", "servicenow_mcp"],
      "cwd": "<path to>/servicenow-mcp"
    }
  }
}
```

---

## 🛠️ Complete Tool Catalog (150 Tools)

Generated from the tools the server actually registers.

| Category | Tools | Description |
|---|---|---|
| **Core CRUD & Query** (9) | `query_records`, `get_record`, `create_record`, `update_record`, `delete_record`, `get_table_schema`, `get_mandatory_fields`, `validate_query`, `execute_stats` | Table API CRUD with dry-run diffs, schema and mandatory-field inspection, query linting, stats. |
| **Incident Management** (8) | `create_incident`, `get_incident`, `list_incidents`, `update_incident`, `resolve_incident`, `close_incident`, `add_work_note`, `add_comment` | Incident lifecycle, work notes and comments. `create_incident` can link the affected CI and service, and reports an active incident with the same short description on that CI instead of duplicating it. |
| **Problem Management** (6) | `create_problem`, `get_problem`, `list_problems`, `update_problem`, `resolve_problem`, `close_problem` | Problem records, root cause, resolution. |
| **Change Management** (6) | `create_change_request`, `get_change_request`, `list_change_requests`, `update_change_request`, `submit_change_for_approval`, `close_change_request` | Change requests, approval submission, closure. |
| **Tasks & Approvals** (6) | `list_tasks`, `get_task`, `update_task`, `list_my_approvals`, `approve_request`, `reject_request` | Tasks across the task table and approval actions. |
| **CMDB (configuration items)** (7) | `search_cmdb_ci`, `get_cmdb_ci`, `create_cmdb_ci`, `update_cmdb_ci`, `list_ci_relationships`, `create_ci_relationship`, `cmdb_health_dashboard` | CI search/get/create/update with duplicate checks and dry-run, relationships (limit and type filter; creation read back), CMDB health. Multi-level walks: see ITOM topology. |
| **Service Catalog** (5) | `list_catalog_items`, `get_catalog_item`, `order_catalog_item`, `list_sc_requests`, `get_request_item` | Catalog items, ordering, requested items. |
| **Knowledge** (4) | `search_knowledge`, `get_knowledge_article`, `create_knowledge_article`, `retire_knowledge_article` | Knowledge search, articles, retirement. |
| **Users & Groups** (7) | `get_user`, `list_users`, `get_group`, `list_group_members`, `add_user_to_group`, `remove_user_from_group`, `check_user_roles` | Users, groups, memberships, roles. |
| **Scripting** (15) | `get_business_rules`, `get_business_rule`, `update_business_rule`, `set_business_rule_active`, `create_business_rule`, `get_client_scripts`, `get_script_includes`, `get_script_include`, `update_script_include`, `list_fix_scripts`, `get_fix_script`, `create_fix_script`, `update_fix_script`, `list_background_script_history`, `execute_background_script` | Business rules, script includes, fix scripts, client scripts, background scripts. |
| **Script Records (generic)** (5) | `list_script_types`, `list_script_records`, `get_script_record`, `create_script_record`, `update_script_record` | 27 script types by `script_type` (scheduled jobs, ACLs, UI policies, widgets ...). |
| **ATF** (3) | `list_atf_suites`, `list_atf_tests`, `get_atf_test_results` | Automated Test Framework suites, tests, results. |
| **System Properties** (3) | `get_system_property`, `list_system_properties`, `set_system_property` | `sys_properties` read and update. |
| **Update Sets & Apps** (4) | `list_update_sets`, `create_update_set`, `complete_update_set`, `list_custom_applications` | Update sets and scoped applications. |
| **ITOM — capabilities & operations** (5) | `get_itom_capabilities`, `list_mid_servers`, `list_discovery_schedules`, `get_discovery_status`, `list_active_events` | See [IT Operations Management](#-it-operations-management-itom). |
| **ITOM — CMDB topology** (4) | `list_ci_classes`, `get_service_topology`, `get_ci_impact`, `find_unmapped_cis` | See below. |
| **ITOM — Event Management** (9) | `list_events`, `get_event`, `create_event`, `get_alert`, `acknowledge_alert`, `close_alert`, `list_alert_groups`, `create_incident_from_alert`, `get_service_health` | Needs Event Management; see below. |
| **ITOM — services** (1) | `create_application_service` | See below. |
| **ITOM — flows & workflows** (3) | `list_flows`, `list_flow_executions`, `get_flow_execution` | See below. |
| **Health** (2) | `snow_check_connection`, `get_instance_info` | Connection test and instance info. |
| **ITAM — capabilities & reports** (4) | `get_itam_capabilities`, `itam_overview`, `asset_ci_reconciliation_report`, `list_expiring_warranties` | See [IT Asset Management](#-it-asset-management-itam). |
| **ITAM — assets & lifecycle** (8) | `search_assets`, `get_asset`, `get_asset_lifecycle_states`, `create_asset`, `update_asset`, `update_asset_lifecycle`, `assign_asset`, `retire_asset` | See below. |
| **ITAM — models & inventory** (8) | `list_asset_models`, `create_asset_model`, `list_stockrooms`, `create_stockroom`, `get_stockroom_inventory`, `list_transfer_orders`, `transfer_asset`, `adjust_consumable_quantity` | See below. |
| **ITAM — software licenses** (7) | `search_licenses`, `get_license`, `create_license`, `list_license_allocations`, `allocate_license`, `deallocate_license`, `license_allocation_summary` | See below. |
| **ITAM — contracts** (6) | `search_contracts`, `get_contract`, `create_contract`, `update_contract`, `link_asset_to_contract`, `unlink_asset_from_contract` | See below. |
| **ITAM — procurement** (5) | `list_purchase_orders`, `get_purchase_order`, `create_purchase_order`, `order_purchase_order`, `receive_purchase_order_item` | See below. |

---

## 📦 IT Asset Management (ITAM)

Asset records (`alm_asset` and its classes) are kept separate from CMDB configuration items (`cmdb_ci`). The ITAM
tools work on asset records and show the linked CI; ServiceNow's own asset-CI synchronization keeps the two in step.

### What it covers, and what it needs

| Capability | Tools | Needs (instance) |
|---|---|---|
| Hardware asset lifecycle: create, update, repair/maintenance, assign / return / reserve, missing, retire | assets & lifecycle | Asset Management (`com.snc.asset_management`) |
| Product models, stockrooms, stock levels vs stock rules, transfer orders, consumable quantities | models & inventory | Asset Management |
| Software license records, rights, allocations to users / CIs | software licenses | Asset Management |
| Contracts, renewals / expiries, contract-asset coverage | contracts | Contract Management (`com.snc.contract_management`) |
| Purchase orders and receiving into stock (ServiceNow creates the assets) | procurement | Procurement (`com.snc.procurement`) |
| Asset-CI reconciliation, warranty expiry, estate overview (the Workspace landing view via API) | capabilities & reports | Asset Management |

`get_itam_capabilities` checks the plugins, tables and your roles on the connected instance.

**Not implemented — needs licensed products** (the tools never produce these figures):
SAM Professional (normalization, compliance positions, install reconciliation, SaaS license management),
the Hardware Asset Management application (disposal orders / e-waste, reclamation, refresh, model lifecycle,
attestation), Cloud Asset / Cloud Cost Management (ITOM cloud licensing, Discovery, MID Server), and Cost
Management (financial reporting). `license_allocation_summary` reports rights vs recorded allocations only and says
so — it is **not** a compliance position.

### Reference ITAM tool names

| Reference | This server |
|---|---|
| `itam.assets.create` / `get` / `search` / `update` / `retire` | `create_asset`, `get_asset`, `search_assets`, `update_asset` (+ `update_asset_lifecycle`, `assign_asset`), `retire_asset` |
| `itam.cis.create` / `get` / `search` / `update` / `relationships` | `create_cmdb_ci`, `get_cmdb_ci`, `search_cmdb_ci`, `update_cmdb_ci`, `list_ci_relationships` / `create_ci_relationship` |
| `itam.licenses.get` / `search` | `get_license`, `search_licenses` |

### Safety rules (every ITAM tool)

- **Lifecycle states and substates are read live from `sys_choice`** — nothing is hard-coded, and invalid pairs
  (e.g. *In maintenance / In repair* on an instance where *In repair* belongs to *In stock* and *In use*) are refused
  with the allowed values.
- **Roles**: writes need `admin` or the area's role (`asset`, `inventory_admin`, `model_manager`, `sam`,
  `contract_manager`, `procurement_admin` / `procurement_user`); ServiceNow ACLs still apply afterwards.
- **Duplicate protection**: serial + model and asset tag (assets *and* CIs — an existing CI with the same serial is
  linked instead of duplicated), license key, vendor + contract number, stockroom name, model name + manufacturer,
  allocation target, open transfer lines.
- **`dry_run`** on every write; **`confirm=true`** for consequential changes (otherwise the tool returns
  `requires_confirmation` with the reasons).
- **Post-write verification**: each write is read back. For lifecycle-critical fields (state, assignment, quantity,
  PO status) a value the platform did not keep makes the result a failure instead of a silent success.
- **Platform behaviours handled** (found on a live instance): assets on an open transfer order are held
  *In stock / Pending transfer*, so lifecycle, assign and retire refuse until the transfer is completed or cancelled;
  where `proc_po.status` is read-only to the API, `order_purchase_order` returns the purchase order link to use
  ServiceNow's **Order** button instead of reporting a false success.
- **Data minimisation**: cost / accounting fields only with `include_financials=true`; license keys never echoed
  (`include_license_key=true` to read one); assignees omitted from warranty lists unless requested; people are shown
  by display name only.

---

## 🛰️ IT Operations Management (ITOM)

ITOM tools work on operational data: CMDB topology and services, MID Servers, Discovery, alerts, and flow
executions. They stay separate from ITSM and ITAM. They read incidents only to show the impact of a CI, and
`create_incident` (ITSM) can link a CI and a service.

### What it covers, and what it needs

| Capability | Tools | Needs (instance) |
|---|---|---|
| CI classes, multi-level service topology, CI impact (affected services + open incidents), CIs with no relationships | CMDB topology | CMDB (`com.snc.cmdb`) |
| Create a manual application service populated with CIs (Application Service API) | `create_application_service` | Application Service API (`com.snc.cmdb.it_service.api`) |
| Flow Designer flows / subflows, legacy workflows, their executions and errors | flows & workflows | Flow Designer / Workflow (active by default) |
| MID Server status | `list_mid_servers` | `ecc_agent` (base platform); a MID Server installed on a host |
| Discovery runs | `get_discovery_status` | `discovery_status` (base platform; filled by Discovery) |
| Discovery schedules | `list_discovery_schedules` | Discovery (`com.snc.discovery`) |
| Events (send, list, get), alerts (list, get, acknowledge, close), alert groups, incident from an alert, service health from open alerts | Event Management tools, `list_active_events` | Event Management (`com.glide.em`) |

`get_itom_capabilities` reports which of these the connected instance has. A feature counts as available only when
its tables exist. Plugin states from `v_plugin` are opt-in (`include_plugin_state=true`): that virtual table took up
to 60 s on a busy instance. A tool whose plugin is missing returns `PLUGIN_REQUIRED` naming the plugin; it does not
pass on a raw `400 Invalid table`.

### Works on any instance and release (checked on every call)

Every ITOM tool declares what it needs. Before doing anything, it checks the connected instance, and it checks again
on every call: nothing is cached between requests.

| Check | Read from | If it fails |
|---|---|---|
| Table exists | `sys_db_object` | `PLUGIN_REQUIRED`, naming the plugin to activate |
| Fields the tool depends on exist (own or inherited) | `sys_dictionary` | `INCOMPATIBLE_INSTANCE`, naming the field; nothing is written |
| User has one of the roles | `sys_user_has_role` | `FORBIDDEN`, naming the roles |
| State / severity / other choice values | `sys_choice` | Never hard-coded; a value is accepted as label or value |
| Optional fields | `sys_dictionary` | Skipped; listed in `fields_not_on_this_instance` |

So:
- **Instance without the plugin:** a clear "install X" message.
- **Plugin installed later:** works on the next call, with no change and no restart.
- **Another instance or release that has it:** adapts to that instance's tables and choices.

When a user can't read `sys_dictionary` (ACLs can return an empty list), the field check steps aside and ServiceNow
decides at write time.

**Verification status:** the Event Management tools were built from ServiceNow's documented `em_event` / `em_alert`
layout and tested on a simulated instance (plugin missing, plugin present, another release, plugin activated
mid-session, role missing). They have **not yet run on an instance with Event Management**. `get_itom_capabilities`
reports each feature as *verified on a live instance* or *documented layout — not yet verified live*.

**Not implemented** (named by `get_itom_capabilities`, never emulated):
- Running a discovery scan: needs Discovery and a validated MID Server, and ServiceNow has no documented public REST
  call to start a schedule that could be verified.
- Top-down mapping from an entry point: needs Service Mapping and a MID Server.
- Starting or cancelling a flow over REST: needs an IntegrationHub REST trigger or a scripted REST API.
- Anomalies, metric data and capacity forecasts: Metric Intelligence / Health Log Analytics / ITOM Optimization.

There are no `itom:*` OAuth scopes in ServiceNow: access is controlled by the user's roles and ACLs.

### Checklist names → tools

| Checklist | This server |
|---|---|
| `get_ci`, `create_ci`, `update_ci`, `search_ci` | `get_cmdb_ci`, `create_cmdb_ci`, `update_cmdb_ci`, `search_cmdb_ci` |
| `get_ci_relationships`, `list_ci_classes` | `list_ci_relationships` (one level), `get_service_topology` / `get_ci_impact` (multi-level), `list_ci_classes` |
| `get_all_application_service_names` | `search_cmdb_ci(sys_class_name="cmdb_ci_service")`, or `list_ci_classes(root="cmdb_ci_service")` for the classes |
| `get_application_service_topology` | `get_service_topology` |
| `get_server_impact_graph`, `get_services_for_server` | `get_ci_impact` |
| `get_unmapped_topology` | `find_unmapped_cis` |
| `create_top_down_service` | `create_application_service` (manual service; top-down discovery needs Service Mapping) |
| `get_discovery_status`, `list_discovery_schedules` | same names |
| `list_workflows`, `get_workflow_status`, `get_workflow_history` | `list_flows`, `get_flow_execution`, `list_flow_executions` |
| `create_event`, `list_events`, `get_event` | same names |
| `get_alert_list`, `acknowledge_alert`, `close_event` | `list_active_events` / `get_alert`, `acknowledge_alert`, `close_alert` |
| `correlate_events` | `list_alert_groups` (Event Management's correlation result) |
| `get_health_score` | `get_service_health` (worst open alert on the service and its CIs) |
| `create_incident` (from an event) | `create_incident_from_alert` (linked on the alert), or `create_incident` with `cmdb_ci` / `business_service` |

### Safety rules

- **Read-only by default**: 17 of the 22 ITOM tools only read. Graph walks are bounded (depth ≤ 6, ≤ 500 CIs, scans
  ≤ 2000) and say when they were cut off (`truncated`, `complete`).
- **Live choices**: alert state / severity, discovery state, execution state and the service fields
  `busines_criticality`, `environment`, `used_for`, `operational_status` are checked against `sys_choice` of the
  instance; an unknown value is refused with the allowed list.
- **`create_application_service`**:
  - Needs `admin`, `app_service_admin`, `sm_admin`, `itom_admin`, `sn_cmdb_admin` or `sn_cmdb_editor`. ServiceNow
    ACLs still apply.
  - Takes `dry_run`, and needs `confirm=true` to create.
  - Honours `SERVICENOW_WRITE_ENABLED`.
  - Refuses a name already used by any service class: ServiceNow's API would silently update that service instead.
  - Checks every CI reference and that each relation hangs off an entry point.
  - Afterwards it reads the record back and confirms the relations through `getContent`. ServiceNow populates services
    asynchronously, so it retries for about 10 s and reports any relations not yet listed.
  - It also creates a service environment, layers, manual endpoint CIs and the relationships between them.
- **Event Management writes:** `create_event`, `close_alert` and `create_incident_from_alert` need `confirm=true`;
  `acknowledge_alert` takes `dry_run`. All of them are read back after writing. `create_event` refuses an identical
  event still waiting to be processed, and `create_incident_from_alert` returns the alert's active incident instead
  of opening a second one.
- **CMDB CIs** (`create_cmdb_ci`, `update_cmdb_ci`): one implementation shared with the Snow Agent. The class may
  be given by label ('Linux Server'). Reference fields take display values (location 'Pune'). Unknown field names
  are refused instead of being dropped silently.
- **Unmapped CIs** are found by checking relationships in batches. The encoded `RLQUERY` form was tested on a live
  instance and returned CIs that have relationships, so it is not used.

---

## 🧪 Testing

```bash
cd servicenow-mcp
PYTHONPATH="src;tests" python -m pytest tests -q        # Windows; use src:tests on macOS / Linux
```

- `test_server.py`, `test_script_records.py` — registration, resources, prompts, query linting, diffs, dry runs, script records
- `test_itam.py` — all 38 ITAM tools on an in-memory fake instance (`fake_servicenow.py`): validation, live-choice
  lifecycle rules, duplicates, confirmation gates, role checks, privacy, verification failures, CMDB CI hardening
- `test_itom.py` — all 22 ITOM and 4 CMDB CI tools on the same fake instance: the five instance scenarios above
  for Event Management, plugin gating, live-choice validation, topology /
  impact walks and truncation, unmapped CIs, application service dry run / confirmation / duplicates / roles / write
  lock / verification (including late population), flows, the agent backend, and the CMDB / incident extensions
- `test_oauth.py` — OAuth token grant, caching, refresh on 401, client credentials, Basic fallback

The ITAM tools were also exercised end to end on a ServiceNow developer instance with labelled test records
(`ITAM-MCP-TEST…`), which were deleted afterwards. The ITOM read tools were run on a developer instance (Australia
release; Event Management, Discovery and Service Mapping not installed), and `create_application_service` was run
there with a labelled `ITOM-MCP-TEST service`. That service and the 18 records ServiceNow created with it were then
deleted. On that instance every Event Management tool returned the plugin-required message on all three hosts
(servicenow-mcp, the Snow Agent catalog, MCP Chat). Their behaviour *with* the plugin and the Discovery-schedule
path are covered by the fake instance only.

---

## 📜 License

MIT License. Designed for seamless enterprise integration with ServiceNow.
