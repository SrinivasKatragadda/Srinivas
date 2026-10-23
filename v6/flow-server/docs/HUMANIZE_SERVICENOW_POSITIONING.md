# Humanize and ServiceNow: Positioning Guide

## Positioning in one sentence

Humanize is an external, API-first authoring and deployment layer that makes it easier to create governed ServiceNow flows and subflows; ServiceNow remains the system of record, workflow runtime, security boundary, and operational platform.

Do not position Humanize as a replacement for ServiceNow. Position it as a faster and more accessible way to build on ServiceNow, with limited competition against ServiceNow's native workflow-authoring and AI-assisted creation tools.

## The simple customer explanation

> Humanize does not replace ServiceNow or move workflows outside it. It translates a structured automation request into ServiceNow-native flow metadata, deploys it into the customer's chosen ServiceNow scope using the ServiceNow SDK, and verifies the result. The resulting flow is owned, reviewed, operated, and governed in ServiceNow.

## Why choose Humanize with ServiceNow

1. **An external, API-first entry point**  
   Humanize exposes flow and subflow generation and deployment through an API. This lets portals, copilots, MCP clients, internal developer platforms, and automation pipelines request ServiceNow-native automation without requiring every interaction to begin in Workflow Studio.

2. **A stable intermediate specification**  
   Humanize accepts a generic JSON definition for triggers, actions, inputs, outputs, and data pills. Upstream products can target this contract instead of generating ServiceNow Fluent TypeScript directly.

3. **Normalization before deployment**  
   Humanize converts common aliases and user-friendly input shapes into the strict forms required by the current implementation—for example action-name aliases, trigger conditions, email field names, record data pills, and lookup queries.

4. **Build, install, and verification in one operation**  
   The service generates source, runs the ServiceNow SDK build, installs into the selected application scope, and verifies the resulting flow or subflow in the target instance.

5. **ServiceNow-native output**  
   Humanize uses ServiceNow's Fluent Flow API and SDK. It does not create a separate workflow runtime or directly insert records into internal Flow Designer tables. Teams can inspect and continue working with the deployed artifact in ServiceNow.

6. **Deterministic, repeatable automation**  
   Stable source IDs map repeated deployments to the same SDK metadata. This supports repeatable generation and normal application lifecycle practices better than one-off manual construction.

7. **A controlled product surface**  
   Humanize can present a smaller, opinionated set of workflow capabilities to non-specialists while keeping instance credentials, scope selection, validation, redaction, and deployment rules in a controlled service layer.

8. **Integration into a broader conversational experience**  
   Humanize is useful when workflow creation is only one part of a larger product journey—for example discovering a requirement, collecting missing fields, generating an automation, and returning a direct Flow Designer link for expert review.

## Where Humanize is complementary

| Area | Humanize role | ServiceNow role | Relationship |
|---|---|---|---|
| Workflow runtime | Produces the definition | Executes and monitors the flow | Complementary |
| System of record | Reads/selects deployment context | Owns records, tables, permissions, and audit history | Complementary |
| Flow Designer / Workflow Studio | Creates an initial native artifact and returns it for review | Visual editing, testing, activation, operations, and lifecycle management | Complementary |
| ServiceNow SDK and Fluent API | Orchestrates and simplifies their use | Provides the supported metadata model, build, and install mechanisms | Complementary |
| External portals, copilots, or MCP tools | Provides an API contract they can call | Receives and runs the deployed automation | Complementary |
| Multi-instance delivery | Provides one external integration pattern for chosen instances/scopes | Enforces each instance's access and application boundaries | Complementary |
| Governance | Validates inputs, redacts secrets, and can enforce an approved subset | Provides platform roles, scopes, security, review, activation, and audit controls | Complementary |
| Existing ServiceNow investments | Makes those investments easier to invoke from another experience | Remains the core platform and source of truth | Complementary |

## Where Humanize competes or overlaps

Humanize competes only in the **workflow authoring experience**, not in the underlying platform.

