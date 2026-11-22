import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { UiFormField, UiSelect, UiTextarea } from '@/components/ui'
import { callOperation } from '@/lib/operations'
import ProjectRoleSettings from './ProjectRoleSettings.vue'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
let wrapper: VueWrapper | undefined
afterEach(() => { wrapper?.unmount(); vi.resetAllMocks() })
const source = { host_key: 'codex', native_name: 'builder', source_path: '.codex/agents/builder.toml' }
const delivery = { host_key: 'codex', agents: { delivery_agent: { source_ref: source, model: 'fallback' }, review_agent: { host_key: 'claude-code', name: 'reviewer', instructions: 'Review' } } }
const catalog = { items: [
  { host_key: 'codex', display_name: 'Codex', available: true, agent_execution: { runnable: 'available', sandboxes: ['workspace-write'], models: ['gpt-6.1-sol'], reasoning_efforts: {} } },
  { host_key: 'claude-code', display_name: 'Claude Code', available: true, agent_execution: { runnable: 'available', sandboxes: ['workspace-write'], models: ['claude-fable-5-2'], reasoning_efforts: {} } },
  { host_key: 'claude-desktop', display_name: 'Claude Desktop', available: true, agent_execution: { runnable: 'unsupported', sandboxes: [], models: [] } },
  { host_key: 'hermes', display_name: 'Hermes', available: false, agent_execution: { runnable: 'unavailable', sandboxes: ['workspace-write'], models: [] } },
] }
describe('Agents role form', () => {
  it('shows effective source precedence, supported apps and non-Codex inline contracts in the existing form', async () => {
    vi.mocked(callOperation).mockImplementation(async (name, args) => {
      if (name === 'tracker.get') return { tracker: { project_id: 1, rev: 3, config_json: { delivery } } }
      if (name === 'hostMcp.status') return catalog
      const selected = (args.role_patch as { role: string }).role
      return { project_id: 1, data: { role_settings: { role: selected, project_dir: '/fixture', effective: { host_key: 'codex', model: 'source-model', native_settings: { model_reasoning_effort: 'high' }, setting_sources: { model: 'source', model_reasoning_effort: 'source' } } } } }
    })
    wrapper = mount(ProjectRoleSettings, { props: { projectId: 1, projectDir: '/fixture', sources: [] }, global: { stubs: { teleport: true } } })
    expect(callOperation).not.toHaveBeenCalled()
    await wrapper.get('button').trigger('click'); await flushPromises()
    expect(wrapper.text()).toContain('source-model (Codex source)')
    expect(wrapper.text()).toContain('An explicit Codex source model takes precedence')
    const field = (label: string) => wrapper!.findAllComponents(UiFormField).find(item => item.props('label') === label)!
    const apps = field('Agent app').getComponent(UiSelect).props('options')
    expect(apps).toContainEqual(expect.objectContaining({ label: 'Claude Desktop · MCP only', disabled: true }))
    expect(apps).toContainEqual(expect.objectContaining({ label: 'Hermes · CLI unavailable', disabled: true }))
    field('Role').getComponent(UiSelect).vm.$emit('update:modelValue', 'review_agent'); await flushPromises()
    expect(field('Role instructions').getComponent(UiTextarea).props('modelValue')).toBe('Review')
    expect(wrapper.text()).toContain('This host uses its supported inline contract')
    expect(wrapper.text()).not.toContain('Codex role source')
    field('Role instructions').getComponent(UiTextarea).vm.$emit('update:modelValue', 'Review changed'); await flushPromises()
    vi.mocked(callOperation).mockRejectedValueOnce(new Error('Save failed'))
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(wrapper.text()).toContain('Save failed')
    expect(field('Role instructions').getComponent(UiTextarea).props('modelValue')).toBe('Review changed')
    expect(wrapper.text()).not.toContain('Saved for fresh invocations')
  })
})
