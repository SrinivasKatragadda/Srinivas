# ServiceNow Automation Agent

Local ServiceNow automation with a browser UI, a FastAPI service, and a Streamable HTTP MCP server. It can manage ServiceNow records, update sets, scoped-app artifacts, knowledge, catalog operations, health checks, and AI-assisted build/document workflows.

The project keeps the main UI/API and MCP endpoint local by default:

| Service | Address | Purpose |
|---|---|---|
| Agent UI and REST API | `http://127.0.0.1:8001/ui` | Browser interface and automation API |
| MCP server | `http://127.0.0.1:8002/mcp` | ServiceNow MCP tools and MCP Chat bridge |

## Prerequisites

- Windows 10/11
- Python 3.11 or later, installed with **Add python.exe to PATH** selected
- A ServiceNow instance reachable from this computer
- A ServiceNow account allowed to use the REST/Table API
- A Groq API key only if AI planning, document generation, or MCP Chat answers are required

The application connects to ServiceNow through HTTPS. Make sure your firewall, VPN, proxy, and ServiceNow IP-access rules permit outbound access to `<your-instance>.service-now.com:443`.

## Quick start

1. Open `snow-ai-service\\.env` in a text editor.
2. Set the required values described in [Environment configuration](#environment-configuration).
3. Double-click [start_agent.bat](start_agent.bat), or run it from Command Prompt:

   ```bat
   cd C:\path\to\snow-agent-v11\v6
   start_agent.bat
   ```

4. Open `http://127.0.0.1:8001/ui` in a browser.
5. Use **Connection** to confirm the account can access the instance, then use Operations, Build App, Update Sets, or MCP Chat.

On the first start, the script creates `snow-ai-service\\.venv` and installs dependencies. It starts the main API on port 8001 and the MCP server on port 8002.

## Environment configuration

The required local configuration file is [snow-ai-service/.env](snow-ai-service/.env). Use [snow-ai-service/.env.example](snow-ai-service/.env.example) as the safe reference template.

`SNOW_INSTANCE_URL` is the **only canonical ServiceNow URL variable**. Do not add or use `SNOW_URL`.

```env
# Required for shared ServiceNow automation and the local MCP server
SNOW_INSTANCE_URL=https://your-instance.service-now.com
SNOW_ALLOW_ENV_CREDS=true
SNOW_USER=your_service_now_username
SNOW_PASS="your_service_now_password"

# Required for AI-assisted planning, document generation, and MCP Chat answers
GROQ_API_KEY=your_groq_key

# Local service bindings (keep these local unless a secured deployment is intended)
AGENT_HOST=127.0.0.1
AGENT_PORT=8001
MCP_HOST=127.0.0.1
MCP_PORT=8002
MCP_TRANSPORT=streamable-http
MCP_URL=http://127.0.0.1:8002/mcp
```

### Configuration reference

| Variable | Required | Description |
|---|---:|---|
| `SNOW_INSTANCE_URL` | Yes for ServiceNow operations | Full instance URL, e.g. `https://dev12345.service-now.com`. No trailing slash is needed. |
| `SNOW_ALLOW_ENV_CREDS` | Yes for shared credentials | Set to `true` to let the API and MCP server use `SNOW_USER` and `SNOW_PASS`. |
| `SNOW_USER` | Yes for shared credentials | ServiceNow integration username. |
| `SNOW_PASS` | Yes for shared credentials | ServiceNow integration password. Quote it when it contains `#`, spaces, semicolons, or other shell-sensitive characters. |
| `SNOW_VERIFY_TLS` | Recommended | Leave `true`. Set `false` only for a temporary lab instance with a known self-signed certificate. |
| `GROQ_API_KEY` | AI features only | Enables AI planning, document generation, and MCP Chat. A missing key does not stop normal REST automation. |
| `AGENT_HOST` / `AGENT_PORT` | No | Main API bind address and port; defaults to `127.0.0.1:8001`. |
| `MCP_HOST` / `MCP_PORT` | No | MCP server bind address and port; defaults to `127.0.0.1:8002`. |
| `MCP_URL` | No | MCP endpoint used by the UI chatbot; default is `http://127.0.0.1:8002/mcp`. |
| `AGENT_API_KEY` | Recommended beyond localhost | Protects `/snow/*` API calls using the `X-API-Key` header. |
| `MCP_API_KEY` | Recommended beyond localhost | Protects MCP HTTP requests using the `x-api-key` header. |
| `AGENT_ALLOWED_ORIGINS` | No | Comma-separated CORS allow-list. Keep the default local-only behavior unless deliberately hosting the service. |
| `FLOW_SERVER_URL` | No | Flow Server address. Default `http://127.0.0.1:3000` (the bundled `v6/flow-server`). |
| `FLOW_ENGINE` | No | `auto` (default: Flow Server, browser fallback per item), `server` or `browser`. |
| `FLOW_BROWSER_HEADLESS` | No | `true` hides the Flow Designer browser (and turns off the step pauses). Default `false`. |
| `FLOW_PAUSE_TIMEOUT` | No | Seconds a paused build waits for an answer on the Build App screen. Default `1800`. |
| `FLOW_KB_THRESHOLD` | No | Similarity (0–1) a subflow in the subflow library needs to be reused. Default `0.45`. |

Never commit `.env`, passwords, API keys, or copied request headers. Rotate a key immediately if it is pasted into a ticket, chat, terminal recording, or repository.

## ServiceNow account requirements

Use a dedicated integration account where possible. It needs the roles and ACL access appropriate for the tables and artifacts you intend to manage. Typical capabilities include access to Incident, Problem, Change, Knowledge, Catalog, `sys_update_set`, and scoped-app tables.

For application development tasks, extra privileges may be required. In particular, ServiceNow `security_admin` elevation cannot be performed via this REST client; configure ACLs manually through an appropriately elevated ServiceNow session when required.

### Required role: `snc_basic_auth_api_access`

The integration account **must have the `snc_basic_auth_api_access` role**. Without it, ServiceNow refuses Basic authentication for REST API calls and the UI reports "ServiceNow is refusing Basic authentication for its REST API". Grant it under User Administration > Users > (account) > Roles. Alternatively, configure an OAuth client and set `SNOW_CLIENT_ID` / `SNOW_CLIENT_SECRET` in `.env`.

### Verify credentials before using the agent

Sign in to the configured instance with the same account in a normal browser first. Then confirm:

1. The account is active and not locked.
2. The password is current.
3. Basic/REST authentication is allowed by your instance policy.
4. The account has the target table ACLs and required roles.
5. Any VPN, SSO, proxy, MFA, or IP restriction permits API access for this computer.

The agent deliberately reports ServiceNow authentication failures as HTTP `401` and authorization/ACL failures as `403`; these are different from a network connection failure.

## Flows and subflows in Build App

A build plan can contain `subflows` (inputs, outputs and Flow Designer actions) and `flows` (a record trigger, actions,
and optionally `"subflow": "<subflow id>"` to call one). The AI planner produces both. Build App builds them last:
**subflows first**, then the flows that call them.

**Automatic first, browser only as a fallback.** Each item is built automatically by the **Flow Server**
(`v6/flow-server`, started by `start_agent.bat` on `127.0.0.1:3000`). It turns the item into ServiceNow SDK code
(Fluent), builds it, installs it into the build's application and checks it in `sys_hub_flow`. A flow calls its subflow
by sys_id, and data pills such as `{{trigger.current.number}}` and `{{input.record_id}}` are generated, so nothing has to
be clicked. Only when the automatic build of an item fails is **that item** built in the Flow Designer browser
(Chromium, Playwright), with pauses on the Build App screen — so a browser window means the automatic route failed for
it, and the log says why. Each result says how it was built.

Before building, a subflow with the same name, or a similar published subflow from the subflow library
(`jobs/subflow_kb.json`) that has every action the new one needs and still exists, is reused instead.

Notes:
* SDK-installed flows and subflows travel with the application package; they are **not captured in update sets**.
  Browser-built ones, and all tables, roles and fields, are.
* The Flow Server keeps one SDK workspace per application (`flow-server/.workspaces/`), so installing a new flow never
  removes a flow or subflow built earlier for the same app.
* Publishing/activation is verified in `sys_hub_flow`; anything not published/active is reported as such, never as done.
  When no engine is available, flows are skipped, not faked.

API: `POST /snow/build/{job_id}/respond` answers a browser pause, `GET /snow/flows/engines` reports the engines,
`POST /snow/flows/subflow-library` lists the remembered subflows for an instance and
`POST /snow/flows/subflow-library/remove` forgets one.

## Starting services manually

Use two PowerShell or Command Prompt windows when you want to run the services separately.

### Main UI/API

```bat
cd C:\path\to\snow-agent-v11\v6\snow-ai-service
.venv\Scripts\activate
python api.py
```

Open `http://127.0.0.1:8001/ui`.

### MCP server

```bat
cd C:\path\to\snow-agent-v11\v6\snow-ai-service
start_mcp.bat
```

The MCP endpoint is `http://127.0.0.1:8002/mcp` and uses the Streamable HTTP transport.

### Connect an external MCP client

Configure an MCP client with the endpoint above. The server can take ServiceNow credentials from either:

1. `SNOW_INSTANCE_URL`, `SNOW_USER`, and `SNOW_PASS` in `.env` when `SNOW_ALLOW_ENV_CREDS=true`; or
2. Per-request headers: `x-servicenow-instance-url`, `x-servicenow-username`, and `x-servicenow-password`.

If `MCP_API_KEY` is set, clients must also send `x-api-key`. Prefer environment credentials for a local trusted setup; use an MCP key and TLS-protected deployment when remote clients need access.

## Browser UI

The UI is served by the API at `/ui`; do not start a second static web server for normal use.

| Screen | What it does |
|---|---|
| Connection / Settings | Shows configured connection state and API configuration. |
| Operations | ServiceNow operational tasks, approvals, catalog, knowledge, and the available tool catalog. |
| Implement | Extracts requirements, creates user stories, generates project documents, and supports approved record creation. |
| Build App | Validates and deploys a ServiceNow application plan with scope and update-set handling, including Flow Designer subflows and flows (see below). |
| Update Sets | Lists, changes context, inspects captured changes, and completes update sets. |
| Health Scan | Performs live, read-only instance checks. |
| MCP Chat | Works through the local MCP server: look-ups run at once, changes are shown for confirmation first; questions are answered by `snow_ai_query`. |
| Activity Log | Displays actions and responses recorded by the UI. |

### MCP Chat

Open **MCP Chat** from the left navigation. The browser sends the message (with the signed-in user's connection) to `POST /snow/mcp/chat`; the API opens an MCP session with the local server, passing that user's ServiceNow credentials as request headers, and:

1. lists the MCP tools and lets the AI pick the one that fits the request;
2. runs **read** tools immediately and shows the result;
3. returns **write** tools as a proposal — the UI shows the exact tool and arguments, and the tool runs only after you confirm (ITAM and ITOM tools then receive `confirm=true`);
4. answers questions, explanations and code requests with `snow_ai_query` when no tool fits.

When the chosen tool needs a ServiceNow plugin the instance does not have (for example Event Management for alerts),
the chat says so and names the plugin to activate. It does not fall back to a general answer. After you activate the
plugin, the same request works; nothing needs restarting.

The browser never receives `MCP_API_KEY`. Every tool runs as the signed-in user, so ServiceNow roles and ACLs apply.

For a successful response, all of the following must be available:

1. Main API listening on port 8001.
2. MCP server listening on port 8002.
3. Valid ServiceNow credentials if the requested answer needs instance data.
4. A valid `GROQ_API_KEY`.

## Requirement pipeline (plan → confirm → deploy → verify)

A configuration request or user story ("make urgency mandatory on the incident form and for API-created records",
"assign network incidents to Network Support") is handled by `snow-ai-service/requirement_pipeline.py`:

1. **Understand.** The AI extracts a structured requirement. The agent then checks the table, fields, choice values
   and referenced groups/users on the live instance. Anything missing or ambiguous becomes a question, never a guess.
2. **Inspect.** Targeted reads of the business rules, client scripts, UI policies, data policies, assignment rules
   (and the priority lookup when relevant) on that table and its parents, plus earlier requirements on this instance.
3. **Compare.** The agent compares behaviour (field + effect + value + condition), not names. Only an exact declarative
   match, or a before rule whose whole script sets the same literal value, counts as "already satisfied". A script
   that touches the same field, a conflicting state, or an inactive exact match needs your decision.
4. **Plan.** Reuse / create / modify (only after you decide) / blocked (the plugin, column or permission is missing:
   it is never swapped for another mechanism) / skip.
5. **Deploy.** Only with `confirm: true`. The agent re-reads the instance first; if anything relevant changed, you get
   a new plan to confirm instead. Changes are built in an update set; on failure, created records are removed and
   changed records get their old values back.
6. **Verify.** Every record written is read back and compared with what was sent; duplicates are counted. Runtime
   behaviour is **not** exercised and is reported as "not run".

| Method and path | What it does |
|---|---|
| `POST /snow/requirements/plan` | `text` or `requirement` → plan + report (writes nothing) |
| `POST /snow/requirements/deploy` | `plan_id`, `confirm: true`, optional `decisions` `{item_id: create\|reuse\|modify\|skip}` |
| `POST /snow/requirements/history` | Requirements implemented on the signed-in instance |

MCP Chat uses the same pipeline through `snow_plan_requirement` (read-only) and `snow_implement_requirement` (runs only
after you confirm). On the Implement screen, configuration stories are planned when deployed and built only after
**Confirm & build**. Plans, history and instance profiles are kept per instance (`jobs/requirements/<instance>/`).
Flow Designer flows on the table are not inspected yet (the report says so).

### Capability matrix (requirement pipeline)

"Tested offline" means against the in-memory / mock instances in `tests/`; it does not prove behaviour on your instance.

| Mechanism | What the pipeline does | Tested offline | Not supported / gaps |
|---|---|---|---|
| Business rules | Create (literal set-value rules by code; custom logic written by the AI, then validated); reuse only a provably equal literal-only before rule; activate an inactive exact match after your decision | Yes | Editing or merging existing scripts; rules are never fired to test them |
| Client scripts | Create onLoad / onChange / onSubmit (AI-written, validated: no GlideRecord / gs on the client); detects `g_form` effects of existing scripts | Yes | Modifying existing scripts; browser behaviour is not tested |
| Script includes | Create only when a reusable class is explicitly asked for; a name collision becomes a decision | Yes | No equivalence check of existing includes; client-callable ACLs not created |
| UI policies | Create with actions; reuse an exact match; change one action's state or activate after your decision | Yes | UI policy scripts are only scanned, not created |
| Data policies | Create with rules; "form + server" = one policy used as a UI policy too; adds only the missing side | Yes | — |
| Assignment rules | Create with a group resolved by exact name; reuse/conflict by behaviour | Yes | Column names are checked on each instance before use (not in the field registry); blocked if missing |
| Priority | Reads the Priority Lookup (`dl_u_priority`); changes a row only after your decision | Yes | Creating lookup rows |
| UI actions, ACLs, notifications, flows | Not planned by the pipeline (flows / ACLs / notifications stay in Build App) | — | Flows on the table are not inspected |
| Runtime testing | Reported as "not run" for every mechanism | — | Needs test records in a dev instance |

### Vendored scripting tools (AI Agent / Operations catalog)

* `create_server_side_script` only writes to server-side script tables; `table=incident` (or an unknown `script_type`)
  is refused; a business rule needs `fields.collection` and gets `advanced=true` with a script.
* `create_business_rule` / `update_business_rule`: `advanced=true` whenever a script is given, `when` validated, a rule
  that would never run is refused, no `sys_class_name` / `action_display`.
* `POST /snow/tools/run` refuses a duplicate (same name and table) for the scripting create tools unless
  `allow_duplicate=true`, and reads the created record back (`verification` in the result). Writes still need
  `confirm=true`.
* `execute_background_script` is disabled unless `AGENT_ALLOW_BACKGROUND_SCRIPTS=true` (it runs arbitrary server code
  through a UI page whose HTTP 200 does not prove the script ran).
* The raw `/sn/tools/*` routes refuse scripting writes (403) — they have no confirmation, duplicate check or read-back.
* Not yet: read-back after vendored **update** tools (they report ServiceNow's response, not an independent read).

## Universal artifact API

For supported ServiceNow tables outside the named UI workflows, use the universal artifact endpoints. The current account must have ServiceNow ACL permission for the requested table.

| Method and path | Operation | Confirmation |
|---|---|---|
| `POST /snow/artifacts/schema` | Read a table's schema | Not required |
| `POST /snow/artifacts/list` | List records | Not required |
| `POST /snow/artifacts/get` | Read one record | Not required |
| `POST /snow/artifacts/create` | Insert record | `confirm: true` required |
| `POST /snow/artifacts/update` | Update record | `confirm: true` required |
| `POST /snow/artifacts/delete` | Delete record | `confirm: true` and a non-empty `reason` required |

The service validates table and field names, rejects encoded-query `javascript:` injection, limits request sizes, and blocks writes to ServiceNow-managed audit fields.

Example: create a configuration artifact in a chosen update-set context.

```json
{
  "table": "sys_script_include",
  "fields": {
    "name": "AcmeHelper",
    "api_name": "x_acme_app.AcmeHelper",
    "script": "var AcmeHelper = Class.create();"
  },
  "confirm": true,
  "context": {
    "scope_mode": "existing",
    "application": "x_acme_app",
    "update_set_name": "ACME_2026_10"
  }
}
```

Configuration metadata can be captured in update sets. Transactional records such as incidents and catalog requests generally are not update-set artifacts. After a configuration change, inspect the Update Sets screen or `/snow/updatesets/changes` to verify capture.

## MCP tool coverage

The MCP server exposes tools for:

- Connection checks and health scans
- Incidents, problems, and changes
- Knowledge search and article creation
- User lookup, creation, update, deactivation, and group assignment
- Update-set listing
- Business-rule generation
- AI ServiceNow questions and the available-tool catalog
- **IT Asset Management (38 tools)** — assets and lifecycle, models, stockrooms, transfers, consumables, software
  licenses and allocations, contracts and renewals, purchase orders and receiving, asset-CI reconciliation,
  warranties and an estate overview

The ITAM tools are the same implementation as in `servicenow-mcp` (package `servicenow-mcp/src/snow_itam`, loaded by
`snow-ai-service/itam_bridge.py`). They are also in the AI Agent / Operations tool catalog, where they replace the
older vendored asset tools (those had lifecycle state values that do not exist on ServiceNow, e.g. *Reserved* as a
state; their `/assets/*` REST routes remain, with the values corrected). Lifecycle states are validated live against
the instance; consequential changes need confirmation; every write is verified by reading it back. Features that need
licensed products (SAM Professional, the Hardware Asset Management application, Cloud / Cost Management) are reported
by `get_itam_capabilities`, never emulated. See `servicenow-mcp/README.md` for the full ITAM reference.

- **IT Operations Management (22 tools)** — capabilities, MID Servers, Discovery status and schedules, Event
  Management (events, alerts, acknowledge, close, alert groups, incident from an alert, service health), CI classes,
  service topology, CI impact, unmapped CIs, application service creation, flow / workflow executions
- **CMDB configuration items (4 tools)** — `search_cmdb_ci`, `get_cmdb_ci`, `create_cmdb_ci`, `update_cmdb_ci`, the
  same implementation as servicenow-mcp (duplicate checks, dry run, read-back; class and references by name)

The ITOM and CMDB tools come from `servicenow-mcp/src/snow_itom`, loaded by `snow-ai-service/itom_bridge.py` in the
same way as ITAM. They are in the AI Agent / Operations tool catalog as the `itom` and `cmdb` packages, and on the
agent's MCP server.

**What you see when the instance lacks something** (AI Agent, MCP Chat and Operations):
- A missing plugin, a missing role, or tables that differ from the documented layout get a clear notice saying what
  to install or ask for. This covers every tool: for vendored tools, a failure naming a table that doesn't exist on
  the instance becomes "plugin required".
- Tools check the instance on every call, so activating the plugin later works without a restart.
- Write tools that support `dry_run` are dry-run before you are asked to confirm, so a change that cannot work is
  explained instead of proposed.
- A request about your instance that no tool can do gets "no tool can do this yet", not a general AI answer.

See `servicenow-mcp/README.md` for the full ITOM reference.

Many tools mutate ServiceNow data. Confirm the intended instance, account, target record, and change-management process before invoking them.

## API documentation

With the API running, open:

- Interactive OpenAPI UI: `http://127.0.0.1:8001/docs`
- OpenAPI JSON: `http://127.0.0.1:8001/openapi.json`

If `AGENT_API_KEY` is configured, include it as `X-API-Key` in requests. API write operations retain their endpoint-specific confirmation requirements.

## Troubleshooting

### `Cannot reach https://...` / `HTTPSConnectionPool` / `Max retries exceeded`

This is a network-layer error, occurring before ServiceNow checks the username or password.

1. Confirm `SNOW_INSTANCE_URL` starts with `https://` and contains only the instance base URL.
2. Open the instance URL in a browser from the same machine.
3. Connect the required VPN, or configure the corporate proxy/firewall to permit outbound TCP 443.
4. Check ServiceNow IP restrictions, proxy rules, and DNS resolution.
5. Keep `SNOW_VERIFY_TLS=true`; do not disable certificate validation to work around a connection issue.

### `401 User is not authenticated`

The ServiceNow instance is reachable, but the credentials are rejected.

1. Re-enter `SNOW_USER` and `SNOW_PASS` in `.env`.
2. Quote the password if it includes `#`, spaces, or semicolons.
3. Verify the account can sign in to the same instance in a browser and is not locked.
4. Confirm the account has the `snc_basic_auth_api_access` role (see ServiceNow account requirements) and that ServiceNow permits the selected authentication method for REST/Table API calls.
5. Restart the main API and MCP server after changing `.env`.

### `403 Forbidden`

The account authenticated but lacks the target role, table ACL, application scope, or required elevated permission. Grant only the minimum needed ServiceNow role/ACL, then retry.

### `No Groq API key available`

Add a valid `GROQ_API_KEY` to `.env`, then restart the main API and MCP server. Normal non-AI ServiceNow REST operations can still work without it.

### `WinError 10048` / port already in use

Another process is already listening on port 8001 or 8002. In PowerShell, identify it:

```powershell
netstat -ano | Select-String '127\.0\.0\.1:(8001|8002)'
```

If it is an old Snow Agent process, stop that exact PID, then start the service again:

```powershell
Stop-Process -Id <PID>
```

Do not terminate an unknown process just because it uses the port; first verify its identity with `Get-Process -Id <PID>`.

### UI works but MCP Chat fails

Check both listeners:

```powershell
netstat -ano | Select-String '127\.0\.0\.1:(8001|8002)'
```

Then verify `MCP_URL` is the local endpoint, `MCP_API_KEY` matches on both sides when used, and `GROQ_API_KEY` is present.

## Security and deployment

- Bind to `127.0.0.1` for a personal workstation. This is the default.
- Do not expose ports 8001 or 8002 directly to the internet.
- If remote access is necessary, place the services behind a managed HTTPS reverse proxy, restrict origins and firewall rules, and set strong `AGENT_API_KEY` and `MCP_API_KEY` values.
- Use least-privilege ServiceNow integration accounts and separate accounts by environment.
- Treat change operations as production changes: use update sets where applicable, approvals, and a test/PDI instance before production.

## Testing

Run the local automated suite from the repository root after activating or directly using the project virtual environment:

```powershell
snow-ai-service\.venv\Scripts\python.exe tests\run_all.py --no-ui
```

The suite uses mock services; it does not validate your live ServiceNow permissions. Before production use, validate required operations against a non-production ServiceNow instance.
