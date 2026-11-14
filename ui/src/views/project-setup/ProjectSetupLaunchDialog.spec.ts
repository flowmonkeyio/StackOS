import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { callOperation } from '@/lib/operations'
import { ProjectSetupOutStatus } from '@/api'
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
  vi.mocked(callOperation).mockImplementation(async name => name === 'hostMcp.status'
    ? { items: [hosts.find(host => host.host_key === key)!, ...hosts.filter(host => host.host_key !== key)] }
    : setupFixture({ launch_preferences: { host_key: key, ...preferences } }))
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
  await button('Advanced options').trigger('click')
  await field('Agent profile').get('input').setValue('project-profile')
  expect(button('Start setup').attributes('disabled')).toBeUndefined()
  await button('Start setup').trigger('click')
  expect(start).toHaveBeenCalledWith({ host_key: 'hermes', model: 'provider/model-alias', model_reasoning_effort: null, provider: 'configured-provider', profile: 'project-profile' })
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
  expect(start).toHaveBeenCalledWith({ host_key: 'grok', model: 'my-installed-alias', model_reasoning_effort: null, provider: null, profile: null })
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
  expect(start).toHaveBeenCalledWith({ host_key: key, model: 'replacement/custom-model', model_reasoning_effort: null, provider: key === 'hermes' ? 'saved-provider' : null, profile: key === 'hermes' ? 'saved-profile' : null })
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
  expect(start).toHaveBeenCalledWith({ host_key: key, model: null, model_reasoning_effort: null, provider: null, profile: null })
})

it('uses model-specific reasoning from the host response and clears it on model and host changes', async () => {
  const fixed = hosts.find(host => host.host_key === 'fixed')!
  vi.mocked(callOperation).mockImplementation(async name => name === 'hostMcp.status'
    ? { items: [{ ...fixed, agent_execution: { ...fixed.agent_execution!,
      reasoning_efforts: { 'recommended-model': ['low', 'high'], 'other-model': ['low'] }, models: ['recommended-model', 'other-model'] } }, ...hosts.filter(host => host !== fixed)] }
    : setupFixture({ launch_preferences: { host_key: 'fixed' } }))
  const start = vi.fn().mockResolvedValue(null)
  wrapper = mount(Dialog, { props: { state: setupFixture(), pending: false, error: null, start },
    attachTo: document.body, global: { stubs: { teleport: true, RouterLink: true } } })
  await flushPromises()
  const reasoningSelect = field('Reasoning').findComponent({ name: 'UiSelect' })
  expect(reasoningSelect.props('options').map((option: { value: string }) => option.value)).toEqual(['', 'low', 'high'])
  reasoningSelect.vm.$emit('update:modelValue', 'high')
  await flushPromises()
  expect(wrapper.text()).toContain('Reasoning: high')
  await button('Start setup').trigger('click')
  expect(start).toHaveBeenLastCalledWith(expect.objectContaining({ model: 'recommended-model', model_reasoning_effort: 'high' }))
  field('Model').findComponent({ name: 'UiSelect' }).vm.$emit('update:modelValue', 'recommended-model')
  await flushPromises()
  expect(reasoningSelect.props('modelValue')).toBe('high')
  field('Model').findComponent({ name: 'UiSelect' }).vm.$emit('update:modelValue', 'other-model')
  await flushPromises()
  expect(reasoningSelect.props('modelValue')).toBe('')
  expect(reasoningSelect.props('options').map((option: { value: string }) => option.value)).toEqual(['', 'low'])
  reasoningSelect.vm.$emit('update:modelValue', 'low'); await flushPromises()
  field('Model').findComponent({ name: 'UiSelect' }).vm.$emit('update:modelValue', 'recommended-model')
  await flushPromises()
  expect(reasoningSelect.props('modelValue')).toBe('low')
  field('Model').findComponent({ name: 'UiSelect' }).vm.$emit('update:modelValue', 'other-model')
  await flushPromises()
  expect(reasoningSelect.props('modelValue')).toBe('low')
  field('Agent app').findComponent({ name: 'UiSelect' }).vm.$emit('update:modelValue', 'hermes')
  await flushPromises()
  expect(field('Reasoning').findComponent({ name: 'UiSelect' }).props('options')).toEqual([{ value: '', label: 'Agent default' }])
  expect((field('Provider').get('input').element as HTMLInputElement).value).toBe('')
})

it('retains an unsupported saved effort for repair and permits a real clear to default', async () => {
  const start = await dialog('native', { host_key: 'native', model: 'native-model', model_reasoning_effort: 'old-effort' })
  expect(button('Start setup').attributes('disabled')).toBeDefined()
  await button('Change').trigger('click')
  expect(wrapper!.text()).toContain('Reasoning old-effort is unavailable')
  expect(field('Reasoning').findComponent({ name: 'UiSelect' }).props('options')).toContainEqual({ value: 'old-effort', label: 'old-effort (not verified for this model)' })
  field('Reasoning').findComponent({ name: 'UiSelect' }).vm.$emit('update:modelValue', '')
  await flushPromises()
  await button('Start setup').trigger('click')
  expect(start).toHaveBeenCalledWith(expect.objectContaining({ model: 'native-model', model_reasoning_effort: null }))
})

