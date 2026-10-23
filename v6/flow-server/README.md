# ServiceNow Generic Flow Server

Local Fastify service that generates ServiceNow Fluent Flow API TypeScript from a generic JSON flow specification, builds it with the ServiceNow SDK, installs it into an existing ServiceNow instance, and verifies the resulting Flow Designer flow.

This is a local deployment service. Bind it to `127.0.0.1` and do not expose it to the internet. Requests contain ServiceNow credentials, and the service intentionally avoids logging or persisting passwords.

## What It Does

- Accepts an existing ServiceNow instance URL, credentials, scope ID, scope name, and generic `flowSpec`.
- Writes `now.config.json` for that scope.
- Writes `src/fluent/generated_flow.now.ts`.
- Runs `npx @servicenow/sdk build`.
- Runs `npx @servicenow/sdk install --reinstall false --open-browser false`.
- Verifies the flow by reading `/api/now/table/sys_hub_flow?sysparm_query=name=<flow name>`.

It uses the Fluent Flow API in generated source. It does not insert into `sys_hub_flow`, `sys_hub_flow_version`, `sys_hub_trigger_instance`, `sys_hub_action_instance`, `sys_variable_value`, `sys_element_mapping`, or `sys_hub_flow_snapshot`.

With SDK 4.7, `now.config.json` requires `scope`, `name`, and a 32-character `scopeId`. If the API request sends a technical scope such as `x_hmz_flowauto` in `scopeId`, the server resolves the actual `sys_scope.sys_id` before build. For global flow deployment, send `scopeId: "global"` or `scopeName: "Global"`; the server writes a global application package with `scope: "global"` and a deterministic 32-character app sys_id. It does not use the global configuration XML upload path because this instance rejects generated Flow XML through `api/fluent/load/global`.

## Install And Run

```bash
npm install
npm run dev
```

The server binds to `127.0.0.1:3000` by default.

On Windows PowerShell, if script execution policy blocks `npm.ps1`, use `npm.cmd install` and `npm.cmd run dev`.

Optional environment variables:

```bash
HOST=127.0.0.1
PORT=3000
REQUEST_BODY_LIMIT_BYTES=262144
ALLOW_PRIVATE_INSTANCE_URLS=false
```

Set `ALLOW_PRIVATE_INSTANCE_URLS=true` only when intentionally targeting a private instance URL during local testing.

## API

### `GET /health`

Returns service status and whether a deployment is currently running.

### `GET /available-actions`

Returns a list of supported generic `core.*` flow actions, their descriptions, and JSON snippets showing how to construct their inputs.

### `POST /generate-flow-source`

Generates Fluent Flow API TypeScript without building or installing.

```json
{
  "flowSpec": {
    "id": "incident_created_notify_admin",
    "name": "Incident Created - Notify Admin",
    "description": "Notify admin when incident is created",
    "runAs": "system",
    "trigger": {
      "type": "record.created",
      "table": "incident",
      "condition": ""
    },
    "actions": [
      {
        "id": "log_created_incident",
        "type": "core.log",
        "inputs": {
          "log_level": "info",
          "log_message": {
            "template": "Incident created: {{trigger.current.number}}"
          }
        }
      }
    ]
  }
}
```

### `POST /deploy-flow`

Generates source, builds, installs, verifies, and returns safe logs with passwords redacted.

### `POST /verify-flow`

Checks whether a flow exists by name.

```json
{
  "instanceUrl": "https://dev305525.service-now.com",
  "username": "admin",
  "password": "password",
  "flowName": "Incident Created - Notify Admin"
}
```

### `POST /available-apps`

Lists app/scope values from `sys_scope` so you can choose the right `scopeId` and `scopeName`.

```json
{
  "instanceUrl": "https://dev305525.service-now.com",
  "username": "admin",
  "password": "password",
  "limit": 25
}
```

### `POST /available-flows`

Lists Flow Designer flows by optional `flowName` and/or `scopeId`.