| Capability | Humanize | Native ServiceNow capability | Degree of overlap |
|---|---|---|---|
| Create a flow from a higher-level request | Converts a generic specification into a flow | Now Assist can generate a flow outline from text or images | Direct overlap |
| Create ServiceNow metadata from code/instructions | Generates Fluent TypeScript and installs it | Build Agent and ServiceNow developer tools can create or modify application metadata | Partial overlap |
| Configure standard flow actions | Maps generic `core.*` actions and inputs | Workflow Studio authors configure actions directly | Direct overlap |
| Create subflows | Generates subflows with typed inputs and outputs | Workflow Studio and Fluent API natively create subflows | Direct overlap |
| Validate and deploy generated automation | Validates its contract, builds, installs, and verifies | Native development and deployment tooling covers application lifecycle | Partial overlap |
| Visual editing and debugging | Not provided by this repository | Workflow Studio provides the native design environment | No meaningful competition |
| Runtime, monitoring, governance, and platform data | Not provided | ServiceNow owns these capabilities | No competition |
| Broad application generation | Current repository is focused on flows/subflows | Build Agent supports broader application metadata and development workflows | ServiceNow is broader |

## Where ServiceNow is the better choice

Recommend native ServiceNow tooling when:

- Authors already work primarily inside Workflow Studio and do not need an external authoring channel.
- The customer wants the broadest set of current platform features, visual flow logic, debugging, recommendations, testing, or monitoring.
- The task extends beyond Humanize's implemented flow/subflow schema into broad application development.
- The organization wants a single-vendor experience and already has the required ServiceNow AI capabilities, roles, skills, and subscription.
- A workflow requires platform features that Humanize has not explicitly validated.

Credibility improves when the team says this openly. Humanize should win because it fits a particular delivery model, not because ServiceNow lacks workflow-building capability.

## Where Humanize is the better choice

Recommend Humanize when:

- Workflow creation must be embedded in an external product, portal, chat, MCP client, or developer platform.
- An upstream system needs a documented JSON contract rather than direct knowledge of Fluent TypeScript.
- The team wants consistent normalization, generation, deployment, and verification behind one API call.
- The organization wants to constrain authors to an approved, opinionated automation surface.
- The same integration pattern must target multiple authorized ServiceNow instances or scopes.
- The desired end state is still a native ServiceNow flow that platform experts can inspect and govern.

## Competitive talk track

### If a buyer says, "ServiceNow already does this"

> Yes—ServiceNow has strong native workflow authoring, including AI-assisted flow generation. Humanize is valuable when you want that creation capability exposed through your own product experience or automation pipeline. It provides a stable external contract, normalizes the request, deploys through the ServiceNow SDK, and hands the native artifact back to ServiceNow for governance and operation.

### If a buyer asks, "Are you replacing Now Assist?"

> Not across the platform. There is overlap in generating an initial flow. Now Assist is the natural choice for authors working inside Workflow Studio. Humanize is designed for externally initiated, API-driven creation and for embedding ServiceNow automation into a broader experience.

### If a buyer asks, "Why not build this directly with the ServiceNow SDK?"

> A team can. Humanize packages the repeated engineering around that SDK: a generic contract, normalization, safe validation errors, source generation, build/install orchestration, credential redaction, scope handling, and post-install verification. The value is the reusable product layer, not a claim that the underlying ServiceNow SDK is unavailable.

### If a buyer asks, "Who owns the generated workflow?"

> The customer does, in their ServiceNow instance and selected application scope. ServiceNow remains the runtime and governance layer.

## Evidence available in the current repository

The following statements are supported by the current implementation:

- REST endpoints generate, deploy, list, and verify flows and subflows.
- Generated artifacts use the ServiceNow Fluent Flow API.
- Deployment uses the ServiceNow SDK build and install commands.
- Flows support record-created, record-updated, and created-or-updated triggers.
- The generator has a generic path for `core.*` actions, with automatic wrapping for common template-valued inputs.
- Subflows support typed string, integer, and boolean inputs and outputs.
- Input normalization handles common trigger/action aliases, conditions, queries, data-pill shapes, and email keys.
- Credentials are redacted from logs and are not intentionally persisted.
- Deployment targets an existing customer-provided ServiceNow instance and application scope.
- Verification checks for the installed artifact and can return a Flow Designer URL.

