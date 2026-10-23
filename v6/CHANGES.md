# v5 → v6 changes

## Broken in v5 → fixed
| v5 | v6 |
|---|---|
| Server did not start: `snow_login.py`, `snow_universal.py` missing | Both added; also runs standalone without platform-service |
| UI called 4 endpoints that did not exist (`/extract/universal`, `/stories/approve-and-deploy`, `/health-scan`, `/universal/chat`) | All implemented |
| Connect screen said "Connected" without calling ServiceNow | `/snow/login` checks credentials, roles and plugins |
| Health scan showed hard-coded numbers (74, "142 CIs", "37 P3") when the call failed; API gave a grade even with a wrong password | Fake data removed. 24 live checks; unknown checks not scored; wrong password → 401 |
| Update set created but never made current → changes went to *Default* | Preferences set + new session + `sys_scope` on every record + `sys_update_xml` verification |
| Role URL built as `https://…https://…` → no role created, log said ✅ | Fixed; every step logs its real outcome (created / reused / skipped / warning / failed) |
| Every table forced to extend `task`; `super_class` sent as a name | Parent from the plan, resolved to its sys_id |
| Reference fields had no target; choice fields became integers | Reference target required and checked; choice fields + `sys_choice` rows |
| "Flow deployed" = empty `sys_hub_flow` record | Real flows via the SoftProdigy Flow Server (snow_sdk); otherwise skipped and reported |
| "Now Assist features" = same 3 hard-coded scripts on every table (wrong auto-number) | Removed; only what the plan contains is built |
| Test Plan / Process Flow / Deployment Guide were BRDs | Own templates; unknown doc type → 400 |
| AI outage → docs marked "Ready" containing the error JSON | 503, document marked Failed with a Retry button |
| Word download regenerated the whole document (different content, double AI cost) | Downloads the document shown on screen |
| Stories went to `rm_enhancement` → `idea` → `task` silently | `rm_story` (or `task` when the Agile plugin is missing, reported) |
| Chat "deploy all" skipped the approval dialogs; results mapped by position | Approval per story; results carry an `index` |
| Chat "create incident/problem/change" called a missing endpoint | Calls the real endpoints after a confirmation dialog |

## Security
* Groq API key: hard-coded in `groq_client.py` and always used (as requested); `.env` / environment values are ignored.
* ServiceNow username/password are never fixed: each user signs in on the connect screen and every call uses
  that user's own credentials (the browser remembers URL + username only, never the password).
* TLS certificate verification on (`verify=False` removed); `SNOW_VERIFY_TLS=false` only for labs.
* Optional `AGENT_API_KEY` → `X-API-Key` required on every `/snow/*` call.
* CORS limited to configured origins (default: localhost dev ports), not `*`.
* Hard-coded platform password removed from the UI.
* Server-side ServiceNow credentials only used when `SNOW_ALLOW_ENV_CREDS=true`.
* Natural-language queries limited to operational tables.
* `approve-and-deploy` requires `approved=true`.

## New
* Build App screen: plan → validate → scope/update set choice → build → capture proof → complete update set.
* Update Sets screen + API: list, make current, view captured changes, complete (refuses empty), export link.
* Plan validator: names, parent tables, reference targets, JS syntax (`node --check` when available),
  `current.update()` in before/after rules, GlideRecord in client scripts, flows without actions.
* Rollback of a failed run; re-runs reuse existing records instead of duplicating them.
* Universal extraction: DOCX tables, XLSX, CSV, audio (Whisper), images (vision), long inputs chunked.
* Problems, knowledge, approvals, service catalog, ATF run, Jira push endpoints.

