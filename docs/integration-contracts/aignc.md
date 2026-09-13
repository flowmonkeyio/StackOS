# AIGNC API Contract

Reviewed: 2026-09-13. Evidence is the operator-supplied supplier guide titled
“Документация по работе с AI API” in the integration request and the supplier's
grounding/structured-output follow-up, both reviewed on 2026-09-12, plus the
operator-directed service-domain replacement on 2026-09-13. These were
provided directly; no public documentation URL is independently verified.
A manual live text smoke, installed StackOS action checks, and raw image cURL
response on 2026-09-13 established the results recorded below. This document
records the reviewed contract
without copying credentials or commercial rates.

## Source Ledger

| Topic | Supplied evidence | Boundary |
| --- | --- | --- |
| Service and authentication | Guide section 1 specifies bearer API key authentication; operator-directed current base URL is `https://cli-api.f2nd.com/v1` | Fixed HTTPS service; no caller-selected endpoint or authentication headers. |
| Endpoint replacement | Operator instruction on 2026-09-13 replaces the original supplier-guide host with `cli-api.f2nd.com` | Provider key `aignc`, existing account bindings, authentication and action behavior remain unchanged. |
| Live text smoke | Manual `/usr/bin/curl` request on 2026-09-13 to the current `/chat/completions` endpoint, using the exact operator-supplied text payload and the same attached Account | HTTP 200; returned model `gemini-3.8-flash`, text `Париж.`, finish reason `stop`, reported `google_searches: 0`. Covers this request only. |
| Models | Sections 2 and 5: 20 named model IDs and `GET /models`; installed StackOS action on 2026-09-13 | Live inventory returned the 20 advertised IDs; StackOS projects the response as `data: [{id}]`. |
| Text | Section 3.1: `POST /chat/completions`, explicit `model`, `messages`, `max_tokens` | Nonstreaming text messages only. |
| Google grounding | Section 3.2: `tools: [{google_search: {}}]`, usage counts, `grounding_metadata` | Explicit opt-in on reviewed Gemini text models. Search execution remains provider-controlled. |
| Grounding follow-up | Supplier follow-up: grounding source chunks/supports, Google redirect links, and `usage.google_searches` | Preserve source/support correspondence and reported search counts; neither enabling search nor a source title proves a search execution or final destination URL. |
| Structured-output follow-up | Supplier guidance for `response_format` and explicit source fields in an output schema | `response_format` remains unsupported and rejected by StackOS; no structured-output schema or source-field policy is added. |
| Images | Section 3.3 documents base64 JPEG in assistant content; raw cURL on 2026-09-13 returned null content and one JPEG data URI in `message.images[0].image_url.url` | Both forms are supported for `gemini-3.1-flash-image`. One inline JPEG; 1408×768 is an observed example, not an output guarantee or size control. |
| Audio | Section 3.4: `input_audio`, WAV/MP3/AAC/FLAC/OGG, Flash 3.8 and 3.7 | Only the two explicitly demonstrated model IDs are enabled for audio. |
| Diagnostics | Section 4: `cf-aig-log-id` | Preserved as `provider_request_id`; commercial headers are excluded. |
| Errors, quotas, retention | Not specified by the guide | No verified provider rate limits, pagination, retry/idempotency, duration/byte limits, moderation, retention, region, or commercial-use contract. |

The current service URL is an operator-supplied destination, not evidence of a signup,
console, API-key, or documentation page. Provider setup exposes it only as a
directional fallback and directs the operator to the supplier for account
instructions. The review date records the supplied contract review, not live
certification of those pages.

The supplier follow-up's HTTP IP-address example does not change the configured
service contract. Following the operator's 2026-09-13 endpoint replacement,
all AIGNC requests use `https://cli-api.f2nd.com/v1`; the connector accepts no
endpoint override.

## Ownership And Setup

Utilities owns provider `aignc`, auth method `api_key`, and four actions. The
auth method takes one required secret field in raw format. The daemon resolves
it and sends `Authorization: Bearer …` to the fixed AIGNC service. The supplied
guide does not establish provider-grant introspection, so permission evidence
is `unavailable` with `provider_enforced` enforcement.

