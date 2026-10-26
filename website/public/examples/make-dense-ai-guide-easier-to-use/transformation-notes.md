# Notes for restructuring the Cedar Drafts passages

Cedar Drafts and its review request are fictional. The complete passages are in
[source-passages.md](source-passages.md). The article uses selected excerpts and
a condensed case to show three editing decisions.

## Comparison: align the attributes being compared

The table compares named guest review with public preview. Its three columns
are the attribute and the two modes; its three rows cover access, review actions
and clearance. It is a comparison of selected attributes, not a complete guide
to choosing a mode.

Keep these distinctions in their cells:

- Named guest review requires the invited email and matching sign-in, permits
  reading and comments, and needs clearance for that particular outside reviewer.
- Public preview permits anyone holding the link to read without signing in,
  permits no comments, and needs public-release clearance.

“Approved sharing” would hide the difference between those permissions. Sending
a public link to one person does not restrict who can open it.

## Procedure: put the correction before creation

The five-step procedure keeps prerequisites outside the numbered actions:
Author role, the requested saved revision, the assigned guest email and clearance
for that body and guest. Missing role or clearance means leave the draft private
and ask the owner to resolve it.

The steps group related controls without losing their order: open the requested
revision and enter the guest email; preview; compare revision and recipient;
create only when both match; record the returned link and expiry.

Keep the mismatch branch inside the comparison step. Close the preview, correct
the selection or address, and preview again. The preview body cannot be edited.
Creation returns a link and an expiry seven calendar days after creation.

The completed request contains the matching revision, recipient, link and expiry.
Creating an invitation sends no email; the owner sends through the existing
review channel.

## Decision: show the requirement that fails

The condensed case examines named guest review only. The request needs comments
and the latest saved edits through the same link, without replacement invitations.
Guest review permits comments but shows a frozen snapshot; later edits require
a new snapshot and invitation.

That update behavior fails the stated requirement. Return the conflict to the
owner before creating a guest link. The example does not establish the suitability
of the other modes; the complete source contains the broader selection case.

## Check the page

Keep table values identifiable under their headers and make the last column
reachable if the table scrolls. Surrounding prose should still fit the narrow
viewport. Keep the procedure's correction before creation and the decision's
live-update requirement beside its snapshot explanation.

Follow the procedure with a mismatched recipient: it should lead back to a
corrected preview before creation.

## Primary guidance

- [Google: tables](https://developers.google.com/style/tables)
- [Google: procedures](https://developers.google.com/style/procedures)
- [W3C: table structure](https://www.w3.org/WAI/tutorials/tables/)
- [W3C: reflow](https://www.w3.org/WAI/WCAG22/Understanding/reflow.html)
