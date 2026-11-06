# Bounded host diagnostics — independent review contract

Implement only the separately authorized diagnostic improvement on the accepted checked-output baseline. Preserve the accepted checkout and every held source-admission/parser/policy candidate. Candidate: `host-diagnostics-candidate`, branch `codex/host-diagnostics-20261105`; baseline manifest has 2238 entries.

Retain only allowlisted host/local error codes, fixed controller stages, bounded HTTP status and boolean retry flags through the existing checked-output event stream. Never retain raw error messages, details, exception representations, stack traces, credentials or arbitrary diagnostic field values. Keep decoded-input and final-byte policy checks. Unknown values must map to a fixed unknown/null value, not be stringified. JSON-RPC codes and methods already retained must become finite allowlists as part of this diagnostic surface.

Cover host error notifications, terminal turn errors, rejected RPC responses, and the last controller activity for generic helper failures. The diagnostic stage is context, not proof of a root cause. A reported retry flag is host telemetry, never authorization or a retry instruction. Preserve typed denials, fail-closed unknown authority, scope/isolation, session/model selection, custody, original recovery budgets and all existing failure history. No automatic retry or broader capabilities.

Implementation first; fresh independent Astra logic/security review of these requirements and actual source/diff, without the executor report. Only after approval, a fresh Luna session may write/run offline synthetic fixtures. Verify actual event encoding/sink behavior, omitted arbitrary strings, malformed/unrecognized values, protected-value rejection, denial classification and unchanged recovery decisions. No real host/model, daemon, business run, installed app, raw auth/session reads, push, merge, or deployment. Do not replay the failed session. Stop this approach on a second independent rejection.

Acceptance is limited to diagnostic capture in offline fixtures. It cannot retrospectively identify the existing failed host's cause or establish successful end-to-end delivery.
