# Independent source gate: APPROVE

Reviewed the original requirements and clarifications against the actual frozen source in `evidence/live-tool-review-source`, with `evidence/post-lint-review-source` used only to establish the delta. No implementation reports, executor history, credentials, model turns, tests, database operations, builds, or installations were consumed or performed. Repository AGENTS/product guidance was consulted as review context; the pinned native CLI binary strings, npm package/wrapper, and the allowed generated protocol schemas supplied primary host-contract evidence.

This is approval of the source gate, not a claim that the app has been installed or that its live four-session flow has succeeded. No blocking source defect was found in this bounded review.

## Actual delta and reviewer boundary

The sole executable-source change between the two frozen snapshots is in `plugins/engineering/scripts/reviewer_isolation.py`: enable `code_mode_host`, remove it from the disabled list, verify the enabled value during feature discovery and effective configuration admission, and record it in isolation evidence. Snapshot manifest metadata also changes. No filesystem roots, network permission, broker operations, role tools, recovery allowances, or native-tool opt-ins are added by this delta.

- `reviewer_isolation.py:39` enables only the dispatcher. The separate `code_mode`, `code_mode_host_grpc`, `code_mode_only`, and `code_mode_tool_search` switches remain disabled at lines 49–52. Shell, native image access, browser/computer tools, plugins/apps, memory/history, permission requests, and multi-agent capabilities remain disabled in the same list.
- `reviewer_isolation.py:527` retains `app-server --strict-config`; lines 544–560 reject malformed discovery, a changed feature-name fingerprint, any disabled feature becoming enabled, or the dispatcher remaining disabled. Lines 573–644 still reject managed instruction/hooks contamination, disallowed or differing permission profiles, inherited instructions/MCP capabilities, unexpected context settings, and skills. Lines 600–603 separately verify both sides of the feature contract in effective configuration.
- `reviewer_isolation.py:460` keeps the review profile at minimal runtime reads plus the outcome snapshot, with no network. `outcome_command` at line 335 explicitly selects that profile for command execution and removes CODEX_HOME/HOME/TMPDIR from reviewer commands. The thread handshake at lines 672–709 independently requires the intended model, empty instruction sources, exact snapshot roots, never-approve policy, and read-only/no-network sandbox.
- Reviewer dynamic tools remain only `outcome_run` and `outcome_image` (`reviewer_isolation.py:269`). Request admission at line 147 binds requests to the active thread/turn and allowlisted tools; dispatch at line 783 validates arguments, confines image reads to the snapshot, and routes commands through the admitted profile. Worker-only edit tools remain separately conditioned on worker mode and stage (`controlled_workspace.py:442`, `:547`). Enabling a dispatcher does not admit those worker tools to a reviewer.

The allowed native CLI's embedded tool description explicitly describes a fresh V8 isolate with no Node, filesystem, or network access. Its embedded error text also identifies `features.code_mode_host` and `codex-code-mode-host` as prerequisites. This independently supports the distinction between the dispatcher and the tools it invokes. The generated `ThreadStartParams`, `ThreadStartResponse`, and `CommandExecParams` schemas support the named permission profile, dynamic tools, runtime roots, instruction-source response, and explicit command profile used here. This is contract evidence, not a live demonstration of dispatcher execution.

## Original flow preserved

The inspected controller still resolves task model overrides above project defaults above built-ins (`delivery.py:416`), persists requested/resolved model and literal thread evidence, and holds mismatches (`delivery.py:969`–1020). Reviewer prompting and dispatch exclude plan, executor report, role guidance and recovery feedback (`delivery.py:517`; `delivery_recovery.py:765`–796). Snapshot selection excludes known report/history/credential provenance and admits necessary changed guidance only as artifact data (`controlled_workspace.py:227`; `delivery.py:271`).

Independent approval remains before test writing. The executor cannot broker test edits, the test writer cannot broker implementation edits, and post-role digest checks reject premature tests, reviewer mutation, unapproved implementation changes, or unrelated test-writer files (`controlled_workspace.py:547`; `delivery_recovery.py:945`–984). Fresh identities are checked in the role loop; literal continuation is preserved where recovery requires it.

Recovery retains evidence-backed episode identity and comparison, one retry and at most one final allowance (`delivery_recovery.py:280`, `:329`). It archives receipts before reopening the same run (`:256`), consumes allowance before dispatch, routes design revisions through the original planner thread, replaces the executor only within the final allowance, and holds uncertain effects or denied authority (`:395`–649). The delta does not alter those rules.

Completion still requires executed observations and the same isolated reviewer assessing original requirements plus actual argv/stdout/stderr/exit evidence; green tests or self-report alone cannot complete handoff (`delivery.py:1340`–1431).

## Remaining runtime evidence

The next authorized post-review probe must establish that the native launch actually finds a compatible `codex-code-mode-host` companion and that the reviewer can invoke its scoped tools successfully. It should also demonstrate that forbidden native tools, host-state reads, writes and network access remain unavailable. The source admission fingerprint is a configuration contract, not a host-reported inventory of the eventual model tool surface.

The designated fresh test-writing/testing stage, coordinated build/install, and actual flow demonstration remain outstanding. Their absence at this source gate is not a source rejection and must not be represented as completed evidence. No permission denial or uncertain external action occurred in this review.
