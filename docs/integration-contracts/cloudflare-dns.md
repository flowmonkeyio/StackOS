# Cloudflare DNS Connector Contract

Verified: 2026-07-15

This connector exposes Cloudflare zone discovery and individual DNS record
CRUD only. The agent chooses the zone, record, payload, and operation. StackOS
validates the published provider contract, resolves the daemon-held token,
executes that exact call, preserves the provider receipt, and records the
normal action audit. It does not add a Cloudflare-specific zone list, default
zone, preliminary read, second confirmation, or policy decision.

## Official documentation ledger

- [List zones](https://developers.cloudflare.com/api/resources/zones/methods/list/)
- [DNS records API](https://developers.cloudflare.com/api/resources/dns/subresources/records/)
- [Create an API token](https://developers.cloudflare.com/fundamentals/api/get-started/create-token/)
- [API token permissions](https://developers.cloudflare.com/fundamentals/api/reference/permissions/)
- [API rate limits](https://developers.cloudflare.com/fundamentals/api/reference/limits/)
- [API error responses](https://developers.cloudflare.com/fundamentals/reference/error-responses/)
- [Official OpenAPI schemas](https://github.com/cloudflare/api-schemas)

The wire base is `https://api.cloudflare.com/client/v4`. Requests use
`Authorization: Bearer <daemon-held-token>` and `Accept: application/json`.
JSON mutations also use `Content-Type: application/json`.

## Authentication

Provider and connector key: `cloudflare`. The `api_token` auth method stores
only the token as raw secret payload. A least-privilege token normally needs
Zone Read for zone discovery. Record reads accept DNS Read or DNS Write;
create, edit, replace, and delete require DNS Write. The operator scopes those
permissions when the token is created.

`auth.test` calls only `GET /user/tokens/verify`. A successful probe proves that
the token is active; it does not claim Zone Read, DNS Read, or DNS Write. The
result returns no token, token id, zone id, or zone name.

## Executable actions

| Action ref | Method and path | Risk |
| --- | --- | --- |
| `utils.cloudflare.zones.list` | `GET /zones` | read |
| `utils.cloudflare.dns.records.list` | `GET /zones/{zone_id}/dns_records` | read |
| `utils.cloudflare.dns.records.get` | `GET /zones/{zone_id}/dns_records/{dns_record_id}` | read |
| `utils.cloudflare.dns.records.create` | `POST /zones/{zone_id}/dns_records` | write |
| `utils.cloudflare.dns.records.edit` | `PATCH /zones/{zone_id}/dns_records/{dns_record_id}` | write |
| `utils.cloudflare.dns.records.replace` | `PUT /zones/{zone_id}/dns_records/{dns_record_id}` | write |
| `utils.cloudflare.dns.records.delete` | `DELETE /zones/{zone_id}/dns_records/{dns_record_id}` | write |

The connector sends one provider request for each action. Create, edit,
replace, and delete do not issue hidden zone/record reads. Syntactically valid
zone and record ids go directly to Cloudflare; provider scope and permission
remain properties of the selected API token.

## List contracts

Zone listing accepts the current public fields `name`, `status`, `type`,
`account.id`, `account.name`, `page`, `per_page`, `order`, `direction`, and
`match`. `name` and `account.name` retain Cloudflare's operator-prefixed value
syntax. Multiple zone types serialize as one comma-separated query value.

DNS record listing accepts the exact dotted filter names documented by the
provider:

- `name`, `name.exact`, `name.contains`, `name.startswith`, `name.endswith`;
- the equivalent `content.*` filters;
- `comment`, `comment.present`, `comment.absent`, `comment.exact`,
  `comment.contains`, `comment.startswith`, `comment.endswith`;
- `tag`, `tag.present`, `tag.absent`, `tag.exact`, `tag.contains`,
  `tag.startswith`, `tag.endswith`;
- `type`, `proxied`, `match`, `search`, `tag_match`, `page`, `per_page`,
  `order`, `direction`, `include_shadow_metadata`, `shadowed_by_name`, and
  `shadowing_name`.

The action performs one page request and preserves Cloudflare `result_info`.
It does not auto-page or reinterpret dotted parameter names.

Record get, create, PATCH edit, and PUT replace also accept the provider's
optional `include_shadow_metadata` boolean query parameter. Delete does not.

## Record bodies

Create, PATCH edit, and PUT replace accept the current 21 record types:

`A`, `AAAA`, `CAA`, `CERT`, `CNAME`, `DNSKEY`, `DS`, `HTTPS`, `LOC`, `MX`,
`NAPTR`, `NS`, `OPENPGPKEY`, `PTR`, `SMIMEA`, `SRV`, `SSHFP`, `SVCB`, `TLSA`,
`TXT`, and `URI`.

The body models common `name`, `ttl`, `type`, string `comment`, `proxied`, `settings`,
and `tags` fields plus the type-specific `content`, structured `data`,
`priority`, and `private_routing` fields in the official schema. CAA, CERT,
DNSKEY, DS, HTTPS, LOC, NAPTR, SMIMEA, SRV, SSHFP, SVCB, TLSA, and URI accept
the provider's optional formatted `content`, optional component `data`, both,
or neither on POST, PATCH, and PUT. StackOS type-checks supplied content and
data fields but does not parse provider-formatted record grammar or require
component completeness that Cloudflare's request schema does not require;
Cloudflare remains the semantic authority. The eight simple-content variants
also preserve Cloudflare's optional-content request contract; the manifest
records the provider's `ipv4`, `ipv6`, and `hostname` format annotations for A,
AAAA, and MX without adding separate local grammar rules. PATCH retains the
current generated-contract requirement for `name`, `ttl`, and `type`. MX and
URI priority is required and ranges from 0 through 65535.

TTL is `1` for automatic or 30 through 86400 seconds. Cloudflare can enforce a
higher plan-specific minimum. Structured numeric bounds are pinned to the
official OpenAPI, including LOC altitude, latitude/longitude, precision and
size; 8/16-bit DNS component ranges; and `N/S` plus `E/W` direction enums.
Provider-added response fields remain visible even when they are not request
fields.

## Responses and errors

Successful list/get/create/edit/replace actions preserve the full Cloudflare
`success`, `errors`, `messages`, `result`, and, for lists, `result_info`
envelope. Delete follows its separate documented response and accepts the
minimal `result.id` receipt; StackOS does not invent missing common-envelope
fields.

HTTP failures preserve safe standard Cloudflare error arrays, RFC 9457 problem
details, or a bounded text fallback. HTTP 2xx with `success:false`, missing
`success:true` on a common envelope, wrong result shape, malformed JSON, and
non-JSON success bodies are failures. `CF-Ray`, `Ratelimit`,
`Ratelimit-Policy`, and `Retry-After` are retained in safe metadata when
present. The API token is removed from returned errors and audit data even if a
provider-controlled message echoes it.

Read actions use the shared bounded retry behavior for transport, 429, and 5xx
failures. Mutations use zero automatic retries because Cloudflare does not
document an idempotency-key contract for these endpoints. A transport or 5xx
mutation failure is marked `outcome_unknown` and returns record-list
reconciliation guidance. A mutation HTTP 2xx without a trustworthy documented
success receipt is classified the same way with `retry_safe=false`; an explicit
provider rejection is not described as an unknown effect. The agent decides
whether and when to inspect or retry.

## StackOS execution boundary

The actions are available only through the generic action catalog and
`action.run`/granted `action.execute`; there is no provider-specific MCP tool.
The normal shared credential, direct-write intent/idempotency, workflow grant,
response-file, redaction, and audit mechanics apply. Those mechanics record an
authorized call; they do not choose the zone, inspect the record first, or
decide whether the requested DNS change is desirable.

## Deliberately excluded

Zone create/edit/delete, zone settings, DNSSEC, batch record operations,
imports/exports, scans, analytics, load balancing, Workers, rules, certificates,
registrar, R2, and every other Cloudflare product are outside this first
contract. Add them only as separately reviewed actions.

Mocked tests prove exact paths, methods, headers, filters, pagination, all 21
record variants, boundary validation, raw receipts, structured failures,
mutation non-retry behavior, credential redaction, auth probing, generic action
audit, and absence of connector-specific gates. A disposable live zone remains
operator-owned release evidence for real permissions, plan behavior, advanced
record types, throttling, and ambiguous network outcomes. API acceptance is not
proof that DNS changes have propagated globally.
