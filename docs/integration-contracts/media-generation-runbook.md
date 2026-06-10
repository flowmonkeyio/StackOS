# Media Generation Tool Runbook

This is the canonical runbook for adding or expanding StackOS media-generation
tools. Use it with [`media-generation.md`](./media-generation.md), which owns
the provider shortlist and per-provider capability facts.

Do not add connector code for a new provider until its deep contract review is
complete and the operator confirms account creation, active billing, and a
stored StackOS credential for that provider. Never ask for or echo the key.

## Required Delivery Shape

Every concrete media tool uses the same StackOS split:

- `stackos/integrations/<provider>.py`: reusable first-party API wrapper.
  It owns vendor endpoint paths, request/response parsing, provider job ids,
  provider error mapping, and generated-asset persistence helpers.
- `stackos/actions/<provider>.py`: thin action connector. It validates the
  static action payload, estimates budget, calls the wrapper, and returns safe
  action output. It must not choose strategy, rewrite workflow intent, or
  silently downgrade unsupported features.
- Agent-visible output must avoid raw provider blobs and secret-looking key
  names. Normalize provider usage metadata before returning it if keys include
  words such as `token`, `secret`, or `credential`; the shared redactor treats
  those as sensitive by design.
- `stackos/plugins/manifest.py`: provider metadata, auth method, independent
  action rows, schemas, budget kind, and `capability_metadata`.
- `stackos/actions/__init__.py` and `stackos/integrations/__init__.py`:
  explicit registration only after the wrapper, connector, tests, docs, and
  changelog are delivered together.

Use provider-specific providers and action refs when schemas differ. Keep the
neutral `video-generation` provider only as a deferred planning placeholder
until StackOS intentionally adds a provider-neutral abstraction. Do not wire
workflow execution through the neutral placeholder when provider-specific
actions exist.

Commit one provider delivery at a time. A provider commit must contain that
provider's contract updates, connector code, manifest/action metadata, tests,
docs/changelog changes, tracker closeout, and independent signoff evidence.

Provider commit detail checklist:

- Provider/action refs and exact model ids delivered.
- Official docs URLs reviewed and any console-rendered evidence used.
- Capability metadata summary, including unsupported provider features.
- Tests run by independent verifier and any skipped checks.
- Operator confirmation status for account, billing, model activation, and
  stored StackOS credential.
- Remaining deferred modes, if any, with `execution_mode`/`deferred_reason`.

## Ticket And Gate Flow

Each provider delivery needs three tickets before implementation:

- Contract ticket: official-doc review, provider key, auth method, endpoint
  family, modes, schemas, limits, budget unit, safety/watermark behavior, and
  open verification items.
- Build ticket: wrapper, connector, manifest, registration, tests, docs, and
  changelog. For new providers, keep this blocked until the operator confirms
  account, billing, and stored credential.
- Signoff ticket: independent verification against current official provider
  docs and StackOS runtime behavior.

For workflow-backed work, keep these tickets dependency-bridged into the
engineering workflow spine. Do not claim final signoff while contract, build,
verification, review, or tracker-audit gates are still open.

## Workflow Wiring

Provider-specific media actions should be exposed to workflows through
`action_contracts`. If a branch is optional, such as "generate video only when
the operator requested video", set `optional: true` on each provider-specific
action contract and keep the concrete action refs on the producing step. This
lets the run plan grant the selected provider action while `readiness.check`
reports unconfigured optional branches as `required_for:
optional_action_execution` instead of blocking the base workflow.

Do not use the neutral `utils.video.generate` action in workflows for actual
execution while it remains `deferred-video-backend-selection`.

## Commit Policy

Commit each provider integration separately. A provider commit must contain all
details needed to review that provider end to end:

- contract facts and official documentation references,
- wrapper and connector code,
- manifest/action capability metadata,
- registration changes,
- tests,
- integration-contract docs,
- action-executor/quality-matrix/changelog updates, and
- independent verifier signoff notes.

Do not mix multiple provider integrations in one commit. Shared scaffolding may
be committed separately only when it is provider-neutral and required by more
than one provider. Documentation-only shortlist or runbook maintenance can be a
separate commit when it does not make a provider executable.

## Deep Contract Review

Use official provider documentation as the truth. The local shortlist can guide
the search, but signoff must compare code and manifest behavior to provider
docs.

Record the following before any connector build:

- Provider manifest key and action refs.
- Auth mechanism, scopes, console setup, billing/region requirements, and safe
  credential metadata.
- Endpoint family and execution model: synchronous image response or async
  video submit, poll, download, persist.
- Supported modes: text-to-image, image-to-image, reference composition,
  mask/inpainting, text-to-video, image-to-video, first/last frame,
  reference-to-video, video-to-video, extension, audio/dialogue, and native
  soundtrack controls.
- Model ids, default model, preview/beta/GA status, and retirement notices.
- Size controls: exact dimensions, aspect-ratio enums, resolution tiers,
  duration, fps, and model-specific exceptions.
- Input formats, input counts, input size caps, URL/base64/file-id support,
  output formats, output URL expiry, and provider retention.
- Safety, watermark, SynthID/provenance, person-generation, commercial use,
  moderation knobs, and region restrictions.
- Pricing and budget unit: per image, per second, per token, per resolution, or
  unknown/deferred.
- Rate limits, concurrency, retryable status codes, provider job status enums,
  error body shape, request ids, and idempotency support.

If any contract fact cannot be verified from official docs or operator
in-console evidence, keep that mode deferred or list it as an open verification
item. Do not guess from third-party SDKs or aggregators.

