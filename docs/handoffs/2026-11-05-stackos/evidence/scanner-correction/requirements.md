# Original requirements for the bounded source-admission correction

Correct StackOS's verified false positive on ordinary JavaScript expressions in source snapshots, and perform identical snapshot validation before creating or starting a new run.

The actual unchanged source at frozen head `0269fe23cbc06855df3cf0844fefc01c0df700ac` includes token variable expressions, null assignments, and runtime Authorization template expressions. Their source syntax must not be mistaken for embedded credential values. Actual source bytes must remain intact. Do not change the application source or exclude unrelated artifacts merely to evade the existing hold.

Preserve rejection of literal credentials, known inherited credential values, private/report/host provenance, unsafe paths and symlinks. Preserve strict general log/prompt redaction, reviewer isolation, relevant outcome inspection tools, manifest integrity, role write limits, model configuration and recovery budgets/history. When syntax cannot be safely classified, fail closed. Admission must remain consistent across relevant consumers so an accepted source is not corrupted or blocked again at a later source-reading boundary.

Preflight must use the same substantive snapshot admission checks as dispatch, before new run creation/start or worker launch. Dispatch must still validate current bytes; a passing earlier preflight is not authority to accept changed or unsafe contents. A preflight failure must not create an empty business run. Existing run history and ownership must remain intact.

Keep this correction bounded to the existing four-session delivery implementation: original requirements to fresh planner, separate implementer, fresh independent reviewer of actual code/outcome without executor reports, and fresh test writer only after approval. Do not alter recovery policy or silently reset budgets. No unrelated source changes.

Do not launch, retry, create, resume or mutate the PostgreSQL business run. Plan 59/run 54 is aborted after settled pre-dispatch failure; no worker ever launched. Plans 57/52 and 58/53 retain earlier failures. Do not change any migration worktree, managed database, provider, deployment, merge state or installation in this correction assignment.

Implementation and independent logic review precede test writing/execution. A fresh reviewer receives this original requirement packet and actual source/baseline artifacts only, without implementer reports or plans. After approval, verify focused existing safety checks and any authorized necessary regression coverage. Report source evidence, verification limitations and any remaining blocker without equating test success to installed-app proof.
