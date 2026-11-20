# Branding Plugin

This domain package follows the canonical
[`StackOS Product Contract`](../../docs/product-direction.md#product-contract).
The workflow organizes editorial method and agent guidance; it does not move
content strategy into core or require code gates for every editorial judgment.

`branding` is the generic Level 1 authority-content plugin. It provides schemas,
two workflow templates, agent presets, and the main-agent orchestration preset for
evidence-grounded content production and automated channel publication.

`branding.brand-foundation-setup` builds or refreshes the active brand profile
and durable voice guide from representative samples, operator judgment,
source-linked research, independent voice review, finalization, and a final
retrieval check. It separates channel-independent voice from image style,
channel mechanics, and temporary campaign tone.

Other workflows consume the selected approved project foundation through
existing `selected_context_json` owner refs and current scoped reads. That source
may be this profile/guide or an approved external, document or legacy equivalent;
do not require foundation setup merely because this plugin does not own it.
Foundation closeout returns exact owner revisions, the decision and retrieval
evidence without silently changing another workflow's source selection.

`branding.content-production.source_scope.refs` receives the exact selected SEO
opportunity and its reviewed handoff, including disposition, current existing-page
coverage, evidence index, intended scope and next owner. It also accepts a
canonical-content synchronization handoff after an applied SEO change. In that
case, reuse the current exact candidate and review/implementation receipts;
update only the selected content owner within existing authority and grants.
Otherwise keep synchronization pending. Do not redraft, republish or replay an
applied edit to repair bookkeeping. Default to `packet_only` unless another mode
is already authorized.

Authors and independent reviewers consume the same selected foundation refs and
actual revisions. Review artifacts bind both that foundation and the exact
candidate; the content-piece review log retains their refs. Recheck both before
canonical updates or publication. Changed applicable guidance requires affected
review even if the prose did not change; retain old receipts as history.

The shared [natural writing method](references/natural-writing.md) guides source
handling, drafting, and review. Preserve useful original wording and context
through handoffs, distinguish endorsement from opinion or feedback, and read the
whole piece before sentence polish. Check both experience claims and the
usefulness of the explanation or proposed solution. Wording patterns are
contextual signals for judgment, not bans or detector targets. Project voice,
identity, examples, and approved positions stay with the selected Level 2
foundation.

The selected angle distinguishes original operator requirements and factual or
disclosure constraints from agent-proposed presentation. Structure, example scope,
and inline detail remain revisable against the reader task. A smaller example
must retain the premises for its remaining claims. Inspect one costly repeated
example, table, or diagram in the existing destination before expanding that form
within draft checks; ordinary prose needs no extra render stage.

Voice review starts with the original request, current approved foundation and
endorsed examples, exact candidate, and necessary factual/disclosure constraints.
The reviewer forms its own reader-task judgment before reading author rationale
or prior verdicts, then reconciles the selected angle in the same review. Main
acceptance integrates reading flow, necessary detail, review findings and relevant
render evidence. Repair the earliest wrong decision while preserving sound work.
Sequential handoffs carry actual rejection and repair decisions; publication alone
does not endorse a writing exemplar. These are responsibilities within existing
steps, not additional roles, stages, approvals, or word limits.

The reference ships inside the plugin. When expanded guidance is needed and the
host provides authorized file access, resolve
`branding-plugin:references/natural-writing.md` from the effective preset or
template's `origin_path` using `../references/natural-writing.md`. MCP access
does not grant filesystem access. Consumers without it use the essential method
kept inline and relevant excerpts supplied through authorized context. Report
any material source gap without claiming to have read an inaccessible reference;
the main agent carries available, relevant guidance into bounded handoffs.

`branding.content-production` is the end-to-end content loop. It decides whether
an operator interview is required, useful, or unnecessary, researches the
facts across the right sources, pulls supporting artifacts, proposes angles,
drafts the canonical piece, optionally generates website/channel imagery,
renders channel publication jobs, executes the explicitly selected publication mode, and
stores the resulting memory for future consistency checks. It can stop with a
review-ready packet when `publication_intent` is `packet_only`.

The plugin owns only generic contracts:

- evidence-items, streams, channel charters, routing policy, and content-pieces
- brand-foundation setup and content-production workflow shapes
- role contracts for profile architecture, evidence, writing, channel publication shaping, and review

Durable memory lives in StackOS resources. `brand-profile` stores voice and
editorial rules, `position` stores standing claims and stance changes,
`evidence-item` stores facts and receipts, `channel` stores channel form, and
`content-piece` is the final output index. Artifacts hold intentional durable
records such as final drafts, publication packets, durable evidence,
reviewed draft records, and generated or selected media. Normal
interview notes, research exploration, angle options, and draft iteration belong
to project-local working conventions until the workflow explicitly preserves
them. The final `content-piece` must store the refs, memory summary, topic
tags, position refs, image refs, and follow-up hooks that future runs need.

Publication output lives in `content-piece.publication_jobs` and matching
`distribution-record` resources. Each selected channel should have an intent-scoped
job with the publication mode, publication bundle ref, exact copy artifact,
image artifact refs, destination hint, execution target, result refs, and any
blocker. Preferred modes are API integration, browser-assisted platform UI,
site/admin UI, or project script. Operator-confirmed manual publication is valid
when the operator explicitly confirms they posted the final copy/media.
Fallback handoff is only for blocked or explicitly waived automation.

Level 2 project overlays own all operator-specific values:

- sources of record
- voice profile and kill tests
- disclosure policy
- concrete channel instances, handles, charters, cadence, and publication modes
- canonical site stack and provider credentials
- standing positions and stream instances

Agents should start from `branding.brand-orchestrator`, then invoke only the
specialists the step needs; evidence curation is conditional, while public
claim, voice, and sanitization reviews remain independent. Run brand-foundation setup when the
active voice profile or guide is missing or materially wrong; otherwise run
content production directly. Foundation and production are separate jobs, not
duplicate branding workflows, and a handoff does not start or mutate the next
workflow. StackOS resources remain the single source of truth; local files may
hold scratch iteration or mirror durable artifacts, but they should not replace
evidence, content-piece, routing, execution intent, publication jobs, distribution
records, or channel memory.

For related pieces, the main agent creates one parent tracker task with ordered
article tickets and runs one complete `branding.content-production` invocation
at a time. Each later invocation receives current canonical work and all
completed earlier article refs; its own angle, draft, and editorial-review steps
own relative voice and form. The parent task tracks sequence and outcomes, not a
separate portfolio brief or batch-wide editorial gate. The keyword library is
evidence, not an evergreen backlog, and a material operator correction should
reopen the earliest affected workflow decision rather than trigger repeated
surface edits.
