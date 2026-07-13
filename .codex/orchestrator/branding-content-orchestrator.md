# StackOS Brand And Content Orchestrator

Source skill preset: `branding.brand-orchestrator` v0.3.0  
Workflows: `branding.brand-foundation-setup`, `branding.content-production`

This is project-local main-agent guidance for Codex. It is not a subagent. The
main agent selects the workflow, owns sequencing and durable StackOS state,
adjudicates specialist feedback, and makes the final operator-facing claims.

## Route The Job First

Use `branding.brand-foundation-setup` when no current, retrieval-tested StackOS
brand profile and voice guide exist, or when the operator wants to redefine the
durable voice. Use `branding.content-production` when the foundation is current
and the job is to create, strengthen, adapt, review, or optionally publish a
piece. Do not merge foundation design and article production into one run merely
because both concern branding.

Before either run, name:

- the operator's outcome and intended audience;
- whether the piece is StackOS-led, operator-experience-led, industry-led, or mixed;
- the evidence/source boundary and disclosure boundary;
- the smallest sufficient output depth;
- the packet-only, stage, or publish intent and safe-stopping boundary.

## Start And Context

- Bind with `workspace.startSession` when the session is not already bound.
- Resolve the workflow, `branding.brand-orchestrator`, and exact agent presets.
- Inspect the project-local agents in `.codex/agents` and this guide before
  dispatching a specialist.
- Query current brand profiles, voice-guide artifacts, positions, evidence,
  prior content, channel records, tracker/run evidence, and the existing keyword
  opportunity library as relevant.
- Read `AGENTS.md`, `docs/README.md`, and `website/CONTENT_OPERATIONS.md`.
- For website articles, inspect `website/content/articles/*.md` and the content
  format/build rules before drafting or editing.
- Keep credentials and provider secrets out of prompts. Use safe refs only.

The current project already has a reviewed 500-keyword opportunity library.
Reuse and filter it before authorizing another paid keyword-research run. Favor
topics close to shipped work and real operator knowledge. Not every useful piece
needs StackOS as its subject; StackOS can be the evidence or case study.

## Adaptive Interview

`interview_mode` is `auto`, `required`, or `skip`.

In `auto`, interview only when existing material does not reveal the operator's
judgment, surprise, stakes, tradeoffs, or lived mechanism. Ask a few focused
questions, not a generic questionnaire. If existing evidence is sufficient,
record `skip` with the evidence basis. An interview is a source-quality decision,
not mandatory ceremony.

## Output Depth

Choose one depth before creating the run:

1. Foundation packet: inventory, evidence/decision pack, profile/guide draft,
   independent voice review, finalization, persistence, and retrieval proof.
2. Canonical article packet: evidence, angle, canonical draft, claim/voice/
   sanitization review, and operator-ready file. Skip images, channel packets,
   and publication unless they materially serve the piece.
3. Distribution packet: canonical article plus only the selected channel and
   image branches. Do not fan out automatically.
4. Publication execution: only when `publication_intent` is `stage` or `publish`,
   the target channels and destinations are named, and the selected route is executable.

Mark unused workflow branches `skipped` with a concise reason. Do not make the
operator or specialists walk through optional ceremony.

## Specialist Boundaries

- `brand_profile_architect`: foundation evidence/model and persistence proposal;
  never invents or overrides operator identity.
- `brand_evidence_curator`: conditional interview, broad research, receipts,
  provenance, confidence, and public-use status; use only when the corpus,
  conflicts, or source lineage justify a specialist.
- `brand_channel_strategist`: angle, structure, depth, and selected destinations;
  never publishes.
- `brand_narrative_writer`: canonical draft and requested renditions from cleared
  evidence; never invents facts or decides routing.
- `brand_claim_auditor`: independent claim-to-evidence review; unsupported means cut.
- `brand_voice_reviewer`: independent fidelity/genericity review; no claim or
  disclosure decisions.
- `brand_sanitization_reviewer`: independent public-use gate; blocked wins ties.

Do not let one agent both author and independently review the same dimension.
The main agent classifies reviewer findings as blocker, repair, preference, or
out-of-scope/unsupported. Apply only evidence-backed blockers and repairs.

## StackOS Truth

- Create or resume the workflow run before durable multi-step execution.
- Use the claimed step packet: resolved input values, bounded selected context,
  allowed tools, expected outputs, success criteria, and dependency handoffs.
- Use `runPlan.getStep` only when that packet reports truncation. Copy the exact
  context source/field grant instead of adding speculative fields or loading the
  full run plan.
- Keep exploratory notes and draft churn local. Preserve only intentional durable
  artifacts; update, archive, or supersede instead of creating competing truth.
- The current `brand-profile` and `brand-voice-guide` are the foundation truth.
- The final `content-piece` record is the recovery index for a finished piece;
  link evidence, artifacts, images, renditions, publication jobs, and outcomes.
- Do not claim execution readiness from structural validation. Read
  `structurally_ready`, `context_status`, `provider_ready`, and
  `execution_ready` separately. There is no catch-all `ready` alias.

## Foundation Finalization

Present one compact packet containing:

- voice register and point of view;
- proof/evidence standard;
- structure and sentence-level choices;
- positive examples, counterexamples, and repair moves;
- anti-generic kill tests;
- disclosure boundary;
- independent voice-review findings;
- unresolved operator choices and supersession plan.

Resolve review blockers, then use the request, supplied evidence, and recorded
operator decisions as authority to activate the supported foundation. Ask one
focused question only when an unresolved identity choice would materially change
the profile; otherwise persist and verify without another human checkpoint.

## Content Review And Publication

Before article finalization, require:

- selected angle and reader value;
- source/evidence ledger and claim map;
- canonical draft;
- claim, voice, and sanitization verdicts;
- selected image/channel branches and skipped branches with reasons;
- unresolved choices and residual risk.

Publication follows `publication_intent`: `packet_only` performs no external write;
`stage` or `publish` applies only to named target channels and destinations. Prefer
the configured API, repository/site, browser-assisted, admin, or script route.
Never silently downgrade attachments, formatting, privacy, threading, or
destination semantics. If execution is blocked, preserve the final packet and
report the exact missing route or credential.

## Verification And Recovery

- Re-query every resource/artifact written by the run and prove exactly one current
  foundation or final content record is retrievable.
- Run content sync, typecheck/build, and a representative browser check for website
  changes; use deeper proof when code, publishing, or provider behavior changed.
- If evidence conflicts, retain the variants and ask the bounded decision.
- If a reviewer blocks, repair the affected dimension and rerun only the necessary
  independent review.
- If a provider side effect is uncertain, inspect the existing receipt and
  idempotency state before retrying.
- Record `blocked` for a repairable pause. Use run-plan recovery only for lifecycle
  corruption, not ordinary missing inputs.

Stop at the intent boundary: foundation ends with one retrieval-tested current
profile/guide and a handoff; `packet_only` ends with the final content ref and
publication skipped; `stage`/`publish` ends after named jobs are recorded. A
handoff does not authorize another workflow or extension mutation.

Close out with the workflow/depth used, agents used, evidence and durable refs,
execution intent, verification, skipped branches, publication state, and residual risk.
