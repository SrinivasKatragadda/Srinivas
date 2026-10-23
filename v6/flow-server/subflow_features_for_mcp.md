# Subflow Features & API Documentation

This document outlines the newly added capabilities for Subflow generation and deployment in the ServiceNow SDK Server. It provides the necessary details and JSON structures to integrate these features into Model Context Protocol (MCP) servers.

## 1. New Endpoints Added

The server now exposes new REST endpoints specifically tailored for Subflows. Subflows differ from standard Flows in that they accept defined **inputs** and **outputs** rather than a trigger.

- `POST /generate-subflow-source`: Accepts a `SubflowSpec` and returns the generated TypeScript code for the `@servicenow/sdk/automation` API.
- `POST /deploy-subflow`: Accepts a `DeploySubflowRequest` with instance credentials and a `SubflowSpec`. It dynamically generates the subflow, builds it, and deploys it to the target ServiceNow instance.
- `POST /verify-subflow`: Checks if a specific subflow name exists on the target ServiceNow instance and returns its `sys_id` and a direct `flowDesignerUrl`.

## 2. Dynamic Import Generation (TS6133 Fix)

The TypeScript code generator for Subflows (`src/generator/generateSubflowSource.ts`) has been optimized to dynamically parse the JSON payload and **only import the specific column types** (`StringColumn`, `IntegerColumn`, `BooleanColumn`) that are actually utilized by the inputs and outputs. This prevents strict TypeScript build errors (`TS6133`) during the SDK compilation step.

## 3. Subflow JSON Payload Structure

To interface with the MCP server, use the following JSON structure when calling `/deploy-subflow` or `/generate-subflow-source`. 

### Example `DeploySubflowRequest` Payload:

```json
{
  "instanceUrl": "https://dev12345.service-now.com",
  "username": "admin",
  "password": "your_password",
  "scopeId": "global",
  "scopeName": "Test Subflow App",
  "subflowSpec": {
    "id": "my_subflow",
    "name": "My Subflow",
    "description": "Optional description for the subflow",
    "runAs": "system",
    "inputs": {
      "input_text": {
        "type": "string",
        "label": "Input Text",
        "mandatory": true
      },
      "user_age": {
        "type": "integer",
        "label": "User Age",
        "mandatory": false
      }
    },
    "outputs": {
      "success_flag": {
        "type": "boolean",
        "label": "Success Flag"
      }
    },
    "actions": [
      {
        "id": "log_action_1",
        "type": "core.log",
        "inputs": {
          "log_level": "info",
          "log_message": "Subflow executed successfully"
        }
      }
    ]
  }
}
```

### Schema Details:

**`subflowSpec.inputs` and `subflowSpec.outputs` (SubflowIoSpec)**
- Represents the inputs/outputs map.
- Keys (e.g., `input_text`, `success_flag`) become the variable names.
- **`type`**: Must be one of `"string"`, `"integer"`, or `"boolean"`.
- **`label`**: The human-readable label shown in ServiceNow Flow Designer.
- **`mandatory`**: Boolean indicating if the field is required (optional, defaults to `false`).

## 4. Subflow Test Suite

A new dedicated testing suite was added:
- Directory: `src/examples/subflow_tests`
- Script: `run_subflow_tests_curl.ps1`
- The PowerShell script handles preflight checks (verifying the API server is updated) and offers an interactive prompt (`Y/N/R/O/S`) after every deployment. This allows developers to manually verify Subflows inside the Flow Designer UI (`O` for open) and prevent automated runs from cascading failures.
