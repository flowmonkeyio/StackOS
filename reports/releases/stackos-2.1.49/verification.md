# StackOS 2.1.49 release verification

Published from `858b690139b3d51f061e06c0c809ad8bc17a50e1`, tagged `v2.1.49`.
Build identity: `20261125T070359Z`.

## Included source

- Archive candidate validation and retained review recovery: `3dbf31ad`.
- Nested Git/worktree workspace ownership correction: `11787ac5`.
- Synchronized release version: `00822ebd`.
- Maintained API-key release loader and consistent local guidance: `858b6901`.

Server consolidation remains reverted on main and is excluded from the net
release change. The original checkout bindings, existing runs, and bridge scope
protection remain intact. After upgrading, a fresh worktree bridge can explicitly
connect the intended project; an already-bound session does not change projects.

## Verification

| Gate | Observed result |
| --- | --- |
| Runtime source signoff | `make signoff` exited 0: lint/format, types, complete Python suite, UI tests and production UI build. |
| Workspace correction | Real checkout/worktree regressions reproduced the original defect; focused delivery tests and independent 118-test review passed. |
| Version delta | 22 version/health tests and desktop doctor passed. |
| Release guidance/helper delta | Independent review approved; 31 tests, Ruff, JavaScript syntax and build-config checks passed. |
| Live release preflight | Maintained loader used the existing API configuration; Apple credential validation passed. |
| Canonical public build | Signed/notarized build exited 0; packaged CLI/browser launcher checks passed. |
| Independent artifact review | 104 source/UI/template matches across app, ZIP and DMG; 20 checks passed, including signatures, stapling, Gatekeeper and isolated CLI execution. |
| Staged publication | Six files uploaded successfully; six complete HTTPS downloads matched accepted SHA256 values. |
| Promotion | Stable installer promoted first, update feed second. |
| Live readback | Installer and feed full HTTPS downloads matched; ordinary updater URL served 2.1.49 without a cache-busting query. |
| Source tag | Remote annotated tag resolves to the exact build source above. |

The version-only and guidance changes reused unchanged runtime/UI evidence and
received current delta checks. The full gate's successful tool execution is
retained in the conversation transcript; its filtered output did not retain
complete suite counts. No suite counts are inferred.

## Release authentication

The production route is the existing App Store Connect API-key configuration,
privately loaded from `.env.local` or supplied through the caller environment.
`scripts/desktop-release.py` imports only Apple/signing file values, preserves
explicit caller values, clears conflicting methods and bypass flags, and
delegates to the existing package commands. No new credentials were required.

The initial `stackos-notary` lookup checked the wrong example authentication
method. Earlier successful evidence already documented this distinction.
`AGENTS.md`, the desktop distribution guide, release signoff guide and preflight
help now agree on the maintained API route; keychain profiles remain explicitly
labeled alternatives. Historical receipts were preserved.

## Scope limits

No local installation or shared-runtime restart was performed. This receipt
does not claim a fresh native updater installation/relaunch. Previous versioned
public artifacts remain available.

The machine-readable [receipt](receipt.json) records source/build identity,
artifact hashes, Apple submission IDs and publication action IDs. Private
execution evidence is under `/private/tmp/stackos-release-2.1.49`; credential
contents are not included in the release report.