Connect the supplier-issued key through **Connections → AIGNC → API key** in
the bound StackOS project. Agents resolve a safe execution target with
`toolProfile.resolve` and use `action.run` for one explicit request or
`action.execute` inside a started, granted run-plan step. The provider contributes
no direct MCP tools, separate agent, prompt rewriting, model selection, fallback
routing, workflow, or model conversation store.

The reusable wrapper in `stackos/integrations/aignc.py` owns transport, safe
response parsing, and generated image bytes. Static provider IDs and local
request caps live in `stackos/integrations/aignc_contract.py`; the manifest and
connector share that inventory. `stackos/actions/aignc.py` validates explicit
inputs, resolves project-scoped audio artifacts, and uses existing generic
image artifact registration. The shared executor owns credentials, grants,
action-call audit, idempotency, and response files.

## Executable Actions

| Action ref | Risk | Required input | Output |
| --- | --- | --- | --- |
| `utils.aignc.models.list` | `read` | None | `data: [{id}]`, optional provider request ID. |
| `utils.aignc.chat.complete` | `cost` | `model`, `messages`, `output_limit` | Text, requested/returned model, finish reason, operational usage, optional grounding and request IDs. |
| `utils.aignc.image.generate` | `cost` | `prompt` | Persisted JPEG in `data`, generic image artifact ID/ref, model and operational usage. |
| `utils.aignc.audio.analyze` | `cost` | `audio_artifact_id`, `model`, `instruction`, `output_limit` | Text analysis/transcription, requested/returned model, finish reason, operational usage and request IDs. |

Model listing executes synchronously and retains the caller-surface output
policy. Chat, image generation, and audio analysis use
the shared background-action mechanism also used by FTP and S3 transfers. Their
initial `action.run` or `action.execute` response accepts the request with status
`running`, a durable `action_call_id`, `poll_operation: actionCall.get`, exact
`poll_arguments`, and `next_poll_after_ms`. Poll that call until its stored
status is terminal, then read the normal response file and any image artifact
listed in the table. The StackOS action ID identifies execution; the completion
`id` and `provider_request_id` identify provider evidence and are not polling
handles. The provider still receives one synchronous, nonstreaming HTTP request.

Keep the current run-plan step running while polling. Step success or skip
recording rejects linked running actions and returns polling repair context.
Failure and blocked recovery retain their normal semantics. A repeated submit
with the same idempotency key and identical request retains the same action ID.
An idempotent submit may replay the initial acceptance receipt even after the
call has finished; `actionCall.get` returns the authoritative current status
and terminal result. Do not reuse a request key for different input. Polling
never submits a second provider request. There is no provider cancellation endpoint,
resumable AIGNC job, or automatic retry. After daemon restart, the shared executor
marks orphaned running calls failed with an unknown outcome.

`cost` is the existing action risk classification for a provider generation
request. At the operator's direction this integration has no pricing tables,
estimates, budget kind, budget enforcement, or provider cost/header projection.
It does not interpret token counts as money. Shared execution mechanics retain
their existing behavior; AIGNC supplies no financial calculation or ledger.

Model IDs are AIGNC identifiers. Similar names do not establish identity or
capability equivalence with an upstream Google, Anthropic, or OpenAI service.
Text uses the 19 reviewed non-image IDs; image generation fixes
`gemini-3.1-flash-image`; audio accepts `gemini-3.8-flash` or
`gemini-3.7-flash`. A newly discovered `/models` ID remains unavailable for
generation until its contract is reviewed. The agent selects the text/audio
model explicitly. Requested and returned model IDs remain separate because
the supplier grounding example requests a reasoning suffix and returns the
base model name.

