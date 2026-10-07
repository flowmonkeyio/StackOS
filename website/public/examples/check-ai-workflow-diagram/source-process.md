# Source process: prepare one announcement for sending

This is a fictional process written for a diagram-review exercise. The team,
roles and behavior are invented. It does not describe a customer incident,
an actual AI-generated failure or a shipped product feature.

## Boundary and roles

The process begins when an author submits a saved announcement draft with its
source references. It ends when a delivery worker confirms that the approved
revision is in the send queue. Sending, delivery, opening and recipient response
are outside this process.

The author writes and revises the announcement. An AI assistant checks whether
each factual claim has a source reference and prepares the review packet. That
presence check does not establish that a source supports a claim. A human editor
reviews the draft and sources and decides whether to approve or request changes.
The delivery worker is a separate software service that queues the approved
revision; it does not decide whether the prose is acceptable.

## Complete sequence

**R1 — Submit.** The author submits the saved draft and its source references to
the assistant. Submission starts the source-reference check; it does not begin
editorial review or add anything to the send queue.

**R2 — Check references.** The assistant checks whether every factual claim has
a source reference. If any reference is missing, it identifies the gap and
returns the draft to the author. The author supplies the missing references,
then the assistant repeats this same check. The process does not advance to
packet preparation while a reference is missing.

**R3 — Prepare.** When all factual claims have references, the assistant puts
the saved draft and those references into a review packet and gives the packet
to the human editor. The packet identifies the exact draft revision.

**R4 — Decide.** The human editor reads that revision and its sources, then
either approves it or requests changes. A change request goes to the author,
who revises the draft and submits the new revision back to the assistant's
source-reference check. That revision must pass the check and reach the editor
in a new review packet. It cannot go directly from revision to the send queue.

**R5 — Queue.** An approval names the exact revision the editor accepted. Only
after that approval does the delivery worker add that revision to the send
queue. The assistant does not provide editorial approval, and the worker cannot
substitute a newer, unreviewed revision.

**R6 — Stop.** The worker returns a Queued confirmation for that revision. This
is the endpoint represented by the diagram. Queued means the revision is in
the send queue; it does not establish that the announcement has been sent or
delivered. Queue failures and later delivery behavior are outside the scope
of this exercise.