## Claims not to make yet

Avoid these statements unless the wider Humanize product has separate evidence for them:

- "Humanize supports every ServiceNow workflow feature." The generator accepts generic `core.*` actions, but every action/input combination has not necessarily been validated.
- "Humanize is safer or more secure than ServiceNow." The products occupy different layers, and no comparative security assessment is present.
- "Humanize replaces Now Assist, Workflow Studio, Flow Designer, or Build Agent."
- "Humanize requires no ServiceNow expertise." Expert review, permissions, scope design, testing, and activation still matter.
- "Humanize provides full production lifecycle management." The current service generates, installs, and verifies; native tooling remains important for testing, activation, debugging, monitoring, rollback, and governance.
- "Dynamic email bodies are fully supported." The repository documents a ServiceNow SDK 4.7 limitation for data pills in `core.sendEmail.ah_body`.
- Unverified claims about cost, speed, accuracy, supported releases, licensing savings, or deployment success rates.

## Recommended category language

Use:

- "ServiceNow-native automation authoring layer"
- "External API for governed flow generation and deployment"
- "ServiceNow workflow accelerator"
- "Embedded workflow creation for ServiceNow"

Avoid:

- "ServiceNow replacement"
- "Alternative ITSM platform"
- "Better version of Flow Designer"
- "Complete replacement for Now Assist"

## Proof points to collect next

To strengthen sales claims, measure:

1. Median time from approved requirement to a reviewable flow in ServiceNow, compared with the customer's existing process.
2. Generation success rate and post-generation edit rate by action type.
3. Percentage of generated flows that pass customer review and testing without structural changes.
4. Number of external channels and ServiceNow instances served by one Humanize integration.
5. Deployment, validation, and verification failure rates with categorized causes.
6. Customer licensing prerequisites for both the Humanize route and the relevant native ServiceNow AI route.
7. Supported ServiceNow releases, SDK versions, actions, flow logic, triggers, and subflow data types in a maintained compatibility matrix.

## Source documentation

### Humanize repository

- `README.md` — service architecture, endpoints, normalization rules, supported triggers/actions, data pills, security notes, and deployment behavior.
- `subflow_features_for_mcp.md` — subflow endpoints, schema, typed inputs/outputs, verification, and MCP integration notes.
- `src/server.ts` — exposed API routes and request handling.
- `src/generator/` — flow/subflow source generation and action/data-pill rendering.
- `src/deploy/` — SDK execution, deployment, listing, and verification behavior.
- `src/validation/` — normalization, redaction, and validation-error behavior.

### Official ServiceNow references

- [Workflow Studio](https://www.servicenow.com/docs/r/build-workflows/workflow-studio/workflow-studio.html) — ServiceNow's environment for authoring, configuring, and monitoring workflows.
- [Create a flow with Now Assist](https://www.servicenow.com/docs/r/build-workflows/workflow-studio/create-flow-now-assist.html) — native generation of a flow outline from text directions or images, including prerequisites and review steps.
- [ServiceNow Fluent Flow API](https://www.servicenow.com/docs/r/application-development/servicenow-sdk/fluent-flow-api.html) — native code model for flows, subflows, triggers, actions, and flow logic.
- [ServiceNow Fluent API reference](https://www.servicenow.com/docs/r/xanadu/application-development/servicenow-sdk/servicenow-fluent-api-reference.html) — defining scoped-application metadata in source code with the ServiceNow IDE or SDK.
- [Build Agent workflow](https://www.servicenow.com/docs/r/application-development/build-agent-workflow.html) — native natural-language-assisted application development, testing, and deployment workflow.

## Internal summary

The honest positioning is **complementary at the platform level and competitive at the entry point**.

- Complementary: Humanize builds ServiceNow-native artifacts; ServiceNow owns execution, data, security, governance, visual editing, and operations.
- Competitive: both Humanize and native ServiceNow tools can turn higher-level instructions into flows and can assist with application metadata creation.
- Differentiator: Humanize's strongest current distinction is an embeddable external API and normalized intermediate contract that orchestrates native SDK generation, deployment, and verification.
