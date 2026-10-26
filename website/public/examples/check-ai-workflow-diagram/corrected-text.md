# Corrected diagram: complete text explanation

The author submits a saved announcement draft and its source references. The
assistant checks whether every factual claim has a reference. When a reference
is missing, the author supplies it and the assistant repeats the check. A
reference being present does not establish that it supports the claim.

Once all references are present, the assistant prepares a packet containing the
exact draft revision and those references. A human editor reviews that packet
and either approves the revision or requests changes.

A request for changes goes to the author. The revised draft returns to the
assistant's source-reference check, passes through packet preparation again,
and receives another editorial decision. Revision does not lead directly to
queuing.

Only approval of the exact revision permits the separate delivery worker to
put it in the send queue. The worker then returns a Queued confirmation for
that revision. The process ends there: it does not establish sending, delivery
or recipient response. Queue failures and later delivery are outside this
diagram's stated scope.

The two return lines represent missing-reference repair and requested draft
revision. Both return to the same reference check. The layout does not define
parallel execution or give the assistant editorial approval authority.

These are the relationships of the fictional process in `source-process.md`.