it('keeps a compatible effort across a deliberate harness change', async () => {
  const fixed = hosts.find(host => host.host_key === 'fixed')!
  const compatible = { ...fixed, host_key: 'compatible', agent_execution: { ...fixed.agent_execution!, reasoning_efforts: { 'recommended-model': ['high'] } } }
  vi.mocked(callOperation).mockResolvedValue({ items: [compatible, { ...compatible, host_key: 'fixed' }] })
  const start = vi.fn().mockResolvedValue(null)
  wrapper = mount(Dialog, { props: { state: setupFixture({ launch_preferences: { host_key: 'fixed', model: 'recommended-model', model_reasoning_effort: 'high' } }), pending: false, error: null, start }, global: { stubs: { teleport: true, RouterLink: true } } })
  await flushPromises(); await button('Change').trigger('click')
  field('Agent app').findComponent({ name: 'UiSelect' }).vm.$emit('update:modelValue', 'compatible')
  await flushPromises()
  expect(field('Reasoning').findComponent({ name: 'UiSelect' }).props('modelValue')).toBe('high')
  await button('Start setup').trigger('click')
  expect(start).toHaveBeenCalledWith(expect.objectContaining({ host_key: 'compatible', model: 'recommended-model', model_reasoning_effort: 'high' }))
})

it('clears an optional saved profile only on this launch through advanced options', async () => {
  const start = await dialog('hermes', { host_key: 'hermes', model: 'custom/model', provider: 'configured', profile: 'saved-profile' })
  await button('Change').trigger('click')
  expect(field('Provider')).toBeDefined()
  expect(wrapper!.text()).not.toContain('Agent profile')
  await button('Advanced options').trigger('click')
  await field('Agent profile').get('input').setValue('')
  await button('Start setup').trigger('click')
  expect(start).toHaveBeenCalledWith(expect.objectContaining({ provider: 'configured', profile: null }))
})

it.each([ProjectSetupOutStatus.setup_needed, ProjectSetupOutStatus.updates_available])('preserves effective saved preferences with an inherited host for %s', async status => {
  const saved = { model: 'custom/model', provider: 'configured', profile: 'project-profile' }
  const effective = { host_key: 'hermes', ...saved }
  // The resolver selects Hermes even though another available host appears first.
  vi.mocked(callOperation).mockImplementation(async name => name === 'hostMcp.status'
    ? { items: [hosts.find(host => host.host_key === 'fixed')!, ...hosts.filter(host => host.host_key !== 'fixed')] }
    : setupFixture({ launch_preferences: effective }))
  const start = vi.fn().mockResolvedValue(null)
  wrapper = mount(Dialog, { props: { state: setupFixture({ status, launch_preferences: saved }), pending: false, error: null, start },
    attachTo: document.body, global: { stubs: { teleport: true, RouterLink: true } } })
  await flushPromises()
  expect(wrapper.text()).toContain('Hermes · Model: custom/model')
  await button(status === 'updates_available' ? 'Update workflows' : 'Start setup').trigger('click')
  expect(start).toHaveBeenLastCalledWith({ ...effective, model_reasoning_effort: null })
  field('Agent app').findComponent({ name: 'UiSelect' }).vm.$emit('update:modelValue', 'grok')
  await flushPromises()
  await button(status === 'updates_available' ? 'Update workflows' : 'Start setup').trigger('click')
  expect(start).toHaveBeenLastCalledWith({ host_key: 'grok', model: null, model_reasoning_effort: null, provider: null, profile: null })
})

it('preserves saved reasoning when the resolver supplies the omitted host', async () => {
  const saved = { model: 'recommended-model', model_reasoning_effort: 'high' }
  const fixed = hosts.find(host => host.host_key === 'fixed')!
  vi.mocked(callOperation).mockImplementation(async name => name === 'hostMcp.status'
    ? { items: [{ ...fixed, agent_execution: { ...fixed.agent_execution!, reasoning_efforts: { 'recommended-model': ['high'] } } }] }
    : setupFixture({ launch_preferences: { host_key: 'fixed', ...saved } }))
  const start = vi.fn().mockResolvedValue(null)
  wrapper = mount(Dialog, { props: { state: setupFixture({ launch_preferences: saved }), pending: false, error: null, start },
    attachTo: document.body, global: { stubs: { teleport: true, RouterLink: true } } })
  await flushPromises()
  expect(wrapper.text()).toContain('Reasoning: high')
  await button('Start setup').trigger('click')
  expect(start).toHaveBeenLastCalledWith({ host_key: 'fixed', ...saved, provider: null, profile: null })
})

it('shows resolved workflow names, truthful legacy and unavailable states, and retains Version details', async () => {
  await dialog('native')
  const state = setupFixture()
  const workflow = state.workflows![0]!
  await wrapper!.setProps({ state: { ...state, workflows: [
    { ...workflow, workflow_key: 'legacy.example', status: 'unknown', successful: null,
      available_sources: [{ kind: 'template', key: 'legacy.example', version: '1', content: { name: 'Readable workflow' } }] },
    { ...workflow, workflow_key: 'missing.example', status: 'source_unavailable', diagnostic: 'Restore the disabled plugin, then check again.', available_sources: [] },
    { ...workflow, workflow_key: 'old.example', status: 'unknown', available_sources: [],
      successful: { verification: { sources: {} } } },
  ] } })
  expect(wrapper!.text()).toContain('Readable workflow')
  expect(wrapper!.text()).toContain('Not verified')
  expect(wrapper!.text()).toContain('Source unavailable')
  expect(wrapper!.text()).toContain('Restore the disabled plugin, then check again.')
  expect(wrapper!.get('summary').text()).toBe('Version details')
})