## v6.1
* **All 522 SoftProdigy ServiceNow tools** (the platform's now_mcp library, 18 role bundles) run inside the agent with
  the signed-in user's credentials: catalog, run (writes need confirmation), plain-English tool picker.
* **Operations screen**: My Approvals, Service Catalog ordering, Knowledge search/create, All tools with forms.
* Chat: requests that are not covered by the built-in intents are matched to a tool.
* **Story spreadsheets imported exactly as written** (IDs, summaries, descriptions, every acceptance criterion);
  long AI inputs are now split on line boundaries, never mid-story.
* Excel export matches the 4-column sample (User Story ID | Summary | Description | Acceptance Criteria);
  Word stories document gets an overview table; original IDs kept everywhere.
* **TSD architecture diagram drawn as an image** from AI-produced layers/components/flows.
* Groq key hard-coded and always used; ServiceNow login is per user.
* User Stories & Acceptance Criteria can be downloaded (Word or Excel) straight from the Review step and from chat,
  without generating the other documents first. Chat treats pasted meeting notes as stories to extract.
* No limit on the number of stories: 1, 3 or 150+ — one Excel row / Word section per story. AI extraction reads up
  to 300,000 characters (AGENT_MAX_INPUT_CHARS), in 6,000-character pieces; an answer that comes back cut off is
  detected and that piece is split and re-asked, so stories are never silently dropped.
* Login asks only for username and password. The instance URL is entered once on the login page and remembered
  (or fixed for everyone with SNOW_INSTANCE_URL in .env); the agent access-key box is gone from the login screen.
* `start_agent.bat` (one-click install + start + open browser). The CMD window only starts the agent and shows a
  clear "RUNNING" block with the browser address, instance, tools loaded and an AI (Groq) check.

## v6.2 — flows & subflows in Build App
* Build plans get `subflows`; flows can call one (`subflow`, `subflow_inputs`). The Groq planner produces them.
* New Flow Designer browser engine (`flow_agent/browser.py`, ported from the standalone now2.py agent): builds subflows
  (inputs/outputs, actions, publish) and flows (trigger, actions, subflow call, activate) as the signed-in user, inside
  the build's application scope and update set. Pauses appear on the Build App screen instead of the terminal.
* Subflow library (`flow_agent/kb.py`, from now3.py): published subflows are remembered per instance and reused by
  later builds when they are similar, contain every needed action and still exist in the instance.
* Removed from the ported code: hard-coded credentials, the cached session file, the NVIDIA parser (Groq plans
  instead) and the "publish" that PATCHed `sys_hub_flow.state` without publishing.
* Flow Server path unchanged for plain flows when `FLOW_SERVER_URL` is set.

## v6.3 — automatic flows & subflows
* The Generic Flow Server is bundled as `v6/flow-server` and started by `start_agent.bat` (port 3000).
* Build App builds every subflow and flow automatically through it (ServiceNow SDK); the Flow Designer browser is only
  a per-item fallback when an automatic build fails, and the log says why.
* Flow Server: new `subflow.call` step (`wfa.subflow` by sys_id) so a flow calls its subflow; one SDK workspace per
  application (an install no longer deletes the app's earlier flows/subflows); a failed build/install no longer leaves a
  broken file behind; flows are activated after a normal install too; each subflow has its own outputs id.
* Plan → Flow Server conversion (`flow_agent/server_specs.py`): action ids namespaced per flow/subflow, subflow actions
  target the record input instead of a trigger, approver groups/emails resolved to sys_ids, createTask/createRecord inputs
  mapped. Planner prompt uses `{{trigger.current.*}}` / `{{input.*}}` data pills.

## IT Operations Management (ITOM)
* New `servicenow-mcp/src/snow_itom` package (13 tools), registered in servicenow-mcp (`tools/itom.py`) and in the
  agent (`snow-ai-service/itom_bridge.py`: tool catalog package `itom` and the agent's MCP server), the same way as ITAM.
* Fixed: `list_discovery_schedules` and `list_active_events` returned `400 Invalid table` on instances without
  Discovery / Event Management. They now say which plugin is needed. Filters are checked against the instance's own
  choice values.
* New: `get_itom_capabilities`, `get_discovery_status`, `list_ci_classes`, `get_service_topology`, `get_ci_impact`,
  `find_unmapped_cis`, `create_application_service` (Application Service API; dry run, confirm, duplicate refusal,
  verified via getContent), `list_flows`, `list_flow_executions`, `get_flow_execution`.
* CMDB: `list_ci_relationships` takes `limit` and `relationship_type`; `create_ci_relationship` reads the relationship
  back; `cmdb_health_dashboard` no longer always reports "Healthy".
* Incidents: `create_incident` can link `cmdb_ci` / `business_service` (validated), and returns the existing active
  incident instead of a duplicate unless `allow_duplicate=true`.
* Not implemented, reported by `get_itom_capabilities`: Event Management actions, running Discovery, Service Mapping,
  starting / cancelling flows over REST, health / AIOps metrics.

## Instance- and release-agnostic tools, Event Management, shared CMDB, connection fixes
* Every ITOM tool checks the connected instance on each call: tables (`PLUGIN_REQUIRED`, names the plugin), fields
  (`INCOMPATIBLE_INSTANCE`, names the field), roles (`FORBIDDEN`, names the roles). Choice values are always read live.
  A plugin activated later works on the next call, with no restart.
* New Event Management tools (built from the documented layout; not yet verified on an instance with the plugin):
  `list_events`, `get_event`, `create_event`, `get_alert`, `acknowledge_alert`, `close_alert`, `list_alert_groups`,
  `create_incident_from_alert`, `get_service_health`.
* `search_cmdb_ci` / `get_cmdb_ci` / `create_cmdb_ci` / `update_cmdb_ci` moved to `snow_itom/cmdb.py`. servicenow-mcp
  and the Snow Agent (AI Agent, MCP Chat) now run the same code. A class can be given by label, reference fields by
  name, and unknown field names are refused.
* The agent's tool picker weights words by how rare they are, handles plurals and "unmapped", knows CI = CMDB, asks for
  missing values instead of reporting "no match", dry-runs writes before asking to confirm, and says "no tool" for
  instance requests it cannot do (general questions still get the AI answer).
* Vendored tools: a failure naming a table that doesn't exist on the instance becomes "plugin required"; a near
  name (e.g. pa_indicator vs pa_indicators) is reported as a tool defect, not a plugin.
* Fixed vendored tools found by a live audit of all 498 agent tools:
  * `request_tools.py` had 14 helpers with their code stripped (only docstrings left), so `list_requests`,
    `list_requested_items`, `list_catalog_tasks`, `list_request_approvals` and the request lookups crashed. Restored.
  * `problem_tools.py` had 2 stripped helpers (problem tasks / incidents of a problem). Restored.
  * Wrong table names: `sysrule_approval`, `pa_dashboard` and `pa_indicator` corrected to `sysrule_approvals`,
    `pa_dashboards` and `pa_indicators`.
* MCP error text from servicenow-mcp's ITOM / CMDB / ITAM tools now includes what to do (e.g. the plugin to install).

## Requirement-driven implementation
* New `requirement_pipeline.py`: understand → validate against the instance → targeted inspection → behaviour-based
  comparison (not by name) → plan → confirmed deploy → read-back verification → report. Missing groups, values, fields
  or tables become questions; conflicts and overlaps become decisions; unavailable mechanisms are blocked, not swapped.
* `sn_build.Builder`: data policies (+ rules), assignment rules, and changes to existing records (old values kept and
  restored on rollback, checked for update-set capture).
* REST `/snow/requirements/{plan,deploy,history}`; MCP tools `snow_plan_requirement`, `snow_implement_requirement`;
  MCP Chat and Implement show the report with **Confirm & build**. approve-and-deploy plans stories when
  `pipeline=true` (the UI); without it the previous behaviour is unchanged.
* Fixed: onChange client scripts were written to `field_name` instead of `field` (they never fired); the Build App
  screen rendered empty (a second `renderBuild` replaced it); `snow_generate_business_rule` deployed rules without
  `advanced=true` (script ignored), without a duplicate check or read-back.
* Tests: `scripting_fixes_test.py`, `requirement_pipeline_test.py` (in-memory instances, `fake_instance.py`),
  `requirement_wiring_test.py`; offline audit of the vendored scripting tools: `vendored_scripting_audit.py`.

## Safety follow-ups
* AI Agent page single-story workflow plans through the requirement pipeline (same Confirm & build as Implement).
* approve-and-deploy no longer guesses assignment groups from the module (`_MODULE_GROUPS` and the name-prefix match
  were removed). A group is used only when given (story, `group_mapping[module]`, or request) and it matches exactly one
  active group; otherwise the story is not created and a question is returned (`needs_input`).
* Vendored scripting write paths fixed or disabled (see README "Vendored scripting tools").
* Login: a 401 "Required to provide Auth information" names both causes (wrong credentials or Basic auth refused)
  instead of only the OAuth one. The login form shows the agent's instance instead of an input, and non-default ports
  (e.g. :9443) are kept.
* Tests updated to the current UI (4 documents, live tool count, ACL "not created" warning).

## AI Agent chat: configuration artifacts are built, never filed as incidents
* `detectIntent`: a request naming a Script Include, Client Script, Business Rule, UI Policy or Data Policy (and not
  phrased as a question / look-up) is `build_artifact` — checked before the long-text, look-up and incident rules, so
  words like "incident", "error", "alert" or "down" no longer route it to Incident creation. It is planned with
  `/snow/requirements/plan` (read-only) and built only after **Confirm & build**. The old chat Business Rule handler
  (which reported update-set capture without checking it) is replaced by this flow; "list business rules" is unchanged.
* `detectStoryType` / Implement deploy and the approve-and-deploy backstop: a story that names a configuration artifact
  is a story (planned), never an incident / problem / change (`retyped_from` reports an override).
* Requirement pipeline, Script Includes: the class name you write wins over the AI's; a missing name, missing behaviour,
  invalid name or unknown application scope becomes a question; client callable only from explicit wording (default
  not callable, stated as an assumption); optional application scope (`x_…`, must exist in sys_scope) else global;
  Script-Include-only requests need no table.
* Tests: `tests/ai_agent_artifact_test.py` (pipeline, backstop and browser routing).
