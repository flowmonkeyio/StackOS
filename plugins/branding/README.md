# Branding Plugin

`branding` is the generic Level 1 authority-content plugin. It provides schemas,
one workflow template, agent presets, and the main-agent orchestration preset for
evidence-grounded content production and automated channel publication.

The only workflow is `branding.content-production`: an end-to-end
journalist-style loop that interviews the operator, researches the facts across
the right sources, pulls supporting artifacts, proposes angles, drafts the
canonical piece, optionally generates website/channel imagery when the selected
structure needs it, renders channel publication jobs, executes configured
publication modes, and stores the resulting memory for future consistency
checks.

The plugin owns only generic contracts:

- evidence-items, streams, channel charters, routing policy, and content-pieces
- the one content-production workflow shape
- role contracts for interview, research, writing, channel publication shaping, and review

Durable memory lives in StackOS resources. `brand-profile` stores voice and
editorial rules, `position` stores standing claims and stance changes,
`evidence-item` stores facts and receipts, `channel` stores channel form, and
`content-piece` is the approved output index. Artifacts can hold drafts, packets,
and generated images, but the approved `content-piece` must store the refs,
memory summary, topic tags, position refs, image refs, and follow-up hooks that
future runs need.

Publication output lives in `content-piece.publication_jobs` and matching
`distribution-record` resources. Each selected channel should have an approved
job with the publication mode, publication bundle ref, exact copy artifact,
image artifact refs, destination hint, execution target, result refs, and any
blocker. Preferred modes are API integration, browser-assisted platform UI,
site/admin UI, or project script. Fallback handoff is only for blocked or
explicitly waived automation.

Level 2 project overlays own all operator-specific values:

- sources of record
- voice profile and kill tests
- disclosure policy
- concrete channel instances, handles, charters, cadence, and publication modes
- canonical site stack and provider credentials
- standing positions and stream instances

Agents should start from `branding.brand-orchestrator`, then adapt the required
specialist preset for each workflow step. For solo-operator branding work, run
`branding.content-production`; do not chain separate branding workflows to finish
one piece. StackOS resources remain the single source of truth; local files may
mirror artifacts but should not replace evidence, content-piece, routing,
approval, publication jobs, distribution records, or channel memory.
