# Streamlined desktop backups — verification

Source delivery is ready. Existing backup configuration is preserved, new setup exports a portable recovery key, and the selected option 3 UI keeps controls stable through loading and actions. This delivery has not been committed, pushed, packaged, installed or released.

Workflow: project 1, run plan 718, run 712, task `workflow-718`, `engineering.tracked-delivery@0.4.0`. Backend/native technical design 2; UI attempt `ui-2`, contract 4, visual design 3, repair 2.

Final integrated candidate: `72935f7792c9a4c8f43db9991a831cec114c2592e26c697ca418fbce5ebc2ad7`, based on `1976d4bf96c8480652400e496534a7ed6b7e99d1`. The manifest records 27 source files and 89 generated bundle files. The tested isolated build output was copied byte-for-byte into `stackos/ui_dist`; independent review checked both trees.

## Delivered flows and ownership

The existing Python backup service owns storage, guarded initialization and archive behavior. The CLI provides local-only entry points; Electron owns native dialogs, OS-encrypted key custody and five no-argument bridge methods. The renderer only receives sanitized results and paths. Private key bytes do not travel through the renderer, REST, MCP or ordinary logs.

First setup selects a destination, saves the portable recovery key, caches an OS-encrypted convenience copy, initializes guarded settings and attempts the first backup. Existing or partial setup is not reset, re-keyed or silently overwritten. Backup and key export remain available afterward; an older setup without a usable convenience copy requests its original matching key. Restore uses a fresh inert folder and does not switch the running installation.

The Backups page uses a compact status band, separate backup/key export rows and an upward-opening Manage menu. Settings and history use canonical side panels. Button loading preserves dimensions; refreshed content remains visible; focus survives setup, menu Escape and settings save. Only explicitly changed settings are saved.

## Evidence and validity

| Check | Result | Scope and reuse boundary |
| --- | --- | --- |
| Isolated `make signoff` | 5,459 Python tests and 742 then-current UI tests; lint, typing and build passed | Exact source overlay on the recorded base. Python/native runtime has not changed since this gate. Its UI portion is superseded by the final gate. |
| Final `make test-ui-unit build-ui` | 752 UI tests / 115 files, vue-tsc and production build passed | Final selected layout and repairs; bundle matches the integrated manifest. Expected size warning only. |
| Focused backend | 130 tests; red-first 16 failures became 16 passes; independent 26-case proof | Existing/partial policy preservation, local-only output, failure behavior and filesystem guards. |
| Native desktop | 27 focused tests and desktop check/doctor passed | Electron action boundaries, key cache safety, cancellation, concurrency and native orchestration. |
| Real Electron / CLI round trip | Passed setup, restart/re-export and restore phases | Real Electron 39.8 safeStorage, canonical CLI and isolated synthetic data; restored synthetic credential decrypts. Deterministic dialog answers, not OS chooser visual automation. |
| UI geometry | 27 action and 9 initial-load cases passed | Real App/components/styles with synthetic operations/dialog results; 1280/390/320. Ran on the prior candidate after gutter/focus repairs; final placement-only change does not alter closed-menu geometry. |
| Current menu proof | 12/12 passed | Final candidate: Escape/settings/history/folder × 1280/390/320, zero document/main scrolling, stable controls and restored focus. |
| Settings save | Passed | One `backup.configure` call with only `interval_hours:72`; enabled settings focus after save, panel retained, Escape returns Manage. Unchanged by final menu placement. |
| Design review | Passed | [Design QA](design/design-qa.md), exact selected image, combined comparison, dark/light and narrow widths, no-project navigation. |
| Preservation | Passed | 2,706 protected baseline files unchanged; live backup policy, outcome and key-exclusion file hashes unchanged. |

See the evidence indexes under `evidence/backend`, `evidence/native`, `evidence/ui`, `evidence/browser` and `evidence/integration`. The independent acceptance receipt is under `evidence/review`.

## Historical failures and repairs

The initial workspace-wide signoff failed on unrelated, pre-existing untracked report Python lint. Those files were preserved. Verification then used an isolated checkout containing the base plus the exact candidate overlay; the isolated website build symlink was remapped within that checkout. This is recorded in the isolation receipt, rather than represented as a pass of the dirty workspace command.

Earlier red tests, superseded layouts and failed geometry runs remain historical evidence. Admitted issues were repaired: stale action feedback, setup focus replacement, initial Restore movement, shared spinner dimensions, dropdown Escape, settings-save focus, outer-scrollbar width changes and the narrow-window menu scroll. Final accepted checks have no unresolved failure; earlier failures are not renamed skipped or deferred.

The native ticket's original documentation hash predates the final Export backup/Export key wording correction in `docs/backups.md`. Its runtime and test files remain byte-identical. The integrated manifest owns the current documentation identity.

## Limits and release state

The local preview at `http://127.0.0.1:5197/backup-qa?state=configured` uses synthetic backup records and native-dialog results, and rejects live `/api`/`/mcp` traffic. Navigation outside the backup flow is preview-only. It demonstrates the actual UI implementation without changing live backup settings.

The implementation provides portable files for device-loss recovery. It does not upload or synchronize them to cloud storage; the operator must keep the backup and recovery key somewhere accessible after device loss. Recovery exports are secret material and should be stored accordingly.

No installed-app replacement, daemon restart, package/signing or public deployment occurred. Packaging/install/restart proof belongs to a future release action; no lifecycle mechanism was modified here. OS-native chooser appearance was not visually automated. Existing live configuration and unrelated work remain untouched.

Rollback before release is the source/bundle diff over the recorded base; no data rollback is required because verification only used isolated fixtures. A later release should use normal package and lifecycle signoff while preserving the existing backup destination, recipient and policy.