```json
{
  "instanceUrl": "https://dev305525.service-now.com",
  "username": "admin",
  "password": "password",
  "flowName": "Incident Created - Notify Admin",
  "scopeId": "10c943a63d9c0c4ce1c6c76689f2671b",
  "limit": 25
}
```

## Flow Spec

Each flow requires:

- `id`: stable source identifier. It becomes a safe `Now.ID` key.
- `name`: Flow Designer flow name.
- `description`: optional description.
- `runAs`: `system` or `user`.
- `trigger`: one supported trigger.
- `actions`: one or more supported actions.

IDs are normalized to lowercase, letters/numbers/underscore only, and max 80 characters. The same ID updates the same SDK metadata on reinstall. Random IDs are not used.

## Input Normalization

Before validation, `/deploy-flow` and `/generate-flow-source` normalize common generic JSON shapes into the strict v1 registry.

The normalization, request redaction, and validation-error formatting code lives in `src/validation/`:

- `inputNormalization.ts`: action/trigger aliases, input key aliases, default values, and template cleanup.
- `redact.ts`: password-safe request logging/debug output.
- `zodIssueFormatter.ts`: concise validation errors with received values and allowed values.

- `core.send_email`, `send_email`, and `email` become `core.sendEmail`.
- `record_trigger` with `events: ["created"]`, `["updated"]`, or both becomes the matching record trigger type.
- `filter`, `query`, and `encoded_query` become `trigger.condition`.
- Human-readable trigger conditions such as `priority equals 2` become encoded query form such as `priority=2`.
- `to`, `subject`, and `body` become `ah_to`, `ah_subject`, and `ah_body`.
- Lookup `query` objects such as `{ "user_name": "admin" }` become encoded query strings such as `user_name=admin`.
- Missing `table_name` defaults from `trigger.table` for `core.sendEmail`.
- Missing `record` defaults to `{{trigger.current.sys_id}}` for record-based actions.
- Record trigger data pills such as `{{trigger.number}}` become `{{trigger.current.number}}`.
- Lookup result pills such as `{{action.lookup_user.records[0].email}}` become `{{action.lookup_user.Record.email}}`.
- Mixed strings such as `"P2 incident {{trigger.current.number}}"` are wrapped as `{ "template": "..." }` where the SDK supports data pills.
- Simple trigger conditions such as `{{trigger.current.priority}} == 2` become encoded query form such as `priority=2`.

SDK 4.7 does not allow data pills in `core.sendEmail.ah_body`. If a generic `body` contains `{{...}}`, the server converts those pills to static placeholders and returns a `normalization.warnings` entry. Dynamic values remain supported in `ah_to` and `ah_subject`.

## Supported V1 Triggers

- `record.created`
- `record.updated`
- `record.created_or_updated`

## Supported V1 Actions

The generator dynamically supports **all** `core.*` actions listed in the ServiceNow Zurich Workflow Studio documentation using a generic fallback. You can use any core action simply by providing its type and inputs:

- `core.askForApproval`
- `core.createRecord`
- `core.updateRecord`
- `core.createTask`
- `core.sendEmail`
- `core.lookUpRecords`
- `core.useAnAiAgent`
- *(...and any other standard core action)*

Inputs for actions that require `TemplateValue` wrapping (such as `values`, `fields`, or `field_values` for `core.createRecord`, `core.updateRecord`, etc.) are automatically handled by the generator. 

**Note**: SDK 4.7 rejects data pills in `core.sendEmail.inputs.ah_body`. Use a static body for `ah_body`; data pills are supported in fields such as `ah_subject` and `log_message`.

## Data Pills

Data pill strings are rendered into `wfa.dataPill(...)` calls:

- `"{{trigger.current.number}}"` -> `wfa.dataPill(params.trigger.current.number, "string")`
- `"{{trigger.current.sys_id}}"` -> `wfa.dataPill(params.trigger.current.sys_id, "reference")`
- `"{{action.some_action.Record.email}}"` -> `wfa.dataPill(someAction.Record.email, "string")`

Plain strings become JSON string literals. Mixed text templates use JavaScript template strings:

```json
{
  "template": "Incident created: {{trigger.current.number}}"
}
```

