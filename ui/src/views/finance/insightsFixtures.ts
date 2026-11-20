import type { SchemaFinanceContentOut } from '@/api'
import { chartProjection, money } from './overviewFixtures'

export function analysisContent(
  result: Record<string, unknown> | null,
  state = 'current',
): SchemaFinanceContentOut {
  return {
    state,
    result,
    request_id: 7,
    bytes: null,
    content_base64: null,
    content_type: null,
    disposition: null,
    filename: null,
    inline_allowed: null,
    next_action: null,
    report: null,
    sha256: null,
    snapshot: null,
  }
}

export function insightFixture() {
  const projection = chartProjection()
  projection.rows = [
    {
      record_ref: 'book:one',
      collection: 'bookkeeping',
      date: '2026-09-01',
      counterparty: 'Synthetic vendor',
      amount: money('2000'),
      category_ref: 'category:adopted',
    },
    {
      record_ref: 'book:two',
      collection: 'bookkeeping',
      date: '2026-09-02',
      counterparty: 'Second item',
      amount: money('1000'),
    },
    {
      record_ref: 'book:outside',
      collection: 'bookkeeping',
      date: '2026-09-03',
      counterparty: 'Not considered item',
      amount: money('1000'),
    },
  ]
  const result = {
    format: 'finance-management-result-v1',
    request_id: '7',
    producer_ref: 'agent:preparer',
    run_ref: 'run:preparer',
    created_at: '2026-10-14T20:00:00Z',
    state: 'supported-opportunities',
    source_snapshot: { revision: projection.snapshot.revision, sha256: projection.snapshot.sha256 },
    query: { ...projection.query },
    selection: { record_refs: [], source_refs: [] },
    taxonomy_proposal: {
      taxonomy_ref: 'taxonomy:proposed',
      state: 'proposed',
      categories: [
        {
          category_ref: 'proposal:software',
          label: 'Software tools',
          description: 'A descriptive group proposed by the reviewer.',
          rationale: 'Source descriptions support inspecting this grouping.',
          evidence_refs: ['source:one'],
          uncertainty: 'Confirm the original source purpose.',
        },
      ],
    },
    category_proposals: [
      {
        proposal_ref: 'proposal:one',
        state: 'proposed',
        record_ref: 'book:one',
        target_digest_sha256: 'b'.repeat(64),
        taxonomy_ref: 'taxonomy:proposed',
        category_ref: 'proposal:software',
        confidence: 'medium',
        rationale: 'The source describes an operating tool.',
        evidence_refs: ['source:one'],
        uncertainty: 'Business use needs confirmation.',
      },
      {
        proposal_ref: 'proposal:two',
        state: 'proposed',
        record_ref: 'book:two',
        target_digest_sha256: 'c'.repeat(64),
        taxonomy_ref: 'taxonomy:proposed',
        category_ref: null,
        confidence: 'low',
        rationale: 'Insufficient purpose evidence to assign a category.',
        evidence_refs: ['source:two'],
        uncertainty: 'Purpose is unknown.',
      },
    ],
    proposal_coverage: {
      considered_record_refs: ['book:one', 'book:two'],
      unassigned_record_refs: ['book:two'],
      gaps: ['Other selected records were not considered in this bounded review.'],
    },
    opportunities: [
      {
        opportunity_ref: 'finding:one',
        kind: 'source-quality',
        title: 'Check the original purpose',
        attention_level: 'watch',
        rationale: 'The source does not establish business use.',
        evidence_refs: ['source:one'],
        uncertainty: 'No financial conclusion until source inspection.',
        assumptions: ['The supplied description is incomplete.'],
      },
    ],
    evidence_refs: ['source:one'],
    gaps: [],
    report_sha256: 'd'.repeat(64),
    review: {
      reviewer_ref: 'agent:control',
      run_ref: 'run:control',
      verdict: 'approved',
      reviewed_at: '2026-10-14T20:05:00Z',
      target_digest_sha256: 'e'.repeat(64),
    },
  }
  const analysis = analysisContent(result)
  return { projection, analysis, result }
}
