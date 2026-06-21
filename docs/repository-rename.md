# Repository Rename Plan

Status: planning only. Do not move the local directory, change git remotes,
or rewrite MCP/Serena registrations without explicit
operator approval.

## Current Identity Inventory

| Surface | Current value | Release target |
| --- | --- | --- |
| Local repo path | `/Users/sergeyrura/Bin/content-stack` | Rename only after the operator approves local path migration. |
| Git remote | `git@github.com:flowmonkeyio/seo-stack.git` | `git@github.com:flowmonkeyio/stackos.git` after the repository exists or is renamed. |
| Python package | `stackos` | Keep. |
| CLI command | `stackos` | Keep. |
| Daemon/UI port | `http://127.0.0.1:5180/` | Keep. |
| Codex plugin slug | `stackos` | Keep. |
| MCP server name | `stackos` | Keep. |
| Codex Serena MCP entry | `serena` | Keep. Per-session stdio uses `--project-from-cwd`, so repo renames do not require a server rename. |
| StackOS workspace binding | Current repo path/git fingerprint | Refresh with `workspace.connect` after any path or remote change. |

## Recommended Sequence

1. Confirm the final repository owner and slug. Recommended slug:
   `flowmonkeyio/stackos`.
2. Rename or create the remote repository outside this checkout.
3. Update the git remote in this checkout after approval:

   ```bash
   git remote set-url origin git@github.com:flowmonkeyio/stackos.git
   ```

4. Keep the package, CLI, plugin slug, and MCP server name as `stackos`.
5. If the local folder is renamed, restart any Codex or Claude sessions that
   were launched from the old checkout so their per-session Serena stdio
   servers relaunch from the new cwd.
6. Re-register the StackOS MCP bridge if the agent runtime stores absolute
   paths for this checkout.
7. Refresh the StackOS workspace binding from the renamed repository so the
   bridge resolves the same project from the new path/remote fingerprint.
8. Run setup and release signoff after the rename:

   ```bash
   make install
   make signoff
   ```

## Non-Goals

- Do not rewrite historical commit messages.
- Do not delete the old remote until the new remote has all refs and the
  operator has confirmed downstream integrations.
- Do not introduce a project-scoped Serena daemon or launchd job from inside a
  normal coding delivery; this repo uses per-session stdio MCP pinned by cwd.
