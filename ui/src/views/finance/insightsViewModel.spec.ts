import { describe, expect, it } from 'vitest'
import { aiCategory, insightsModel } from './insightsViewModel'
import { analysisContent, insightFixture } from './insightsFixtures'

describe('reviewed AI proposal presentation', () => {
  it('keeps exact proposed taxonomy/assignments/coverage separate from source categories', () => {
    const { projection, analysis } = insightFixture(),
      before = JSON.stringify(projection)
    const model = insightsModel(analysis, projection)
    expect(model.current).toBe(true)
    expect(model.supplied).toBe(true)
    expect(model.partial).toBe(true)
    expect(model.groups[0]!.label).toBe('Software tools')
    expect(model.groups[0]!.items[0]!.label).toContain('Synthetic vendor · 2026-09-01 · USD 20.00')
    expect(aiCategory(model, 'book:one')).toEqual({
      category: 'Software tools · proposed',
      confidence: 'medium',
    })
    expect(aiCategory(model, 'book:two')).toEqual({
      category: 'Unassigned by review',
      confidence: 'low',
    })
    expect(aiCategory(model, 'book:outside')).toEqual({
      category: 'Not considered',
      confidence: 'Unavailable',
    })
    expect(projection.rows[0]!.category_ref).toBe('category:adopted')
    expect(JSON.stringify(projection)).toBe(before)
    expect(model.opportunities[0]).toMatchObject({
      title: 'Check the original purpose',
      attention: 'watch',
      uncertainty: 'No financial conclusion until source inspection.',
    })
  })
  it('distinguishes legacy missing material, reviewed empty output and pending result', () => {
    const { projection, result } = insightFixture()
    const legacy = { ...result } as Record<string, unknown>
    delete legacy.taxonomy_proposal
    delete legacy.category_proposals
    delete legacy.proposal_coverage
    const legacyModel = insightsModel(analysisContent(legacy), projection)
    expect(legacyModel.supplied).toBe(false)
    expect(aiCategory(legacyModel, 'book:one').category).toBe('Not supplied by this review')
    const empty = {
      ...result,
      taxonomy_proposal: { ...result.taxonomy_proposal, categories: [] },
      category_proposals: [],
      proposal_coverage: { considered_record_refs: [], unassigned_record_refs: [], gaps: [] },
      opportunities: [],
    }
    const emptyModel = insightsModel(analysisContent(empty), projection)
    expect(emptyModel.emptyCategories).toBe(true)
    expect(aiCategory(emptyModel, 'book:one').category).toBe('Not considered')
    expect(insightsModel(analysisContent(null, 'awaiting_agent'), projection).hasResult).toBe(false)
    expect(aiCategory(insightsModel(null, projection), 'book:one').category).toBe(
      'Awaiting reviewed result',
    )
  })
  it.each(['stale', 'revision', 'date', 'category', 'currency', 'comparison'])(
    'never buckets current records from %s context',
    (change) => {
      const { projection, analysis } = insightFixture()
      if (change === 'stale') analysis.state = 'stale'
      else if (change === 'revision') projection.snapshot.revision = 2
      else
        projection.query = {
          ...projection.query,
          ...(change === 'date'
            ? { start: '2026-10-01' }
            : change === 'comparison'
              ? { comparison: { start: '2025-09-01', end: '2025-09-30' } }
              : { [change === 'category' ? 'category_ref' : 'currency']: 'changed' }),
        }
      const model = insightsModel(analysis, projection)
      expect(model.current).toBe(false)
      expect(model.historical).toBe(true)
      expect(model.groups[0]!.items[0]!.label).toBe('Item from the original review')
      expect(aiCategory(model, 'book:one').confidence).toBe('Unavailable')
      expect(aiCategory(model, 'book:one').category).not.toContain('Software tools')
    },
  )
  it('preserves current review across paging/sorting but marks unseen considered items accurately', () => {
    const { projection, analysis } = insightFixture()
    projection.query = {
      ...projection.query,
      offset: 50,
      limit: 25,
      sort: 'amount',
      descending: true,
    }
    projection.rows = []
    const model = insightsModel(analysis, projection)
    expect(model.current).toBe(true)
    expect(model.groups[0]!.items[0]!.label).toBe('Reviewed item outside this page')
  })
})
