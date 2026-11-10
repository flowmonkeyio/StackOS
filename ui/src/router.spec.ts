import { describe, expect, it } from 'vitest'
import router from './router'

describe('approved human routes', () => {
  it.each(['', '/tasks?view=graph&task=workflow-793&detail=workflow-793&ticket=nav', '/schedules', '/cost-budget', '/connections', '/agents', '/library?tab=saved&plugin=engineering&type=decision&item=decision:42', '/plugins', '/plugins/support', '/plugins/agency', '/plugins/engineering/workflows/engineering.tracked-delivery', '/finance?analysis=42'])('resolves the exact project destination %s', (suffix) => {
    const route = router.resolve(`/projects/7${suffix}`)
    expect(route.matched.length).toBeGreaterThan(0)
    expect(route.params.id).toBe('7')
    expect(route.matched.some(record => record.redirect)).toBe(false)
  })
  it.each(['inbox', 'activity', 'runs', 'runs/42', 'agent-requests', 'action-calls', 'agent-presets', 'capabilities', 'operations', 'workflow-templates', 'data', 'resources', 'agent-defaults', 'overview', 'settings', 'diagnostics', 'advanced'])('removes %s with no forwarding shim', (suffix) => {
    expect(router.resolve(`/projects/7/${suffix}`).matched).toEqual([])
  })
  it('preserves scoped query identity and separated global destinations', () => {
    const route = router.resolve('/projects/7/library?tab=saved&plugin=engineering&type=decision&item=decision:42')
    expect(route.query).toEqual({ tab: 'saved', plugin: 'engineering', type: 'decision', item: 'decision:42' })
    for (const path of ['/', '/accounts', '/setup']) expect(router.resolve(path).matched).toHaveLength(1)
  })
})
