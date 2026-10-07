# What the returned paragraphs show

These are actual Codex outputs from a fictional exercise run on October 7, 2026.
They show the tool using and reconciling supplied writing guidance. They do not
measure model quality, consistency rates, rankings or reader response.

| Run | Adapter selects | Observed reads | Returned result |
| --- | --- | --- | --- |
| Codex, original guide | `voice-guide.md`, revision 1.0 | Adapter, guide 1.0 and facts | An announcement with a brief harbor metaphor |
| Codex, stale adapter | `voice-guide-copy.md`, revision 1.0 | Old copy, facts, adapter and current guide 2.0 | The model identified the conflict and used guide 2.0 |
| Codex, repaired adapter | `voice-guide.md`, revision 2.0 | Adapter, guide 2.0 and facts | An announcement with a literal action opening |

The original Codex paragraph began:

> Your article is waiting in harbor. While it’s pending your colleague’s review, you can enable review reminders for that draft.

That opening matches revision 1.0's requested metaphor followed by a practical
action. Under revision 2.0, its first sentence needs revision: the newer guide
asks for a literal opening naming the reminder or reviewer. The facts have not
changed; the editorial preference has.

The stale-reference run began:

> Turn on review reminders for the colleague assigned to your article while it waits for review.

The model had read the older guide, then found the newer file. It explicitly
reported that the adapter named revision 1.0 while revision 2.0 replaced it.
It then used the newer guide. We did not need to repair stale wording in this
output, and we did not rerun the experiment until it failed.

The repaired-adapter run began:

> Enable review reminders for your article while it waits for your colleague’s review.

This is a different sentence with the same applicable editorial choices. The
trace shows the current guide was read directly. A revision number in the
answer would have been insufficient on its own; the retained read output
contains the actual guide text.

All three Codex announcements preserve the supplied weekday schedule, workspace
time zone, review decision and publishing limit. None establishes that reminders
make reviews faster. The examples in the voice guide supply expression choices;
the separate facts file supplies the reminder behavior.

## What changed in the setup?

The stale adapter asked the model to read `voice-guide-copy.md`. We changed that
reference back to `voice-guide.md`, then started a fresh run. Both runs produced
prose consistent with revision 2.0, but only the repaired adapter named the
intended current source.

It would be wrong to describe this as proof that stale references always cause
stale prose. The observed model found and reconciled the conflict. The useful
lesson is that an editor can inspect which source was selected, see whether
the model resolved a conflict, and fix the source choice instead of leaving
future runs to rediscover the problem.
