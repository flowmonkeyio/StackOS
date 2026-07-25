# Linear Integration Contract

Status: **executable implementation; automated contract signoff complete; live
operator OAuth and personal-key smoke pending**

Reviewed: 2026-07-24

## Scope And Architecture

The shared selection, profile-isolation, evidence, readiness, and migration
rules are canonical in the
[one-brain auth-method contract](../auth-providers.md#one-brain-auth-method-contract).
This document records only Linear-specific protocol and action facts.

Linear follows the existing static provider architecture exactly:

- `OAuthProviderContract` and the shared OAuth lifecycle own authorization,
  refresh, scope evidence, daemon-held credentials, connection state, and local
  revocation for the OAuth method.
- `LinearIntegration` owns the one fixed GraphQL endpoint, authenticated HTTP,
  response limits, and provider error normalization.
- one `LinearActionConnector` and `plugins/linear/plugin.yaml` own the curated
  action contract.
- generic `action.list`, `action.describe`, `action.validate`, `action.run`,
  and `action.execute` own discovery, grants, credential resolution,
  idempotency, response files, and audit.
- `ProviderObjectReferenceRepository` owns reusable account-bound object refs.
- the existing manifest-driven Connections UI owns setup, connect, reconnect,
  test, revoke, readiness, expiry, safe account display, and repair guidance.

There is no Linear-specific MCP/REST/CLI operation, raw GraphQL action,
client-credentials method, app actor, admin scope, webhook surface, runtime
schema discovery, custom Linear route, issue browser, personal-key management,
or provider-side revocation execution.

## Official Sources

- [OAuth 2.0 authentication](https://linear.app/developers/oauth-2-0-authentication)
- [GraphQL API](https://linear.app/developers/graphql)
- [Developer getting started](https://linear.app/developers/sdk) (documents
  OAuth and personal API-key authentication)
- [Pagination](https://linear.app/developers/pagination)
- [Filtering](https://linear.app/developers/filtering)
- [Rate limiting](https://linear.app/developers/rate-limiting)
- [Deprecations](https://linear.app/developers/deprecations)
- [Current public schema](https://studio.apollographql.com/public/Linear-API/schema/reference?variant=current)
- [Official brand resources](https://linear.app/brand)

## OAuth Contract

| Property | Executable contract |
| --- | --- |
| Method | OAuth 2.0 authorization code |
| Authorization endpoint | `https://linear.app/oauth/authorize` |
| Token endpoint | `https://api.linear.app/oauth/token` |
| Client authentication | `client_id` and `client_secret` in the token request body |
| PKCE | Required, `S256` |
| Fixed authorization parameters | `actor=user` |
| Requested scopes | `read,write` |
| Token type | returned `token_type` must be `Bearer` on authorization and refresh |
| Returned scope field | `scope`; every authorization and refresh response must contain both `read` and `write` before credential mutation; the shared resolver then enforces the selected action's declared scope before dispatch |
| Initial exchange evidence | `refresh_token`, `expires_in`, and returned `scope` |
| Refresh evidence | rotated/current `refresh_token`, `expires_in`, `Bearer`, and full `read,write` scope evidence |
| Account binding | fixed `viewer { organization { ... } }` probe after authorization/test |
| Credential handling | access token, refresh token, client id, and client secret remain daemon-held |
| Disconnect | existing local `account.revoke`; Linear's documented `POST https://api.linear.app/oauth/revoke` capability is explicitly deferred |

The callback is application-owned and fixed by the shared OAuth infrastructure.
The manifest presents
`https://auth.stackos.flowmonkey.io/api/v1/auth/oauth/callback`; the relay
forwards the unchanged callback to the local daemon. Callers cannot choose a
redirect URI.

The account probe persists only safe organization/user identity metadata and
the opaque credential ref. Sanitized diagnostics may include provider account
identifiers needed to distinguish workspaces; action inputs and outputs use
account-bound opaque object refs. The probe never exposes Linear access tokens,
refresh tokens, or personal keys.

## Personal API-Key Contract

| Property | Executable contract |
| --- | --- |
| Method | Linear personal API key |
| Setup | Operator creates the key in Linear account settings and stores it through the generic StackOS Connections form. |
| Payload | The `api_key` field is encrypted as one raw daemon-held value. |
| Transport | StackOS sends that raw value as the complete `Authorization` header; it does not prefix `Bearer`. |
| Permission posture | Linear enforces the key's workspace and action permissions at request time. StackOS records no locally verified scope grants for this method. |
| Renewal | None. The generic resolver does not enter the OAuth renewal lifecycle for a personal key. |
| Account binding | The same fixed `viewer { organization { ... } }` probe returns existing safe organization/user metadata. |
| Repair | Replace or adjust the key in Linear if a request is rejected, then run the generic connection Test again. |

The saved `auth_method_key` is authoritative. The wrapper never infers OAuth
versus a personal key from the encrypted payload shape. An unknown method or a
method that conflicts with the generic auth-test probe context fails before any
provider request.

## GraphQL Transport Contract

| Property | Executable contract |
| --- | --- |
| Endpoint | `POST https://api.linear.app/graphql` only |
| Authorization | OAuth: `Authorization: Bearer <daemon-resolved access token>`; personal key: the raw daemon-resolved key as the complete `Authorization` value |
| Documents | repository-owned `.graphql` files below `plugins/linear/graphql/` |
| Caller-supplied query | forbidden |
| Request body cap | 256 KiB |
| Response body cap | 5 MiB |
| Read retries | shared bounded retry behavior for safe transient failures |
| Write retries | zero automatic transport retries |
| Metadata | safe request id, rate-limit, and query-complexity fields when returned |

The integration loads a reviewed document by path, rejects paths outside the
Linear document directory, and sends only the document plus connector-built
variables. Manifest config mirrors the document/root/scope mapping but cannot
override the connector's hard-coded 34-action table.

## Schema Evidence

The public endpoint was introspected without credentials on 2026-07-23 with
deprecation and input-value deprecation data enabled. Descriptions were omitted
because they do not affect executable document validation.

| Evidence | Value |
| --- | --- |
| Snapshot | `plugins/linear/schema/introspection-2026-07-23.json` |
| Metadata | `plugins/linear/schema/introspection-2026-07-23.metadata.json` |
| Root policy | `plugins/linear/schema/root-policy-2026-07-23.json` |
| SHA-256 | `e3ed66bdf20c08167ba614c0b54f7b281038e2c6c83c6dddff24d050260ff8c1` |
| Query roots | 163 |
| Mutation roots | 371 |
| Fixed action documents | 34 |
| Unique allowed roots | 33 |
| Excluded roots | 501 |

Thirty-four actions use thirty-three unique roots because
`linear.issue_relations.list` deliberately reuses the `issue` root and traverses
`issue.relations`. Linear's workspace-wide `issueRelations` root has no issue
filter and is excluded.

`issueSearch` is current and not schema-deprecated. It is excluded by StackOS
product policy in favor of the selected `searchIssues` contract. Tests must not
describe that exclusion as provider deprecation.

## Fixed Action Catalog

Every input schema has `additionalProperties: false`. All provider object
arguments are account-bound `provider-object:` refs. List actions use forward
pagination with `first` defaulting to 50 and bounded to 1–100; `after` is an
opaque cursor. `order_by` is limited to `createdAt` or `updatedAt`.

| Action | Root | Exact public inputs | Output projection |
| --- | --- | --- | --- |
| `linear.viewer.get` | `viewer` | none | viewer and organization safe refs plus selected names |
| `linear.teams.list` | `teams` | `first`, `after`, `include_archived`, `order_by` | bounded team nodes and page info |
| `linear.teams.get` | `team` | `team_ref` | selected team fields |
| `linear.users.list` | `users` | `first`, `after`, `include_archived`, `include_disabled`, `order_by` | bounded user nodes and page info |
| `linear.users.get` | `user` | `user_ref` | selected user fields |
| `linear.workflow_states.list` | `workflowStates` | paging, archive/order, `team_ref`, created/updated bounds | bounded workflow-state nodes and page info |
| `linear.workflow_states.get` | `workflowState` | `workflow_state_ref` | selected workflow-state fields |
| `linear.projects.list` | `projects` | paging, archive/order, `team_ref`, created/updated bounds | bounded project nodes and page info |
| `linear.projects.get` | `project` | `project_ref` | selected project fields |
| `linear.cycles.list` | `cycles` | paging, archive/order, `team_ref`, created/updated bounds | bounded cycle nodes and page info |
| `linear.cycles.get` | `cycle` | `cycle_ref` | selected cycle fields |
| `linear.issue_labels.list` | `issueLabels` | paging, archive/order, `team_ref`, created/updated bounds | bounded label nodes and page info |
| `linear.issue_labels.get` | `issueLabel` | `issue_label_ref` | selected label fields |
| `linear.issues.list` | `issues` | paging, archive/order, reviewed refs, created/updated bounds | bounded issue nodes, selected nested refs, and page info |
| `linear.issues.get` | `issue` | `issue_ref` | selected issue fields and nested refs |
| `linear.issues.search` | `searchIssues` | `term`, paging, archive/order, reviewed refs, created/updated bounds | bounded issue search nodes and page info |
| `linear.comments.list` | `comments` | `issue_ref`, paging, archive/order | issue-scoped comment nodes and page info |
| `linear.comments.get` | `comment` | `comment_ref` | selected comment fields |
| `linear.issue_relations.list` | `issue` | `issue_ref`, paging, archive/order | issue-scoped relation nodes and page info |
| `linear.issue_relations.get` | `issueRelation` | `relation_ref` | selected relation and issue refs |
| `linear.issues.create` | `issueCreate` | `team_ref`, `title`, optional description/refs/labels/priority/estimate/due date | `success` plus selected created issue |
| `linear.issues.update` | `issueUpdate` | `issue_ref` plus optional title/description/refs/priority/estimate/due date | `success` plus selected updated issue |
| `linear.issues.archive` | `issueArchive` | `issue_ref` | `success` plus selected issue |
| `linear.issues.unarchive` | `issueUnarchive` | `issue_ref` | `success` plus selected issue |
| `linear.issues.labels.add` | `issueAddLabel` | `issue_ref`, `issue_label_ref` | `success` plus selected issue |
| `linear.issues.labels.remove` | `issueRemoveLabel` | `issue_ref`, `issue_label_ref` | `success` plus selected issue |
| `linear.comments.create` | `commentCreate` | `issue_ref`, `body`, optional `parent_comment_ref` | `success` plus selected comment |
| `linear.comments.update` | `commentUpdate` | `comment_ref`, `body` | `success` plus selected comment |
| `linear.comments.resolve` | `commentResolve` | `comment_ref` | `success` plus selected comment |
| `linear.comments.unresolve` | `commentUnresolve` | `comment_ref` | `success` plus selected comment |
| `linear.comments.delete` | `commentDelete` | `comment_ref` | `success`, provider receipt, and input-derived `deleted_ref` |
| `linear.issue_relations.create` | `issueRelationCreate` | `issue_ref`, `related_issue_ref`, `relation_type` | `success` plus selected relation |
| `linear.issue_relations.update` | `issueRelationUpdate` | `relation_ref`, `relation_type` | `success` plus selected relation |
| `linear.issue_relations.delete` | `issueRelationDelete` | `relation_ref` | `success`, provider receipt, and input-derived `deleted_ref` |

The exact variable types, defaults, root arguments, and field projections live
in the 34 executable documents. `tests/unit/test_linear_schema_contract.py`
validates every document against the pinned schema, rejects deprecated selected
fields/arguments/enum values, and proves exact parity between the documents,
manifest, connector table, and root allow/exclusion policy.

Issue list/search filters are connector-built from only the typed fields above.
Callers cannot submit provider-native filter trees. The connector distinguishes
omitted fields from explicit `null` on update. Relation types are limited to
`blocks`, `duplicate`, `related`, and `similar`, while the GraphQL variable
remains the provider's `String` type.

## Safe Reference And Output Contract

Every selected provider `id` is removed and converted to an account-bound
opaque ref whose public field matches its object type, including `issue_ref`,
`team_ref`, `user_ref`, `workflow_state_ref`, `project_ref`, `cycle_ref`,
`issue_label_ref`, `comment_ref`, `relation_ref`, and `organization_ref`.

Outputs include the fixed document result plus:

- `schema_ref` and GraphQL operation metadata;
- safe request/rate-limit/query-complexity metadata when present;
- page info for connection reads;
- provider `success` for mutations;
- `deleted_ref` derived from the validated input for delete actions.

Raw provider ids, credentials, authorization headers, schema bodies, local
paths, and unrestricted GraphQL responses are not public action output.

## Error, Retry, And Idempotency Contract

- GraphQL `errors` fail the action even when HTTP status is 200 and `data` is
  partially present.
- `success: false` fails the action.
- Linear's HTTP 400 plus GraphQL `RATELIMITED` response is classified as a
  provider rate-limit failure separately from HTTP 429.
- HTTP 401, 403, 429, 5xx, timeout, malformed JSON, oversized responses, and
  invalid payload structure return redacted structured repair context.
- a write transport failure after dispatch is `outcome_unknown` and is not
  automatically retried.
- StackOS action idempotency prevents a repeated local dispatch for the same
  key and replays the canonical stored response. It does not claim to prove the
  Linear mutation outcome after an ambiguous provider timeout.

## Explicit Exclusions

The root policy excludes all schema roots outside the 33-root allowlist,
including administration, application management, API-key administration,
integrations, webhooks, workspace membership/role changes, OAuth application management,
attachments/uploads, documents, initiatives, roadmaps, notifications, inbox,
favorites, templates, imports/exports, raw search variants, and other
unreviewed product surfaces.

`issueSearch` is an intentional product-policy exclusion. Linear documents
`POST https://api.linear.app/oauth/revoke` with a form `token` and optional
`token_type_hint`; StackOS does not execute that endpoint in this delivery.
Remote revocation is an explicit unsupported/deferred provider capability, not
an absent provider feature. Local disconnect uses the existing StackOS
revocation path, removes the daemon-held credential, and does not invalidate
the token at Linear. An operator who requires immediate provider-side
invalidation must revoke the application/token in Linear before local cleanup;
otherwise the remote token may remain valid until provider expiry or manual
revocation.

## Operator Setup

1. In StackOS, choose **Connect with Linear** to use OAuth, or **Personal API
   key** to use an existing personal credential.
2. For OAuth, create a Linear OAuth application, register the exact callback
   shown in StackOS, enter the client id and secret, and authorize the
   user-actor `read,write` request.
3. For a personal key, create the key in Linear account settings with only the
   workspace and action permissions required for the curated StackOS actions,
   then enter it only in the local Connections form.
4. Return to Connections and run **Test** separately. The same test binds and
   displays the safe Linear workspace/account identity for either method.
5. Use generic reconnect for expired or missing OAuth scopes. For a personal
   key, replace or adjust the key in Linear when Linear rejects it. Generic
   revoke removes the daemon-held local credential only; revoke in Linear first
   when immediate provider-side invalidation is required.

No client credential, token, or Linear provider id should be pasted into
documentation, tracker evidence, agent prompts, or repository files.

## Verification And Signoff

Three states are intentionally separate:

1. **Contract approval:** complete. The pinned schema, root policy, 34 fixed
   documents, OAuth protocol facts, and architecture boundaries were reviewed
   before implementation.
2. **Executable automated signoff:** complete. Focused tests cover schema
   parity, OAuth exchange/refresh/account binding, method-aware raw versus
   Bearer transport, transport errors and limits, all action classes, safe
   refs, scope denial, grants, MCP visibility, run-plan execution/audit,
   idempotency, generic Connections rendering, and production UI build.
3. **Live operator gate:** pending. A real Linear OAuth application and a
   least-privilege personal API key must each complete a separate connection
   Test, representative read, one reversible write, and local revoke using
   operator-held credentials. OAuth additionally proves refresh.

The live gate is release evidence, not permission to add admin integration,
webhooks, raw GraphQL, personal-key management, or any other excluded surface.