Text requests contain 1–100 `system`, `user`, or `assistant` messages with string
content only. Messages, prompts, and audio instructions are each limited to
32,000 characters. `output_limit` is required for chat/audio and bounded to
1–8192; image requests default it to 2048. The wrapper translates this safe
action field to provider `max_tokens`; the provider field itself is not an
accepted action-input alias. These are StackOS request caps,
not verified provider context limits. Unknown fields, raw tools, streaming,
`response_format`, embeddings, function calling, image references/editing,
image-size controls, and raw audio data/paths are rejected.

Each generation action also accepts `read_timeout_seconds`, an integer from
60 to 1800, defaulting to 600. It limits inactivity while waiting for provider
response data; it is not a total execution deadline or an upstream job-poll
timeout. Connect and pool waits are each limited to 15 seconds and writes to
180 seconds. Model listing retains its 180-second HTTP timeout. These are
local client limits, not supplier guarantees. The timeout field is consumed
inside the connector and is never sent as part of the provider request body.
Background submission allows MCP and CLI callers to return promptly; the agent
uses short `actionCall.get` reads instead of holding a conversation call open.

For grounding, the agent sets `google_search: true`; the wrapper alone translates
that boolean to the documented provider `tools` entry. Normalized
`grounding_metadata` preserves `webSearchQueries`, `groundingChunks` with source
URL/title fields, and `groundingSupports` linking answer segments to their
source-chunk indices. Source order and indices retain their provider meaning;
they are not renumbered into different source associations. Malformed source
slots remain empty placeholders so valid indices keep their positions.

Segment offsets retain provider coordinates; they are not recomputed after
secret redaction. If redaction changes answer length, `startIndex` and
`endIndex` may no longer address the returned sanitized text. Consumers must
validate spans before using them for highlighting or citation placement.
The sanitized segment text and its source-chunk indices remain separate
evidence; do not guess corrected offsets or weaken redaction to preserve them.
Malformed support entries and invalid source indices are omitted.
Segment `startIndex`, `endIndex`, and `text` are retained only when supplied
and valid. Missing or invalid offsets are not synthesized; usable segment text
can remain without offsets. Supports without a usable segment or source indices
are omitted.

Google grounding redirect URIs are retained unchanged after normal credential
sanitization. A domain in a source title does not establish the final URL behind
a redirect. StackOS does not follow these links, resolve redirect destinations,
or issue another search. If the final destination is needed, the calling agent
can make an explicit request through an appropriate retrieval tool.

`google_search: true` expresses request intent; it does not prove that the
provider searched. A returned `usage.google_searches: 0` means the provider
reported zero searches. An absent count stays unknown and is not filled with
zero. Preserve source metadata and usage as distinct evidence rather than
inferring one from the other.

The structured-output follow-up advises callers to exclude source fields such
as `search_sources`, `sources`, `links`, or `urls` from a provider JSON Schema
when using Google grounding, and to read sources from `grounding_metadata`.
The current StackOS action
does not expose `response_format` or JSON Schema output, so the supplier's
schema-with-sources issue does not affect this executable surface. Any future
structured-output support needs a separately reviewed explicit provider
contract. StackOS does not add required source fields, rewrite prompts, infer
business output schemas, or inject a source-selection rule into this connector.

Generated speaker labels and transcript structure are text from the provider;
they do not establish structured diarization, timestamps, or verified speaker
identities.

## Managed Audio And Image Artifacts

Audio analysis reads the artifact row in the current project, resolves its URI
inside the configured daemon `generated-assets` directory, checks audio type,
format and framing, then encodes the bytes and sends them to AIGNC for the
explicit request. It accepts existing
audio artifacts, including audio stored by a provider download action. Artifact
kind alone is insufficient: a `communication-media` artifact can contain valid
audio, and a row labeled `audio` cannot authorize a file outside managed assets.
The local audio cap is 20 MiB. The guide's “several hours” claim does not override
that byte cap or establish a supported duration guarantee.

