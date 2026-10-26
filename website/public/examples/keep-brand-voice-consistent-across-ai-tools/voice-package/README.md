# One voice guide, two tool adapters

This is a fictional exercise for an article about keeping editorial guidance
consistent across AI tools. Draft Pier, its behavior and its editorial choices
are invented. Nothing in the package describes a customer or establishes a real
product capability.

## Files

- `voice-guide.md`: the original guide, revision 1.0.
- `voice-guide-v2.md`: the replacement guide, revision 2.0.
- `facts.md`: fictional product facts, unchanged throughout the exercise.
- `AGENTS.md`: Codex instructions that explicitly request the source files.
- `CLAUDE.md`: Claude Code instructions that import those same files and request
  an explicit read for the exercise's evidence record.
- `prompt.txt`: the same writing task for each tool.

For the baseline, make an isolated working folder for each tool. Copy only
`AGENTS.md`, `CLAUDE.md`, `voice-guide.md`, `facts.md` and `prompt.txt` into it.
Keep `voice-guide-v2.md` outside those folders until the revision exercise.
The original Codex trial used these five files.

Start each tool in its baseline folder and give it `prompt.txt`.
Both adapters select `voice-guide.md`. Inspect the file reads and
returned source note before judging the prose. A model's claim to have used
revision 1.0 is less useful than the actual source read alongside that claim.

Codex discovers its instruction file when a run starts. Its adapter then asks
the model to read the shared guide. Claude Code's `@` syntax imports a file;
keep those lines outside code fences in the real `CLAUDE.md`. These are different
loading mechanisms serving the same source choice.

## Exercise the revision

1. Save the original `voice-guide.md` as `voice-guide-copy.md`.
2. Replace `voice-guide.md` with the contents of the saved `voice-guide-v2.md`
   from outside the working folder. Keep the filename `voice-guide.md`.
3. Deliberately make one adapter name `voice-guide-copy.md` instead of
   `voice-guide.md`. This models the common mistake of retaining a pasted copy.
4. Start a fresh session and run the same task. Compare the observed source path
   and revision with the current owner, not just whether the paragraph sounds
   polished. The stale copy still calls itself approved within the old scenario.
5. Repair that adapter to name `voice-guide.md`. Start another fresh session,
   repeat the task, and inspect the actual source read and affected opening.

Revision 1.0 asks for one brief harbor or navigation metaphor. Revision 2.0 asks
for a literal opening that names the reminder or reviewer. Neither instruction
changes the reminder schedule. Review facts and expression separately.

The obviously easy choice is to paste the guide into every tool once. That
creates extra copies to reconcile. Starting a new session does not itself
correct that reference.

## What the Codex runs show

The recorded local exercise uses Codex CLI0.160.1. No
model or reasoning override was supplied. Tool execution was restricted to
reading; MCP connections and web tools were excluded. This is a worked example,
not a benchmark or a promise of identical output.

Codex returned an announcement in the initial run. In the stale-reference run,
it also found revision 2.0, reported the conflict and wrote against the newer
guide. The stale adapter did not cause stale prose in that observed attempt.
After we repaired the adapter, a fresh run read revision 2.0 directly and
returned a literal opening. The source defect was fixed even though the model
had already worked around it. See the exact outputs in the accompanying files.

## Primary documentation

- [OpenAI: AGENTS.md instruction discovery](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
- [Anthropic: CLAUDE.md imports](https://code.claude.com/docs/en/memory#import-additional-files)
- [GitLab: instruction files and their source guide](https://docs.gitlab.com/development/documentation/ai-instruction-files-documentation/)

Documentation checked October 7, 2026. Newer documentation may describe features
absent from an older installed CLI. This example uses `CLAUDE.md` imports and
does not depend on newer Claude Code support for `AGENTS.md`.
