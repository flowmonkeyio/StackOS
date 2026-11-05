# Stopped source-admission correction

As of 2026-11-04 18:13:19 UTC, no accepted code change is active or installed.

The original implementer produced a bounded source-admission/preflight candidate. The first fresh independent reviewer rejected regex/automatic-semicolon handling, incomplete newline RHS admission, and loss of the parent's protected-value policy at the broker boundary. The same implementer made one correction pass, including private bounded stdin transport for that policy. A separate fresh reviewer rejected the second candidate because JavaScript HTML-style comments could still be mistaken for executable references and disclose labelled literal content.

The hand-written JavaScript lexer/exemption approach has reached two independent logic rejections and is stopped. Do not patch another keyword/comment case and silently reset its budget. The common cause is use of an incomplete JavaScript grammar as a credential-admission boundary. A materially different design must provide validated syntax classification or preserve strict rejection without admitting literal content; it must retain this history and undergo independent review.

Both frozen candidates, incremental patches and reviewer-authored reports remain in this directory. The implementer also encountered one local TPF/heredoc no-op; readback detected it before review, and direct patches were used thereafter. No test was written or executed because logic approval never occurred. Static syntax/lint checks are not security approval or real-flow proof.

Only this correction's five source/documentation changes were restored after verifying exact current hashes against the second frozen candidate. `stop-result.json` confirms all seven initial source/test hashes and the full Git status match the pre-task baseline. All pre-existing uncommitted work remains. No MYB worktree, StackOS business run, managed database, provider, deployment, merge or installation was changed by this task.

The preflight-before-run-creation shape was source-reviewed positively, but was not retained alone as a claimed complete fix. The installed false positive and missing-designer recovery symptom remain unresolved. PostgreSQL plan 59/run 54 remains the previously verified aborted run; this correction did not retry it.