## Custom Action Rendering

Because the generator dynamically handles all generic `core.*` actions, you only need to add a custom action renderer if an action has extremely specific SDK requirements that the generic fallback doesn't cover (e.g., specific data pill validations).

To add a custom action renderer:
1. Add or refine the action schema in `src/schema/flowSpec.schema.ts`.
2. Add a renderer branch in `src/generator/renderAction.ts` before the `default` case.
3. Use `renderDataPill(input, context)` for all values that may contain data pills.
4. Return a stable `const <actionVariable> = wfa.action(...)` statement.

## Curl Example

```bash
curl -X POST http://127.0.0.1:3000/deploy-flow \
  -H "Content-Type: application/json" \
  -d @request.json
```

Example `request.json`:

```json
{
  "instanceUrl": "https://dev305525.service-now.com",
  "username": "admin",
  "password": "password",
  "scopeId": "x_hmz_flowauto",
  "scopeName": "HMZ Flow Automation",
  "reinstall": true,
  "flowSpec": {
    "id": "incident_created_notify_admin",
    "name": "Incident Created - Notify Admin",
    "description": "Notify admin when incident is created",
    "runAs": "system",
    "trigger": {
      "type": "record.created",
      "table": "incident",
      "condition": ""
    },
    "actions": [
      {
        "id": "log_created_incident",
        "type": "core.log",
        "inputs": {
          "log_level": "info",
          "log_message": {
            "template": "Incident created: {{trigger.current.number}}"
          }
        }
      },
      {
        "id": "send_email_admin",
        "type": "core.sendEmail",
        "inputs": {
          "table_name": "incident",
          "record": "{{trigger.current.sys_id}}",
          "watermark_email": true,
          "ah_to": "admin@example.com",
          "ah_subject": {
            "template": "New Incident Created: {{trigger.current.number}}"
          },
          "ah_body": {
            "template": "A new incident was created. Open the record in ServiceNow for details."
          }
        }
      }
    ]
  }
}
```

Set `reinstall: true` when you want the SDK to recreate the app metadata from the beginning. This maps to `npx @servicenow/sdk install --reinstall true --open-browser false` and may remove metadata in that app scope that is not present in the local SDK package. Leave it false for normal updates.

## PowerShell Example

```powershell
$body = Get-Content .\request.json -Raw
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:3000/deploy-flow `
  -ContentType 'application/json' `
  -Body $body
```

## SDK Auth, Build, And Install Troubleshooting

- The service uses CI authentication variables for each SDK command:
  - `SN_SDK_INSTANCE_URL`
  - `SN_SDK_USER`
  - `SN_SDK_USER_PWD`
  - `SN_SDK_NODE_ENV=SN_SDK_CI_INSTALL`
- It does not run `sdk auth --add`.
- It does not create a new SDK project per request.
- If build fails, call `/generate-flow-source` and inspect the generated Fluent source.
- If install fails, confirm the user can install into the requested scope and that the scope ID/name match the target application.
- If verification fails after install succeeds, confirm the SDK installed the flow under the requested name and that the user can read `sys_hub_flow`.





https://mbcgc727-3000.inc1.devtunnels.ms/

## Changes for the Snow Agent (v6)

- **One workspace per application.** The SDK deletes, on install, any record of the app that is no longer in the
  source (`src/fluent/generated/keys.ts`). Each flow/subflow is therefore kept as its own file
  (`src/fluent/flow_<id>.now.ts`, `subflow_<id>.now.ts`) in the workspace of its app; other apps' workspaces are parked
  in `.workspaces/<scope sys_id>/`. A failed build or install puts the item's file back as it was.
- **`subflow.call` action** — a flow calls a deployed subflow by sys_id:
  `{"type": "subflow.call", "inputs": {"subflow_sys_id": "<32 chars>", "subflow_inputs": {"record_id": "{{trigger.current.sys_id}}"}, "wait_for_completion": true}}`
- Flows/subflows are activated after a normal install when they are not active yet.
- Offline check (no instance): `npx tsx src/examples/offline_build_check.ts`.
