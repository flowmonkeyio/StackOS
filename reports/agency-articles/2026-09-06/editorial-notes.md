# Agency article — internal editorial packet

Current state: published after the operator's later deployment approval.
The sole editable canonical article is now
`website/content/articles/managing-a-solo-agency-with-ai-agents.md`.
The sibling report is retained only as the historical approved-prose snapshot.
Content-piece 996 and artifact 2142 point to the website source and publication
receipt 8987. The packet-only instructions and reviews below document the
earlier editorial phase; they are not the current publication status.

Not public article copy. Run: `branding.content-production`, plan 376 / audit 371.
Intent: `packet_only`; one article, sequential steps and reviews. Do not publish,
integrate into the website, change agent definitions, or touch the engineering diff.

## Reader and request

Solo agency owner managing several clients, sometimes several projects for one
client. Explain how an AI-agent arrangement could work in ordinary business
terms. The owner wants useful continuation, not a collection of chat helpers.
Agency context and project context should stay small. Finance serves the business
across its projects and can also exist without an agency.

This is a proposed operating design. We have no production agency results,
measured savings, customer story, or autonomous reliability evidence to claim.
Avoid a department-per-agent org chart, technical implementation manual, vague
finance tips, or a StackOS pitch. No product name or product links are needed.

## Current voice and comparison set

Authoritative foundation: `resource-record:247`, approved `artifact:450` v1.2.0,
retrieved at the start of this run. Local role contracts are under
`.codex/agents/brand-*.toml`; main guidance is
`.codex/orchestrator/branding-content-orchestrator.md`.

Voice: calm, specific, practical, naturally varied. Start with an operating
tension or useful distinction. Name the actor, actual decision and consequence.
Use first person only for attributable operator choices; do not invent lived
experience. Prefer plain language. Let the material choose the shape and ending.
Show enough detail that the reader could try the arrangement. Avoid repeated
not-X-but-Y constructions, symmetric departments, generic benefit adjectives,
stock caveat paragraphs, padded lists, or a recap ending. Review clusters in the
actual prose; do not use a blacklist or fake roughness.

Read all three recent approved exemplars:

- `website/content/articles/building-ai-finance-department-one-person-business.md`
  (`resource-record:987`): already owns the proposed finance coordinator and
  partial-payment continuation example. Do not recreate its opening or roster.
- `website/content/articles/when-is-a-receipt-actually-processed.md`
  (`resource-record:988`): already owns the two-attachment receipt walkthrough.
- `website/content/articles/customer-paid-invoice-still-open.md`
  (`resource-record:990`): already owns the payment/reminder context decision.

The older handoff article provides background, not the desired technical altitude:
`website/content/articles/what-ai-agent-handoff-should-include.md`.

The new article should own agency-level organization: company context, separate
engagements, coordination across commitments, and shared finance. Use a different
operating situation and prose shape from the three finance pieces.

## Cleared evidence and limits

Durable source pack: `urn:stackos:project:1:resource:995`.

E1 — Operator choices in this conversation: multiple clients/projects; agency-root
finance; independent finance without agency; minimal setup; one external financial
source of truth. Generalize these choices. They support a proposed arrangement,
not a universal rule or a claim that the agency has already run this way.

E2 — Sanitized design support: `plugins/agency/README.md` and
`plugins/finance/references/backend-contract.md`. A client can have several
engagements. Descriptive context does not grant data access. Shared company
finance may attribute source-backed records to a project without creating another
financial master. Unknown/shared attribution stays unknown, not invented.
The source implementation is not evidence of real-world agency performance.

