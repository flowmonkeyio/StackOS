import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { callOperation } from '@/lib/operations'
import type { DesktopAgentExecutionCapabilities, DesktopMcpHostStatus } from '@/lib/desktop'
import Dialog from './ProjectSetupLaunchDialog.vue'
import { setupFixture } from './setupFixtures'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
let wrapper: VueWrapper | undefined
beforeEach(() => vi.mocked(callOperation).mockReset())
afterEach(() => { wrapper?.unmount(); document.body.innerHTML = '' })

// Capability combinations from the canonical host catalog, without requiring
// those native apps or credentials to be installed in a component test.
const capabilities: DesktopAgentExecutionCapabilities = {
  sandboxes: ['workspace-write'], review_sandbox: 'read-only', models: [],
  custom_models: false, provider_selection: false, profile_selection: false, requires_provider_model: false,
  notes: 'Fixture', sources: [], checked_on: '2026-11-13', credential_status: 'not_checked', model_access: 'not_checked',
}
const hosts: DesktopMcpHostStatus[] = [{
  host_key: 'hermes', display_name: 'Hermes', available: true,
  agent_execution: { ...capabilities, models: [], recommended_model: null,
    custom_models: true, provider_selection: true, profile_selection: true, requires_provider_model: true },
}, {
  host_key: 'grok', display_name: 'Grok', available: true,
  agent_execution: { ...capabilities, models: ['grok-build'], recommended_model: null,
    custom_models: true, provider_selection: false, profile_selection: false, requires_provider_model: false },
}, {
  host_key: 'fixed', display_name: 'Fixed app', available: true,
  agent_execution: { ...capabilities, models: ['recommended-model'], recommended_model: 'recommended-model',
    custom_models: false, provider_selection: false, profile_selection: false, requires_provider_model: false },
}, {
  host_key: 'native', display_name: 'Native default app', available: true,
  agent_execution: { ...capabilities, models: ['native-model'] },
}]

async function dialog(key: string, preferences: Record<string, string> = {}) {
  vi.mocked(callOperation).mockResolvedValue({ items: [hosts.find(host => host.host_key === key)!, ...hosts.filter(host => host.host_key !== key)] })
  const start = vi.fn().mockResolvedValue(null)
  wrapper = mount(Dialog, { props: { state: setupFixture({ launch_preferences: preferences }), pending: false, error: null, start },
    attachTo: document.body, global: { stubs: { teleport: true, RouterLink: true } } })
  await flushPromises()
  return start
}
const field = (label: string) => wrapper!.findAllComponents({ name: 'UiFormField' }).find(item => item.props('label') === label)!
const button = (label: string) => wrapper!.findAll('button').find(item => item.text() === label)!
async function chooseCustom() {
  const select = field('Model').findComponent({ name: 'UiSelect' })
  await select.get('[role="combobox"]').trigger('click')
  const choice = select.findAll('[role="option"]').find(item => /custom/i.test(item.text()))
  expect(choice, 'the catalog permits custom models').toBeDefined()
  await choice!.trigger('click')
}

it('accepts an initial custom model from an empty catalog and requires its provider', async () => {
  const start = await dialog('hermes')
  expect(button('Start setup').attributes('disabled')).toBeDefined()
  await field('Model').get('input').setValue('provider/model-alias')
  expect(button('Start setup').attributes('disabled')).toBeDefined()
  await field('Provider').get('input').setValue('configured-provider')
  await field('Agent profile').get('input').setValue('project-profile')
  expect(button('Start setup').attributes('disabled')).toBeUndefined()
  await button('Start setup').trigger('click')
  expect(start).toHaveBeenCalledWith({ host_key: 'hermes', model: 'provider/model-alias', provider: 'configured-provider', profile: 'project-profile' })
})

it('offers custom entry alongside fixed choices and never silently defaults an empty custom value', async () => {
  const start = await dialog('grok')
  await chooseCustom()
  expect(button('Start setup').attributes('disabled')).toBeDefined()
  await field('Model').get('input').setValue('my-installed-alias')
  expect(field('Model').get('input').attributes('id')).not.toBe(field('Model').get('[role="combobox"]').attributes('id'))
  await wrapper!.setProps({ pending: true })
  expect(field('Model').get('input').attributes('disabled')).toBeDefined()
  await wrapper!.setProps({ pending: false })
  await button('Start setup').trigger('click')
  expect(start).toHaveBeenCalledWith({ host_key: 'grok', model: 'my-installed-alias', provider: null, profile: null })
  const select = field('Model').findComponent({ name: 'UiSelect' })
  select.vm.$emit('update:modelValue', 'grok-build')
  await flushPromises()
  expect(field('Model').find('input').exists()).toBe(false)
  await button('Start setup').trigger('click')
  expect(start).toHaveBeenLastCalledWith(expect.objectContaining({ model: 'grok-build' }))
})

it.each(['hermes', 'grok'])('retains a saved %s custom model and allows an explicit replacement', async key => {
  const start = await dialog(key, { host_key: key, model: 'saved/custom-model', ...(key === 'hermes' ? { provider: 'saved-provider', profile: 'saved-profile' } : {}) })
  expect(wrapper!.find('input').exists()).toBe(false)
  expect(wrapper!.text()).toContain('saved/custom-model')
  await button('Change').trigger('click')
  expect((field('Model').get('input').element as HTMLInputElement).value).toBe('saved/custom-model')
  await field('Model').get('input').setValue('')
  expect(field('Model').find('input').exists()).toBe(true)
  expect(button('Start setup').attributes('disabled')).toBeDefined()
  await field('Model').get('input').setValue('replacement/custom-model')
  await button('Start setup').trigger('click')
  expect(start).toHaveBeenCalledWith({ host_key: key, model: 'replacement/custom-model', provider: key === 'hermes' ? 'saved-provider' : null, profile: key === 'hermes' ? 'saved-profile' : null })
})

it('uses only the selected host capability and preserves fixed recommendations', async () => {
  await dialog('hermes')
  await field('Model').get('input').setValue('custom/old-host')
  field('Agent app').findComponent({ name: 'UiSelect' }).vm.$emit('update:modelValue', 'fixed')
  await flushPromises()
  expect(field('Model').find('input').exists()).toBe(false)
  const select = field('Model').findComponent({ name: 'UiSelect' })
  expect(select.props('modelValue')).toBe('recommended-model')
  expect(select.props('options').some((option: { label: string }) => /custom/i.test(option.label))).toBe(false)
  expect(wrapper!.text()).toContain('recommended-model (Recommended)')
})

it.each(['native', 'grok'])('keeps the native default when %s has no recommendation', async key => {
  const start = await dialog(key)
  const select = field('Model').findComponent({ name: 'UiSelect' })
  expect(select.props('modelValue')).toBe('')
  expect(select.props('options')).toContainEqual({ value: '', label: 'Agent default' })
  expect(wrapper!.text()).not.toContain('(Recommended)')
  await button('Start setup').trigger('click')
  expect(start).toHaveBeenCalledWith({ host_key: key, model: null, provider: null, profile: null })
})
