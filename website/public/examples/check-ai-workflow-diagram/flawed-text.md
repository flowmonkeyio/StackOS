# Candidate diagram: visual transcription

This text describes what the candidate diagram draws. Compare it with the
separate written source to decide whether those relationships are correct.

Eight nodes are visible: Author submits saved draft; Assistant: references
complete?; Author adds missing references; Assistant prepares packet;
Assistant: approve?; Author revises draft; Worker queues approved revision;
and Queued confirmation.

The arrow between submission and reference checking points from the reference
check to author submission. The reference check's Missing branch leads to the
author adding references; that node returns to the reference check. Its All
present branch leads to packet preparation.

Packet preparation points to the Assistant: approve? decision. That decision's
Approved branch points to the worker queuing the revision. A separate line
also runs directly from packet preparation to the queue node. The Author
revises draft node points back to the reference check, but no arrow connects
the approval decision to that revision node.

The queue node points to Queued confirmation. The two paths returning to the
reference check share the lane on the left. There are no swimlane or grouping
boxes beyond the individual process and decision nodes.
