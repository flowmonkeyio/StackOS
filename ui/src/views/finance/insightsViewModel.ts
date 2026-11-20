import type { SchemaFinanceContentOut, SchemaFinanceProjectionOut } from '@/api'
import { formatMoney, object, objects, refs, text } from '@/lib/finance'

export type InsightProposal = {
  recordRef: string
  label: string
  categoryRef: string
  confidence: string
  rationale: string
  uncertainty: string
  evidenceRefs: string[]
}
export type InsightsModel = ReturnType<typeof insightsModel>
function selectionSignature(value: unknown): string {
  // Compare displayed population context, not pagination or sort order.
  const query = object(value)
  const ignored = new Set([
    'offset',
    'limit',
    'sort',
    'descending',
    'expected_snapshot',
    'workspace_ref',
    'access_generation',
  ])
  return JSON.stringify(
    Object.fromEntries(
      Object.entries(query)
        .filter(
          ([key, value]) =>
            !ignored.has(key) && value !== null && value !== undefined && value !== '',
        )
        .sort(([a], [b]) => a.localeCompare(b)),
    ),
    (_key, child) =>
      child && typeof child === 'object' && !Array.isArray(child)
        ? Object.fromEntries(Object.entries(child).sort(([a], [b]) => a.localeCompare(b)))
        : child,
  )
}
export function insightsModel(
  analysis: SchemaFinanceContentOut | null,
  projection: SchemaFinanceProjectionOut | null,
) {
  const published = analysis?.state === 'current' || analysis?.state === 'stale'
  const result = published ? object(analysis.result) : {}
  const hasResult = Object.keys(result).length > 0
  const source = object(result.source_snapshot)
  const snapshotMatches =
    Boolean(projection) &&
    source.sha256 === projection?.snapshot.sha256 &&
    source.revision === projection?.snapshot.revision
  const selectionMatches =
    Boolean(projection) &&
    selectionSignature(result.query) === selectionSignature(projection?.query)
  const current = analysis?.state === 'current' && snapshotMatches && selectionMatches
  const historical = published && hasResult && !current
  const historicalSource = historical && (analysis?.state === 'stale' || !snapshotMatches)
  const taxonomy = object(result.taxonomy_proposal)
  const coverage = object(result.proposal_coverage)
  const supplied =
    published &&
    Object.hasOwn(result, 'taxonomy_proposal') &&
    Array.isArray(result.category_proposals) &&
    Object.hasOwn(result, 'proposal_coverage')
  const considered = refs(coverage.considered_record_refs),
    unassigned = refs(coverage.unassigned_record_refs)
  const label = (refValue: string) => {
    const row = current ? projection?.rows.find((row) => row.record_ref === refValue) : undefined
    return row
      ? [
          text(row.counterparty) || 'Unnamed item',
          text(row.date) || 'Undated',
          formatMoney(row.amount),
        ].join(' · ')
      : historical
        ? 'Item from the original review'
        : 'Reviewed item outside this page'
  }
  const proposals: InsightProposal[] = objects(result.category_proposals).map((row) => ({
    recordRef: text(row.record_ref),
    label: label(text(row.record_ref)),
    categoryRef: text(row.category_ref),
    confidence: text(row.confidence),
    rationale: text(row.rationale),
    uncertainty: text(row.uncertainty),
    evidenceRefs: refs(row.evidence_refs),
  }))
  const groups = objects(taxonomy.categories).map((category) => ({
    categoryRef: text(category.category_ref),
    label: text(category.label),
    description: text(category.description),
    rationale: text(category.rationale),
    uncertainty: text(category.uncertainty),
    evidenceRefs: refs(category.evidence_refs),
    items: proposals.filter((proposal) => proposal.categoryRef === category.category_ref),
  }))
  const unassignedItems = unassigned.map((recordRef) => ({
    recordRef,
    label: label(recordRef),
    proposal: proposals.find((proposal) => proposal.recordRef === recordRef),
  }))
  const opportunities = objects(result.opportunities).map((finding) => ({
    ref: text(finding.opportunity_ref),
    title: text(finding.title) || text(finding.kind).replaceAll('-', ' '),
    kind: text(finding.kind).replaceAll('-', ' '),
    attention: text(finding.attention_level),
    rationale: text(finding.rationale),
    uncertainty: text(finding.uncertainty),
    assumptions: refs(finding.assumptions),
    evidenceRefs: refs(finding.evidence_refs),
    impact: finding.estimated_impact ? formatMoney(finding.estimated_impact) : '',
  }))
  const gaps = [...refs(result.gaps), ...refs(coverage.gaps)]
  const query = object(result.query)
  return {
    current,
    historical,
    historicalSource,
    published,
    hasResult,
    supplied,
    snapshotMatches,
    selectionMatches,
    groups,
    proposals,
    considered,
    unassignedItems,
    opportunities,
    gaps,
    partial: gaps.length > 0 || result.state === 'incomplete-data',
    resultState: text(result.state),
    emptyCategories: supplied && !groups.length && !proposals.length && !considered.length,
    window:
      query.start && query.end
        ? `${text(query.start)} through ${text(query.end)}`
        : 'Original window unavailable',
    sourceRevision: typeof source.revision === 'number' ? String(source.revision) : 'Unavailable',
  }
}
export function aiCategory(model: InsightsModel, recordRef: string) {
  if (model.historical)
    return {
      category: model.historicalSource ? 'Historical review' : 'Different review selection',
      confidence: 'Unavailable',
    }
  if (!model.published || !model.hasResult)
    return { category: 'Awaiting reviewed result', confidence: 'Unavailable' }
  if (!model.supplied) return { category: 'Not supplied by this review', confidence: 'Unavailable' }
  if (!model.considered.includes(recordRef))
    return { category: 'Not considered', confidence: 'Unavailable' }
  const proposal = model.proposals.find((proposal) => proposal.recordRef === recordRef)
  if (!proposal?.categoryRef)
    return { category: 'Unassigned by review', confidence: proposal?.confidence || 'Not supplied' }
  const category = model.groups.find((group) => group.categoryRef === proposal.categoryRef)
  return {
    category: `${category?.label || 'Category label unavailable'} · proposed`,
    confidence: proposal.confidence,
  }
}
