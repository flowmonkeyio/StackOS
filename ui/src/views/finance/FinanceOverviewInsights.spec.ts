import { afterEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import FinanceOverviewInsights from './FinanceOverviewInsights.vue'
import { insightsModel } from './insightsViewModel'
import { analysisContent, insightFixture } from './insightsFixtures'

afterEach(() => {
  document.body.innerHTML = ''
})
const mountInsights = (model: ReturnType<typeof insightsModel>, props = {}) =>
  mount(FinanceOverviewInsights, {
    props: {
      model,
      status: 'Reviewed analysis · current source',
      hasRequest: true,
      requestAllowed: true,
      refreshAllowed: true,
      ...props,
    },
    attachTo: document.body,
  })
describe('read-only Overview category and concern presentation', () => {
  it('shows proposed groups, confidence, unassigned scope and reviewed concerns with keyboard inspect intents', async () => {
    const { analysis, projection } = insightFixture()
    const wrapper = mountInsights(insightsModel(analysis, projection))
    expect(wrapper.text()).toContain('do not change source categories or financial totals')
    expect(wrapper.text()).toContain('Software tools · proposed · 1 item(s)')
    expect(wrapper.text()).toContain('2 records considered · 1 proposed assignments · 1 unassigned')
    expect(wrapper.text()).toContain('Partial review')
    expect(wrapper.text()).toContain('Confidence: medium')
    expect(wrapper.text()).toContain('Confidence: low')
    expect(wrapper.text()).toContain('Check the original purpose')
    expect(wrapper.text()).toContain('Attention: watch')
    expect(wrapper.text()).toContain('No financial conclusion until source inspection')
    const inspect = wrapper.findAll('button').find((button) => button.text() === 'Inspect item')!
    expect(inspect.classes()).toContain('focus-ring')
    ;(inspect.element as HTMLButtonElement).focus()
    expect(document.activeElement).toBe(inspect.element)
    await inspect.trigger('click')
    expect(wrapper.emitted('inspect')?.[0]).toEqual(['book:one'])
    await wrapper
      .findAll('button')
      .find((button) => button.text() === 'Inspect source:one')!
      .trigger('click')
    expect(wrapper.emitted('inspect')?.[1]).toEqual(['source:one'])
    await wrapper
      .findAll('button')
      .find((button) => button.text() === 'Request new category and concern review')!
      .trigger('click')
    await wrapper
      .findAll('button')
      .find((button) => button.text() === 'Refresh reviewed suggestions')!
      .trigger('click')
    expect(wrapper.emitted('request')).toHaveLength(1)
    expect(wrapper.emitted('refresh')).toHaveLength(1)
    wrapper.unmount()
  })
  it('keeps historical and different-selection items separate from current row labels', () => {
    const { analysis, projection } = insightFixture()
    analysis.state = 'stale'
    const wrapper = mountInsights(insightsModel(analysis, projection), {
      status: 'Historical analysis · source changed',
    })
    expect(wrapper.text()).toContain('Historical review: the source changed')
    expect(wrapper.text()).toContain('do not bucket current records')
    expect(wrapper.text()).toContain('Item from the original review')
    expect(wrapper.text()).not.toContain('Synthetic vendor')
    wrapper.unmount()
  })
  it('distinguishes missing legacy fields from reviewed empty output and explicit unassigned without confidence', async () => {
    const { result, projection } = insightFixture()
    const legacy = { ...result } as Record<string, unknown>
    delete legacy.taxonomy_proposal
    delete legacy.category_proposals
    delete legacy.proposal_coverage
    const wrapper = mountInsights(insightsModel(analysisContent(legacy), projection))
    expect(wrapper.text()).toContain('legacy review did not supply category proposals')
    expect(wrapper.text()).not.toContain('Reviewed empty category output')
    const empty = {
      ...result,
      category_proposals: [],
      taxonomy_proposal: { ...result.taxonomy_proposal, categories: [] },
      proposal_coverage: { considered_record_refs: [], unassigned_record_refs: [], gaps: [] },
      opportunities: [],
    }
    await wrapper.setProps({
      model: insightsModel(analysisContent(empty), projection),
    })
    expect(wrapper.text()).toContain('Reviewed empty category output')
    expect(wrapper.text()).toContain('no concern findings')
    const unassigned = {
      ...empty,
      proposal_coverage: {
        considered_record_refs: ['book:two'],
        unassigned_record_refs: ['book:two'],
        gaps: [],
      },
    }
    await wrapper.setProps({
      model: insightsModel(analysisContent(unassigned), projection),
    })
    expect(wrapper.text()).toContain('item-specific confidence and rationale were not supplied')
    wrapper.unmount()
  })
  it.each([
    'Awaiting agent',
    'Running in the linked agent run',
    'Agent run blocked',
    'Analysis failed',
    'Analysis request submission is unverified',
  ])('shows %s without manufacturing reviewed categories', (status) => {
    const wrapper = mountInsights(insightsModel(null, null), {
      status,
      requestAllowed: false,
      refreshAllowed: false,
    })
    expect(wrapper.text()).toContain(status)
    expect(wrapper.text()).toContain('No reviewed category or concern material')
    expect(wrapper.text()).not.toContain('Software tools')
    expect(
      wrapper.findAll('button').every((button) => button.attributes('disabled') !== undefined),
    ).toBe(true)
    wrapper.unmount()
  })
  it('escapes labels and finding text and never invents legacy attention severity', () => {
    const { result, projection } = insightFixture()
    result.taxonomy_proposal.categories[0]!.label = '<img src=x onerror=alert(1)>'
    result.opportunities[0]!.title = '<script>alert(1)</script>'
    const legacyFinding = { ...result.opportunities[0] } as Record<string, unknown>
    delete legacyFinding.title
    delete legacyFinding.attention_level
    const wrapper = mountInsights(
      insightsModel(
        analysisContent({
          ...result,
          opportunities: [
            result.opportunities[0],
            { ...legacyFinding, opportunity_ref: 'finding:legacy' },
          ],
        }),
        projection,
      ),
    )
    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.find('script').exists()).toBe(false)
    expect(wrapper.text()).toContain('<img src=x onerror=alert(1)>')
    expect(wrapper.text()).toContain('<script>alert(1)</script>')
    expect(wrapper.text()).toContain('Attention level not supplied')
    wrapper.unmount()
  })
})
