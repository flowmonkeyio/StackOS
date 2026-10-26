# Complete relationship audit

The source is the fictional process in `source-process.md`, clauses R1–R6.
Node and edge IDs correspond to SVG element IDs and `diagram-mapping.json`.
This audit records the authored defects and repairs. It does not report what
an independent reviewer found; that result is recorded separately.

## Every node

| ID | Required meaning and actor | Source | Candidate | Corrected |
| --- | --- | --- | --- | --- |
| N1 | Author submits the saved draft and references. | R1 | Actor and action retained. | Retained. |
| N2 | Assistant checks reference presence, with complete/missing outcomes. | R2 | Node retained. Reference presence is not source verification. | Retained. |
| N3 | Author supplies missing references before another check. | R2 | Retained. | Retained. |
| N4 | Assistant prepares a packet identifying the exact revision and references. | R3 | Node retained. | Retained. |
| N5 | Human editor approves the revision or requests changes. | Roles; R4–R5 | Incorrectly assigns the decision to the assistant. | Actor restored to human editor. |
| N6 | Author revises after a change request. | R4 | Node retained, but its incoming decision branch is absent. | Node and branch retained. |
| N7 | Separate delivery worker queues only the editor-approved revision. | R5 | Node retained, but an extra incoming edge bypasses approval. | Node retained with only the approved route. |
| N8 | Queued confirmation is the endpoint; delivery is outside scope. | R6 | Retained. | Retained. |

## Every required or drawn relationship

| ID | Relationship required by the source | Source | Candidate | Correction |
| --- | --- | --- | --- | --- |
| E1 | N1 submission → N2 reference check | R1 | Reversed: N2 → N1. | Point from submission to the check. |
| E2 | N2 missing reference → N3 author supplies it | R2 | Present, labeled Missing. | Retain. |
| E3 | N3 supplied references → N2 repeated check | R2 | Present through the return lane. | Retain. |
| E4 | N2 all references present → N4 packet preparation | R2–R3 | Present, labeled All present. | Retain. |
| E5 | N4 packet → N5 editorial decision | R3–R4 | Edge present; destination actor is mislabeled. | Retain edge and repair N5. |
| E6 | N5 changes requested → N6 author revises | R4 | Missing. | Restore the branch labeled Changes. |
| E7 | N6 revised draft → N2 repeated source-reference check | R4 | Present. | Retain; do not skip preparation and editorial review. |
| E8 | N5 exact-revision approval → N7 worker queues it | R4–R5 | Edge present; the decision actor is wrong. | Retain edge and repair N5. |
| E9 | N7 queue operation → N8 Queued confirmation | R5–R6 | Present. | Retain; do not rename the endpoint Sent. |
| E10 | No direct N4 packet → N7 queue relationship is allowed. | R3–R5 | Unsupported direct edge is drawn. | Remove it. |

The corrected graph has eight nodes and nine directed relationships. Both
return paths go to N2; neither makes preparation or approval optional. The
diagram uses no grouping to assign authority or imply simultaneous execution.
It is deliberately bounded at Queued. Adding downstream sending or failure
branches would expand the source process rather than correct its representation.
