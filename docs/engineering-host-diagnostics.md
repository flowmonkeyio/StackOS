# Engineering host failure diagnostics

The isolated helper retains bounded diagnostic metadata through its existing
checked-output stream. `host.diagnostic` records an allowlisted host code, a
bounded HTTP status (100–599) when the pinned protocol supplies one, a boolean
`will_retry` flag when supplied by an error notification, and a fixed stage.
Unknown values become `unknown` or `null`; raw error text, details and exception
representations are never copied. Decoded-input admission still happens before
projection, and the final encoded event is checked before any sink write.

`host.error` preserves the existing unknown-authority RPC rejection marker.
Its RPC method and standard JSON-RPC error code now come from finite allowlists;
custom numeric codes become `null`. Terminal turn errors and error notifications
are distinct stages. Generic worker/reviewer failures also retain the last main
controller activity. That stage is context, not proof of which operation caused
the failure; cleanup can fail while unwinding an earlier activity.

Confined observations retain validated non-denial notification diagnostics before
exactly one final completion row. The shared finite schema rejects arbitrary extra
rows, duplicate completions, denial diagnostics and RPC-rejection records. Size,
exit, timeout and existing refusal/custody checks still apply. Observation request
admission, command dispatch and result emission have separate fixed activity stages.

Diagnostic events and retry flags do not grant authority, clear a denial,
establish completion, or change recovery allowances. Typed host denials and
unclassified-failure holds retain their existing behavior. No additional host
tools, filesystem roots, network permissions or fallback path is introduced.

These diagnostics help future authorized attempts. They cannot reconstruct
discarded details from an earlier failed host, and their offline verification
does not establish successful model execution or a full delivery workflow.
