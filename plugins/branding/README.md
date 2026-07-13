# Branding Plugin

`branding` is the generic Level 1 authority-content plugin. It provides schemas,
two workflow templates, agent presets, and the main-agent orchestration preset for
evidence-grounded content production and automated channel publication.

`branding.brand-foundation-setup` builds or refreshes the active brand profile
and durable voice guide from representative samples, operator judgment,
source-linked research, independent voice review, finalization, and a final
retrieval check. It separates channel-independent voice from image style,
channel mechanics, and temporary campaign tone.

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
