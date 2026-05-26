# Repository Rename Plan

Status: planning only. Do not move the local directory, change git remotes,
rename launchd labels, or rewrite MCP/Serena registrations without explicit
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
| Project Serena server | `serena-content-stack` | Rename only with a coordinated Serena/launchd migration. |
| Serena launchd label | `com.oraios.serena-mcp.content-stack` | Rename only after the local repo path is approved. |
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
5. If the local folder is renamed, stop any repo-scoped Serena launchd job,
   update the Serena project path/label/log name, then restart it.
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
- Do not rename the project-scoped Serena MCP from inside a normal coding
  delivery; that is local machine setup and should be a deliberate maintenance
  step.
