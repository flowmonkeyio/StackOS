# StackOS Operations

StackOS operations are the protocol-neutral callable layer. An operation is
registered once, then adapters expose it through MCP, REST, and CLI when its
surface policy allows that.

```text
OperationSpec
  -> MCP tool
  -> REST operation call
  -> CLI command
  -> UI operation catalog
```

The service, repository, connector, auth, and audit code remains the source of
behavior. Operations define the callable contract around that behavior:

- name and summary
- input and output models
- handler
- mutating/read-only classification
- grant and secret policy
- enabled surfaces
- agent guidance, prerequisites, return notes, and examples

## Agent Documentation

Agents are the primary callers for workflow execution operations. Scripts can
use the same catalog for automation and setup, but run-plan mechanics are
optimized for agents that need precise schemas, grants, examples, and safe
return notes. Callers should not guess operation payloads. The operation
registry returns an agent-readable description for every registered operation:

```bash
content-stack ops list
content-stack ops describe action.execute --json
content-stack ops describe runPlan.claimStep --json
```

The same metadata is available through REST:

```http
GET /api/v1/operations
GET /api/v1/operations/action.execute
```

Each description includes:

- enabled surfaces: `mcp`, `rest`, `cli`
- grant policy, for example `run-plan-step-action-ref`
- secret policy
- JSON input schema
- JSON output schema
- prerequisites
- return notes
- examples

MCP tools are generated from the same operation specs, so MCP clients still get
tool schemas through `tools/list`.

The UI reads the same docs at `/projects/{project_id}/operations`. That page is
not a second registry; it renders `GET /api/v1/operations` and
`GET /api/v1/operations/{operation_name}`.

## Generic REST Calls

The generic REST adapter is command-shaped and meant for scripts, CLI, CI, and
external automation:

```http
POST /api/v1/operations/{operation_name}/call
```

Payload:

```json
{
  "arguments": {
    "project_id": 1,
    "action_ref": "utils.sitemap.fetch",
    "input_json": {
      "urls": ["https://example.com/sitemap.xml"]
    },
    "run_token": "..."
  }
}
```

UI-friendly resource routes still exist separately, for example
`GET /api/v1/projects/{project_id}/action-calls`. The operation endpoint is the
generic callable surface; resource routes remain the ergonomic query/read model
for the UI.

## CLI Calls

The CLI uses the same REST operation adapter:

```bash
content-stack ops call action.describe \
  --input describe-action.json
```

For common cross-cutting fields, the CLI can merge flags into the input JSON:

```bash
content-stack ops call action.execute \
  --project 1 \
  --run-token "$RUN_TOKEN" \
  --idempotency-key sitemap-1 \
  --input action-input.json
```

`--input -` reads the operation arguments from stdin.

Common operations also have aliases that still call the generic operation
endpoint:

```bash
content-stack actions describe utils.sitemap.fetch --project 1
content-stack actions validate utils.sitemap.fetch --project 1 --input action-input.json
content-stack actions execute utils.sitemap.fetch \
  --project 1 \
  --run-token "$RUN_TOKEN" \
  --input action-input.json

content-stack run-plans validate --project 1 --template-key core.project-memory-review
content-stack run-plans create --project 1 --input run-plan.json
content-stack run-plans start 42 --project 1
content-stack run-plans claim-step 42 --step-id fetch-sitemap --run-token "$RUN_TOKEN"
content-stack run-plans record-step 42 \
  --step-id fetch-sitemap \
  --status success \
  --result step-result.json \
  --run-token "$RUN_TOKEN"
```

## Registered Core Operations

The current core operation registry includes:

- `action.describe`
- `action.validate`
- `action.execute`
- `runPlan.validate`
- `runPlan.create`
- `runPlan.start`
- `runPlan.get`
- `runPlan.list`
- `runPlan.update`
- `runPlan.claimStep`
- `runPlan.recordStep`

`action.execute` keeps the same boundary everywhere:

1. A valid `run_token` is required.
2. The run token must belong to a started run plan.
3. Exactly one run-plan step must be running.
4. The requested `project_id` must match the plan project.
5. The active step must declare the requested `action_ref`.
6. The frozen `mcp_tool_grants` snapshot must grant `action.execute` for that
   exact `action_ref`.
7. Credentials are resolved inside the daemon; callers pass only
   `credential_ref`.
8. The execution writes an `action_calls` audit row with redacted request,
   response, metadata, and run-plan linkage.

`runPlan.*` keeps the same boundary everywhere:

1. `runPlan.validate`, `runPlan.create`, `runPlan.start`, `runPlan.get`, and
   `runPlan.list` are bootstrap/setup operations.
2. `runPlan.claimStep` and `runPlan.recordStep` require the `run_token` returned
   by `runPlan.start`.
3. `runPlan.claimStep` activates only the frozen grants for the claimed step.
4. `runPlan.recordStep` persists the terminal result and closes the plan/run
   when the last step finishes.
5. `runPlan.update` remains an admin-only MCP operation and is intentionally not
   exposed through REST or CLI.

No operation adapter should bypass repository/connector auth, grant, idempotency,
or audit code.