E3 — [Anthropic, Building effective agents](https://www.anthropic.com/engineering/building-effective-agents),
read September 7 UTC / September 6 local, 2026. Its orchestrator-workers pattern
uses a central model to delegate and synthesize; its agent section describes
environmental feedback and returning for human judgment. Use only these general
principles, not a framework/product recommendation. Attribute any such statement
with an adjacent link. At most 200 derived words from this source, no quotation
needed. It does not prove the proposed agency arrangement works.

E4 — [Microsoft, AI agent orchestration patterns](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns),
read the same day. Recommends the lowest complexity that meets requirements and
notes coordination overhead and failure modes from multiple agents. At most 200
derived words, no quotation needed. No optimal role count or agency business
outcome is established.

Keyword library: `artifact:326`,
`reports/seo/stackos-keyword-opportunities-2026-07.json`. Filtered for agency,
client, business operations and orchestration. Adjacent opportunity:
`ai agent orchestration for small business`. No exact agency/client keyword match;
no volume or ranking claim. Current site inventory and relevant content records
show no article already owning agency-level organization. No new paid research.

## Public-use boundary

The approved guide permits sanitized workflow/design lessons and generalized
operator judgments. Exclude credentials, private customer/account identifiers,
commercial terms from actual customers, private metrics, vulnerabilities, local
paths, run IDs, evidence IDs and this internal packet. Invented example details
must be clearly illustrative, not an anonymized incident. No tax/legal advice.

Do not imply that labels/folders enforce access isolation, that a saved prompt
creates working connections, that an invoice is permitted just because delivery
is complete, or that unknown finance/project attribution can be filled by guess.
The article should explain one company's arrangement, not pooling distinct legal
businesses or client-owned books.

## Output and review requirements

Canonical prose in a sibling Markdown file, not `website/content/articles/` yet.
No frontmatter publication dates needed at review stage. Links to public sources
may appear naturally; no internal claim map in article prose. Separate claim map
and concise comparative self-audit belong here or in the specialist handoff.

Required independent passes, sequentially: claim, voice, then disclosure.
Voice must name the closest exemplar and compare opening, contrast/caveat
language, cadence, transitions, section logic and ending. Main agent adjudicates
actual evidence, repairs only necessary issues, and persists one final
content-piece recovery record. No optional images or channel fan-out.

## Selected angle — orchestrator decision

Selected the strategist's new agency operating-map angle. Working title:
“Managing a solo agency with AI agents.” The agency-wide arrangement owns this
piece; shared finance is one part, not the organizing story. The other ideas—a
weekly review and shared finance alone—were held because they introduce an
unsupported cadence or repeat the finance batch.

Use a spatial structure: the ambiguity of one client with two engagements;
small agency-wide context; distinct active engagements; coordination across
commitments; company finance; a concrete placement diagnostic to finish.
Target about 850–1,000 words, without padding to a count. Specialist roles should
be described only where the work calls for them, not as a department roster.
Include what agents actually read, do, and return, in business language. The
arrangement needs configured access and a way to start work; prompts alone do
not run a business. Keep that explanation brief and proportionate.

Differentiate the actual prose from the finance batch: no first-person desire
opening, partial-payment story, quoted owner reply, stepwise artifact-state
walkthrough, or fresh-session handoff ending. Semantic constraints must remain
local to relevant claims, not become repetitive disclaimers. Clearly hypothetical
examples are welcome; do not imply a witnessed client incident.

Public draft path:
`reports/agency-articles/2026-09-06/managing-a-solo-agency-with-ai-agents.md`.
No image, extra channel rendition, website integration, or publication selected.

## Draft handoff and main integration

Writer created the canonical draft. Main kept the selected spatial argument but
replaced abstract “four locations,” “agency root,” “returns,” and “next states”
with business briefs, project records, reports, and an illustrative scheduling
decision. Removed the ambiguous sentence claiming the owner's decision was
visible before one had been made. These are drafting repairs, not new evidence
or a change to the durable voice. Claim and independent voice reviews follow.

Claim map for review:

- C1: Multiple engagements per client, small agency brief, separate working
  records, company finance — E1/E2; proposed organization, no reported outcome.
- C2: Read/do/report responsibilities, configured access and start path — E2
  and the proposed arrangement; no product capability or self-activating prompt.
- C3: Project scope, unresolved approvals, cross-commitment comparison and
  owner-priority decision — explicit hypothetical proposal, E1/E2 support the
  organization rather than proving performance or supplying real client facts.
- C4: Central delegation/synthesis and human-judgment return — E3, narrowly
  attributed. Additional multi-agent overhead — E4, narrowly attributed.
- C5: One company financial source, source-backed project association, visible
  unknown/shared attribution, separate businesses and client books excluded — E1/E2.
- C6: Placement diagnostic at the end — proposed reader test, not test results.

No claimed production incident, measured savings, autonomous reliability, or
standing-position update. Public draft is not operator-approved or published.

## Independent claim review

Reviewer: `/root/agency_article_claim`, `branding.claim-auditor` v0.1.0.
Verdict: pass; no blockers or repairs. Main accepts the evidence-backed result.

All C1–C6 material groups passed. The reviewer checked agency and finance source
contracts and independently opened both primary citations. Delegation/synthesis
and returning for human judgment are supported by Anthropic; coordination
overhead/failure modes and lowest-sufficient complexity are supported by Microsoft.
Each source contributes substantially fewer than 200 derived words. The proposed
organization and explicitly hypothetical case do not assert deployment results.
No contradiction with the three finance exemplars, duplicate financial master,
guessed allocation, or independently authorized client action was found.

Exact audited boundaries include “Writing those instructions does not itself
connect the accounts or start a recurring process,” “Consider a hypothetical
client with two engagements,” and “Any client message or revised commitment still
follows the owner's rules for that action.” No claim repair is pending.

## Independent voice review

Reviewer: `/root/agency_article_voice`, `branding.voice-reviewer` v0.2.3.
Verdict: ready; no blocker, repair, or preference. Main accepts the comparative
evidence after reading the full current draft.

Closest prior piece is 987, the finance-department article. That article opens
with “I wanted” and develops a finance coordinator and specialist roster; this
one starts with the website/campaign ambiguity and places work by business scope.
The receipt article 988 follows two attachment states; payment article 990 moves
from matching money into a contact decision. Neither is the new article's frame.

The reviewer checked opening, caveat/contrast clusters, cadence, transitions,
section logic and ending against all three. Boundaries appear beside access,
client authority, research attribution and finance association; they do not form
a repeated not-X-but-Y rhythm. “Later work,” “After the owner chooses,” “This
resembles,” and “Before naming another agent” advance different parts of the
argument. The final placement diagnostic differs from the earlier fresh-session,
replacement-document and payment-contact endings. Generic abstraction, symmetry,
product promotion, fake personal authority and lexical-density clusters were also
checked. No upstream angle/structure repair is indicated.

## Independent disclosure review

Reviewer: `/root/agency_article_disclosure`,
`branding.sanitization-reviewer` v0.1.0. Verdict: cleared for the designated
canonical review candidate; no blocked text, repair or missing operator decision.
The reviewer verified the authoritative boundary in guide 450 v1.2.0. The unnamed
client examples are explicitly hypothetical. Access references remain general;
there are no credentials, security-sensitive implementation details, customer or
account data, private metrics, actual customer terms, or private financial records.
The internal notes are excluded from public copy. Clearance is not publication
authority or operator approval.

## Delivery state

Canonical review draft only, about 1,000 words. Images, channel variants, website
integration and publication are intentionally skipped. No code/build/restart,
commit or push belongs to this article run. Claim/voice/disclosure passes remain
independent and were performed sequentially. The workflow's final content record
and canonical artifact provide the durable recovery index; this file is internal
editorial detail, not a second article or voice authority.