For a host-local recording, use the host's separately authorized filesystem
capability to copy the selected file below the configured daemon data directory,
for example `<data-dir>/generated-assets/audio-inputs/project-1/meeting.wav`
for project 1. Use a distinct project directory and filename. The default data
directory is `~/.local/share/stackos`; use the running daemon's actual configured
directory. A workspace binding supplies project scope, not filesystem access.
Do not put paths, bytes, or base64 into the AIGNC action input.

Register that intentional durable input through `artifact.create` in a running
step with the explicit artifact write grant. For example, this operation input
registers a file that has already been staged:

```json
{
  "plugin_slug": "utils",
  "kind": "audio",
  "uri": "/generated-assets/audio-inputs/project-1/meeting.wav",
  "name": "Meeting recording",
  "mime_type": "audio/wav",
  "provenance_json": {"source": "operator-selected local recording"}
}
```

`artifact.create` registers metadata; it does not copy or upload the file. Use
the returned artifact ID in the audio action payload. For a repository CLI
session, inspect the current schema first with
`.venv/bin/stackos ops call action.describe --project <project-id> --input describe-aignc.json`,
where the input file contains `{"action_ref":"utils.aignc.audio.analyze"}`.
A granted execution input can then contain:

```json
{
  "action_ref": "utils.aignc.audio.analyze",
  "input_json": {
    "audio_artifact_id": 42,
    "model": "gemini-3.8-flash",
    "instruction": "Transcribe the recording and summarize the decisions.",
    "output_limit": 1000
  }
}
```

Pass the actual resolved credential/profile reference and active run token
according to `action.describe` and the shared [action executor](../action-executor.md)
contract. Illustrative artifact ID `42` must be replaced by the returned
project artifact ID.

Image output accepts either the guide's bare base64 string in assistant
`content`, or the verified response with null/empty content and exactly one
`images` entry of type `image_url`. That entry's `image_url.url` must start
with `data:image/jpeg;base64,`. Nonempty text alongside an image, multiple or
malformed image entries, remote URLs, and other media types are rejected;
StackOS does not fetch image URLs. Chat and audio still require string content.
Image bytes are strictly base64-decoded, checked for JPEG start/end framing,
capped at 20 MiB, persisted under generated assets, and
registered as a generic image
artifact during repository-backed execution. Base64 never appears in the
normalized response or action audit. These framing checks do not decode pixels
or guarantee a valid playable media stream. The provider JSON response is
bounded to 30 MiB.

## Response And Recovery

Normalized text output includes `requested_model`, `returned_model`, `text`,
`finish_reason`, optional completion `id`, optional `provider_request_id`, and
present operational usage fields. Usage uses safe names `prompt_count`,
`completion_count`, `total_count`, `reasoning_count`, `cached_count`, and
`google_searches`, so shared credential redaction does not hide token counts.
Missing counts stay absent. Supplier `cost` and `X-Model-Pricing` are discarded
before normalized output, metadata, generated artifact metadata, or audit.
Raw response mode returns normalized connector data, not the original provider
body. MCP/REST external results use the shared response-file envelope by
default; CLI defaults to inline normalized JSON.
These are terminal outputs. A background acceptance or pending poll is not a
completed research result. AIGNC progress can describe an execution phase, but
provides no token stream, percentage, search counter, or draft answer while the
upstream request is running.

Provider failures use the shared `ActionConnectorError` path with sanitized
`provider_status_code`, `provider_error`, and request/rate-limit diagnostics
when present. Neither model-list GETs nor generation POSTs are automatically
retried. An ambiguous
timeout may have executed upstream: inspect retained action audit and the
provider request ID before deciding whether another explicit request is
appropriate. AIGNC has no documented idempotency guarantee. Invalid model,
capability, artifact, and request shape fail before provider dispatch.

## Verification Boundary

Mocked tests cover the supplied wire examples, strict inputs and model
capabilities, daemon authentication, safe error/diagnostic projection, malformed
responses, usage and grounding, model discovery, image persistence, audio
containment, and generic direct/granted execution and audit. Contract/catalog
tests keep provider setup and action schemas aligned with this document.
These establish implementation behavior against supplied and captured evidence.
Generation on other model IDs, longer audio, moderation and provider retention
remain unverified. No supplied credential is
stored in code, fixtures, documentation, or verification output.

