# Independent integration review

Fresh Astra session `/root/astra_pr21_integration_review`, 5 November 2026. The reviewer read actual tracked/untracked source against `github/main` and PR21 head `c54575d7`, applicable instructions and original requirements. It did not read implementer/execution reports or run tests/hosts/installations. The following records its returned findings; this file is a coordinator transcription, not a reviewer-authored source file.

Verdict: suitable for an explicitly incomplete draft PR21 handoff; **not production execution approval or completion of the original requirements**. No merge-specific required-run regression found.

1. `docs/agent-presets.md:234` described only delivery and reviewer while the 0.7.0 workflow/controller require four roles plus handoff. The coordinator reconciled this paragraph after review; no runtime change.
2. `delivery.py:481` supports project defaults/task overrides, but `delivery.py:1400` rejects model changes on resume and `delivery_recovery.py:194` requires the original model. Replacements keep the frozen role mapping. Separate recovery-model configuration is unsupported.
3. Operational recovery remains narrower than requested: `delivery.py:525` distinguishes design/execution, while `delivery_recovery.py:445` needs an original designer thread. Preparation failure before its first thread exists stops without the requested operational retry.
4. Strict scanning at `delivery.py:436` and `controlled_workspace.py:598` remains a real execution blocker. No parser substitution or scanner relaxation was found.

Preserved: required step/plan/digest binding and completion/rollup gates in `tracker/mirrors.py:59` and `tracker/mutations.py:1347–1473`; both import sets; reviewer prompts excluding executor report/plan; fresh review before tests; denial/uncertain-custody holds. Tracker success rows still establish authored state, not authenticated independent execution.

This review is static. Line numbers describe the reviewed source at handoff time and may drift. See the handoff and validation for final evidence and remaining limits.