When official docs render as an application shell through fetch/curl, the
contract ticket needs browser-rendered evidence from the official docs or
operator-provided in-console evidence. If that evidence path is unavailable,
leave the exact fact unverified and keep the build ticket blocked.

Keep raw rendered snapshots, screenshots, and scratch captures out of provider
commits unless they live under an intentional evidence artifact path and the
commit needs them. Prefer distilled tracker evidence plus curated docs.

## Capability Metadata

Every media action must include `config.capability_metadata` with:

- `modalities`: input and output media types.
- `modes`: the exact modes this StackOS action exposes.
- `execution`: sync/deferred/async, provider endpoint, polling behavior, and
  generated-asset persistence.
- `models`: model ids and model-specific limits for size, aspect, resolution,
  duration, fps, input media, output format, count, quality, and fidelity.
- `safety`: moderation, watermark/provenance, person-generation, and terms
  caveats relevant to agents.
- `unsupported_provider_features`: official provider features that exist but
  this StackOS action does not expose.
- `docs`: official docs links used for the action contract.

When provider docs expose more capability than the connector supports, choose
one path explicitly:

- expose and test the feature,
- list it in `unsupported_provider_features`, or
- leave the whole action/mode deferred with `execution_mode` and
  `deferred_reason`.

If a provider returns temporary or signed media URLs, the wrapper must download
them immediately, persist local generated-assets refs, strip the provider URLs
from returned output and action-call audit, and test that no signed URL or query
signature reaches agent-visible payloads.

Never omit a known provider feature silently. Recent misses to guard against:
mask uploads, URL/file-id image references, streaming partial images,
background controls, moderation controls, output compression, Responses API
image flows, async video job ids, output URL expiry, watermark/provenance
behavior, and provider-specific region/commercial caveats.

## Image Connector Pattern

Image providers are usually synchronous, but output transport differs.

Implementation requirements:

- Validate prompts, model ids, dimensions/aspect/quality/output format, input
  image counts, and input image sources before provider calls.
- Support only documented input sources. If StackOS accepts generated-asset refs
  but the provider also supports URL/file-id/base64, expose those only when the
  connector can resolve and audit them safely.
- Persist returned base64 bytes or downloaded provider URLs under generated
  assets. Agent-facing output should return artifact URLs, not raw base64.
- Strip or redact raw provider payload fields that contain generated bytes,
  secrets, signed URLs, or unstable internal ids.
- Keep pricing estimates conservative when the provider bills by tokens or has
  dynamic pricing. Label estimates as guardrails when invoices are authoritative.

OpenAI-specific expansion example: `utils.image.generate` and
`utils.image.edit` currently expose Image API generation and generated-asset
reference edits. Before adding OpenAI masks, streaming, Responses API image
flows, `background`, `moderation`, or `output_compression`, add schema fields,
connector validation, provider tests, manifest metadata, docs, and signoff for
that specific feature.

## Video Connector Pattern

Video providers are async unless official docs prove otherwise.

Implementation requirements:

- Submit one provider job from one action call and preserve the provider job id
  in action metadata/audit.
- Poll documented status endpoints until success, failure, timeout, or
  connector cap. Normalize provider statuses into safe output and error
  details.
- Download provider output immediately when URLs expire or are signed. Persist
  video bytes, thumbnails, last frames, and any audio output under generated
  assets as separate artifacts.
- Record generation settings, model id, duration, resolution, aspect ratio,
  fps, input refs, watermark/provenance behavior, and provider request/job ids
  in metadata.
- Treat WAN-style 24-hour URLs, Veo-style short server retention, and similar
  provider retention windows as hard persistence requirements.
- Keep partial or still-processing jobs from looking successful. A nonterminal
  provider status must return a blocked/failed action result with repair or
  retry context unless the action is explicitly modeled as submit-only.

## Tests And Signoff

For each delivered provider/action, add focused tests before claiming done:

- Wrapper tests with mocked HTTP for success, provider error, malformed output,
  auth failure, rate limit/retry metadata, and generated-asset persistence.
- Connector validation tests for schema edges, enum mismatches, count caps,
  unsupported modes, cost estimate, and credential-required behavior.
- MCP/action tests proving `action.describe`, `action.validate`, direct
  availability/readiness, and run-plan-granted `action.execute` behavior.
- Plugin manifest inventory tests for provider/action counts, deferred state,
  missing connector behavior, and capability metadata.
- Stale-ref scan across manifests, workflow templates, tests, docs, and
  operator-facing setup text.
- Documentation updates in this directory, `docs/action-executor.md`,
  connector quality matrix, and `CHANGELOG.md`.

Final signoff must be performed by an independent reviewer against current
official provider docs and the actual code diff. The reviewer should report
blockers first, then non-blocking risks, then pass/fail verdict.

## Operator Confirmation Template

Before unblocking a provider build ticket, ask:

`Please confirm for <provider>: account created, billing active, and API key
stored in StackOS for provider key <provider-key>. Do not paste the key.`

After confirmation, verify provider readiness through StackOS auth/readiness
operations. If credentials or billing are missing, keep the build ticket
blocked and leave actions deferred.

## Deferral Rules

Use deferred action metadata when a provider is planned but not executable:

- no connector code exists,
- official docs are incomplete or console-only facts are missing,
- operator registration/billing/credential confirmation is missing,
- pricing is unknown for a spend-bearing action and no conservative budget
  guardrail or explicit operator waiver is recorded,
- safety constraints are unknown for a spend-bearing action,
- async status/download semantics are not implemented, or
- tests and docs are not delivered in the same change.

Deferred actions must not include `config.connector`. They must include
`execution_mode`, `deferred_reason`, provider docs or setup links, and
capability metadata that makes the non-executable state obvious.