On 2026-09-13, a manual cURL request to
`https://cli-api.f2nd.com/v1/chat/completions` succeeded with HTTP 200 using the
operator-supplied Russian text payload, model `gemini-3.8-flash`, and provider
`max_tokens: 100`. The response was `Париж.`, with finish reason `stop` and zero
reported Google searches. The key for the same attached daemon Account was
resolved only in the local process; the smoke receipt contains no credential.
The local receipt is
`/private/tmp/stackos-aignc-live-smoke/f2nd-curl-result.json`. This verifies
connectivity, authentication, and one text request on the replacement endpoint;
it does not establish other model access.

Installed StackOS action checks on 2026-09-13 subsequently verified model
listing, plain text, Google grounding (two reported searches, four source
chunks and four supports), and analysis of a synthetic spoken WAV. Image action
execution reached HTTP 200 but failed because the parser expected string
content. A raw cURL response confirmed `content: null` and one inline JPEG in
`message.images`; the decoded image was 1408×768 and 275,946 bytes. The parser
regression fixture mirrors that wire shape without committing live image bytes.
Local raw evidence is
`/private/tmp/stackos-aignc-f2nd-live/image-raw-curl-20260913T074004Z.json`.
Offline replay of that exact raw response through an isolated source StackOS
MCP `action.run` succeeded: the generic image artifact preserved all 275,946
bytes, response files and audit excluded base64 and provider cost fields, and
idempotent replay made no second request. The local receipt is
`/private/tmp/stackos-aignc-f2nd-live/image-fixed-stackos-offline-replay-receipt.json`.
This proves the parser and shared artifact path separately from verification of
a newly installed desktop build.

The corrected installed build subsequently completed live image action 9186,
persisting a valid 1408×768 JPEG as project artifact 2714. Long grounded chat
action 9187 then completed in 105.342 seconds with `finish_reason: stop`, 6,401
words, 14 reported searches, 53 source chunks, and 148 supports. This probe used
the earlier synchronous action path and 180-second read timeout; it does not
establish the behavior of a newly installed background-action build.

That research response reported 12,776 completion tokens despite an 8,192-token
request limit, and 14 searches despite the supplier follow-up's stated 0–10
range. StackOS preserves these reported counts rather than clamping them.
`output_limit` is a bounded request parameter, not verified enforcement by the
proxy. Grounding structure passed index/span checks; the research claims and
source quality were not independently verified. The local receipt is
`/private/tmp/stackos-aignc-f2nd-live/long-grounded-research-9187-summary.json`.

After the operator installed build `20260913T083248Z`, live action 9188 verified
the background path through native StackOS MCP. Submission returned `running`
with its action ID and polling guidance in 5.653 seconds. Two separate
`actionCall.get` reads observed the `requesting` phase before a terminal read
returned `success` and the standard response-file metadata without polling
hints. The connector completed in 71.574 seconds; the request omitted
`read_timeout_seconds` and used the installed 600-second default. This verifies
acceptance and completion through polling, but does not exercise the full
timeout duration.

The response contained 6,266 words, eight reported searches, eight queries,
37 source chunks, and 47 grounding supports, with `finish_reason: stop`.
It again reported more completion tokens than requested: 11,894 against an
8,192-token request limit. The test made one provider submission and did not
retry. Its local receipt and report are
`/private/tmp/stackos-aignc-f2nd-live/background-live-9188-summary.json` and
`/private/tmp/stackos-aignc-f2nd-live/long-grounded-research-9188.md`.
Generated research claims remain unverified; these are execution and response
structure measurements.

Independent inspection found valid source-chunk indices and all 47 sanitized
segment texts present in the answer. Only 31 of 47 provider byte spans directly
matched that sanitized answer: one redacted segment changed length by four
bytes, shifting subsequent text. This is an existing grounding/redaction
limitation, separate from background execution; the receipt does not certify
complete citation-offset integrity.
