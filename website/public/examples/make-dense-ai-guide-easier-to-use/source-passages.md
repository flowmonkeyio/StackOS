# Dense source passages for a content-design exercise

These passages were written for this exercise. Cedar Drafts is an invented
documentation app used by an invented small software team. Its modes, interface,
rules and review request are fictional. No customer, deployment, measured result
or real product capability is described. The passages supply the complete
conditions for three editorial transformations; they are not quotations from
an actual product manual or outputs from a model-quality experiment.

## Source 1: Review modes

Workspace review lets signed-in workspace members read and comment on the live
draft, so saved edits appear in the version they see; the review view does not
allow them to edit the body, and access continues until their workspace access
is removed. Named guest review instead gives one invited email address access
to a frozen snapshot after that guest signs in with the matching address; the
guest can read and comment but cannot edit the body, the invitation expires
seven calendar days after creation, and later changes to the draft do not
appear in that snapshot. To show the guest a changed draft, the author must
create a new snapshot and invitation. Public preview gives anyone holding its
link read-only access to a frozen snapshot without signing in, permits no
comments or body edits, and expires 48 hours after creation; later draft changes
also require a new preview. The team permits workspace review for internal
material, permits named guest review only when the body has been cleared for
that particular outside reviewer, and permits public preview only when the
body has been cleared for public release. Approval to show a draft to one guest
does not meet the public-preview condition, even if the public link is shared
with only that guest. None of these modes publishes the draft to the team's
website.

## Source 2: Create a guest review invitation

Before creating an invitation, the editor needs the Author role in Cedar Drafts,
the saved revision named in the review request, the assigned guest's email
address, and clearance to share that revision's body with that guest; if the
clearance or role is missing, the editor must leave the draft private and ask
the document owner to resolve the missing prerequisite. In the draft's
Revisions panel, the editor opens the requested saved revision, chooses Guest
review, enters the assigned email address and selects Preview. The preview
shows the snapshot's body, its revision number and the invited email address.
The editor compares the revision and recipient with the review request before
creating anything. If either differs, the editor closes the preview, corrects
the selected revision or address, and previews again; editing text inside the
preview is not possible and does not substitute for selecting the right saved
revision. Once both match, Create invitation returns a review link and its
expiry time, seven calendar days after creation. Creating the invitation does
not send an email. The editor copies the link and expiry into the review request
for the document owner to send through the team's existing review channel.
This procedure is complete when the request contains the matching revision,
recipient, link and expiry. It does not establish that the guest has received
the message, opened the snapshot or finished reviewing it.

## Source 3: Choose a mode for this review request

The team's selection rule is to use a review mode only when it satisfies every
required condition in the request: the reader can access it, the permitted
actions are sufficient, its update behavior fits the review, its expiry covers
the review period and the planned disclosure is allowed. An editor must not
silently turn a requirement into a preference to select the closest match.
If no mode fits, the editor returns the conflicting requirements to the
document owner before creating a link. For this case, the reviewer is outside
the company and has no workspace membership. The body describes an unreleased
feature. The owner has cleared that body for this named reviewer, but has not
cleared it for public release. The reviewer needs to add comments during a
four-calendar-day window starting when the invitation is created. During those
four days the author will keep editing the draft, and the request explicitly
requires the reviewer to see the latest saved edits through the same review
link without receiving replacement invitations. The editor may choose among
the three modes in Source 1 but cannot add a workspace member, change the
disclosure clearance or replace the live-update requirement with a single
frozen review copy. No review mode has been chosen and no link has been created
in the supplied case.
