import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { callOperation } from '@/lib/operations'
import type { DesktopAgentExecutionCapabilities } from '@/lib/desktop'
import type { DeliveryDefaults, TrackerSummary } from '@/lib/task-tracker/types'
import AgentDefaultsForm from './AgentDefaultsForm.vue'
import AgentDefaultsTab from '../AgentDefaultsTab.vue'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
const call = vi.mocked(callOperation)
const capability: DesktopAgentExecutionCapabilities = {
  sandboxes: ['workspace-write'],
  review_sandbox: 'workspace-write',
  models: ['catalog-model'],
  custom_models: true,
  provider_selection: true,
  profile_selection: true,
  requires_provider_model: false,
  notes: 'Catalog fixture notes.',
  sources: [],
  checked_on: '2026-11-06',
  credential_status: 'not_checked',
  model_access: 'not_checked',
}
const initial: DeliveryDefaults = {
  host_key: 'catalog-host',
  future_setting: { retained: true },
  delivery_agent: {
    name: 'Builder',
    instructions: `Full packet\n${'long instructions '.repeat(900)}\nEnd`,
    model: 'saved-custom',
    provider: 'existing-provider',
    profile: 'existing-profile',
    future_role_field: 42,
  },
  review_agent: {
    name: 'Reviewer',
    instructions: 'Independent review\nSecond line',
    model: 'catalog-model',
  },
}
let defaults: DeliveryDefaults
let wrapper: VueWrapper | undefined

function tracker(id = 1, delivery = defaults): TrackerSummary {
  return {
    id,
    project_id: id,
    key: 'work',
    name: 'Work',
    description: '',
    rev: 17,
    config_json: { delivery, unrelated: { retained: true } },
    created_at: '',
    updated_at: '',
  }
}

beforeEach(() => {
  defaults = structuredClone(initial)
  call.mockReset()
  call.mockImplementation(async (name, args) => {
    if (name === 'hostMcp.status')
      return {
        items: [
          {
            host_key: 'catalog-host',
            display_name: 'Catalog Harness',
            status_label: 'Installed',
            agent_execution: capability,
          },
          {
            host_key: 'explicit-host',
            display_name: 'Explicit Harness',
            agent_execution: {
              ...capability,
              custom_models: false,
              requires_provider_model: true,
              profile_selection: false,
            },
          },
          {
            host_key: 'plain-host',
            display_name: 'Plain Harness',
            agent_execution: {
              ...capability,
              custom_models: false,
              provider_selection: false,
              profile_selection: false,
            },
          },
        ],
      }
    if (name === 'tracker.configureDelivery') {
      defaults = structuredClone(args.delivery as DeliveryDefaults)
      return { data: tracker(Number(args.project_id)), project_id: args.project_id }
    }
    return { tracker: tracker(Number(args.project_id)) }
  })
})
afterEach(() => wrapper?.unmount())

async function setup(id = 1) {
  wrapper = mount(AgentDefaultsForm, { props: { projectId: id } })
  await flushPromises()
  return wrapper
}

async function setupProjectTab(id = 1) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/projects/:id/agent-defaults', component: AgentDefaultsTab }],
  })
  await router.push(`/projects/${id}/agent-defaults`)
  await router.isReady()
  wrapper = mount(AgentDefaultsTab, { global: { plugins: [router] } })
  await flushPromises()
  return { w: wrapper, router }
}
async function choose(w: VueWrapper, selector: string, label: string) {
  await w.get(selector).trigger('click')
  const option = w.findAll('[role="option"]').find((item) => item.text() === label)
  expect(option, `option ${label}`).toBeDefined()
  await option!.trigger('click')
}
function saves() {
  return call.mock.calls.filter(([name]) => name === 'tracker.configureDelivery')
}

describe('Project agent defaults', () => {
  it('loads full instructions and changes only the edited field in the complete saved object', async () => {
    const w = await setup()
    expect((w.get('#delivery_agent-instructions').element as HTMLTextAreaElement).value).toBe(
      initial.delivery_agent!.instructions,
    )
    expect(w.get('#delivery_agent-model').text()).toContain('saved-custom')
    await w.get('#delivery_agent-name').setValue('Changed builder')
    await w.get('form').trigger('submit')
    await flushPromises()
    const expected = structuredClone(initial)
    expected.delivery_agent!.name = 'Changed builder'
    expect(saves()).toEqual([['tracker.configureDelivery', { project_id: 1, delivery: expected }]])
    expect(w.text()).toContain('Saved and reloaded project defaults (revision 17)')
    expect(call.mock.calls.at(-1)).toEqual(['tracker.status', { project_id: 1 }])
    expect(
      call.mock.calls.every(([name]) =>
        ['hostMcp.status', 'tracker.status', 'tracker.configureDelivery'].includes(name),
      ),
    ).toBe(true)
    expect(w.find('[type="submit"]').attributes('disabled')).toBeDefined()
  })

  it('uses backend catalog/default/custom and supported provider/profile choices without replacing saved values', async () => {
    const w = await setup()
    expect(w.text()).toContain('do not prove authentication or model access')
    await choose(w, '#delivery_agent-model', 'Custom model…')
    await w.get('#delivery_agent-custom-model').setValue('new-custom')
    await w.get('#delivery_agent-provider').setValue('new-provider')
    await w.get('#delivery_agent-profile').setValue('new-profile')
    await choose(w, '#review_agent-model', 'Native default')
    await choose(w, '#delivery_agent-host', 'Explicit Harness')
    // Host selection must not silently discard saved model/provider/profile.
    expect((w.get('#delivery_agent-profile').element as HTMLInputElement).value).toBe('new-profile')
    expect(w.get('#delivery_agent-profile').attributes('disabled')).toBeUndefined()
    expect(w.text()).toContain('Explicit provider and model required')
    await w.get('#delivery_agent-model').trigger('click')
    expect(w.findAll('[role="option"]').map((item) => item.text())).toContain('catalog-model')
    // The shared owner also permits provider-specific models when a provider is supplied.
    expect(w.findAll('[role="option"]').map((item) => item.text())).toContain('Custom model…')
    expect(
      w
        .findAll('[role="option"]')
        .find((item) => item.text() === 'Explicit model required for execution')!
        .attributes('disabled'),
    ).toBeDefined()
    await w.get('form').trigger('submit')
    await flushPromises()
    expect(defaults.delivery_agent).toMatchObject({
      host_key: 'explicit-host',
      model: 'new-custom',
      provider: 'new-provider',
      profile: 'new-profile',
    })
    expect(defaults.review_agent!.model).toBeNull()
  })

  it.each([
    ['delivery_agent', 'profile', 'Explicit Harness'],
    ['review_agent', 'profile', 'Explicit Harness'],
    ['delivery_agent', 'provider', 'Plain Harness'],
    ['review_agent', 'provider', 'Plain Harness'],
  ] as const)(
    'explicitly clears retained incompatible %s %s after a harness switch',
    async (role, field, harness) => {
      defaults[role] = {
        ...defaults[role],
        provider: 'saved-provider',
        profile: field === 'profile' ? 'saved-profile' : null,
        future_role_field: { retained: true },
      }
      const expected = structuredClone(defaults)
      const w = await setup()
      await choose(w, `#${role}-host`, harness)
      await choose(w, `#${role}-model`, 'catalog-model')
      const input = w.get(`#${role}-${field}`)
      expect((input.element as HTMLInputElement).value).toBe(`saved-${field}`)
      expect(input.attributes('disabled')).toBeUndefined()
      expect(w.text()).toContain(`Clear the saved ${field}`)
      const clear = input.element.parentElement!.querySelector('button[aria-label="Clear input"]')!
      expect(clear).not.toBeNull()
      const clearButton = clear as HTMLButtonElement
      clearButton.click()
      await flushPromises()
      expect(w.find(`#${role}-${field}`).exists()).toBe(false)
      await w.get('form').trigger('submit')
      await flushPromises()
      expected[role] = {
        ...expected[role],
        host_key: field === 'profile' ? 'explicit-host' : 'plain-host',
        model: 'catalog-model',
        [field]: null,
      }
      expect(defaults).toEqual(expected)
      expect(saves()).toEqual([
        ['tracker.configureDelivery', { project_id: 1, delivery: expected }],
      ])
      expect(w.text()).toContain('Saved and reloaded project defaults')
    },
  )

  it('supports empty defaults and preserves unavailable saved harness/model selections', async () => {
    defaults = {}
    const w = await setup()
    expect(w.text()).toContain('No project agent defaults saved')
    await w.get('#delivery_agent-name').setValue('New builder')
    await w.get('#delivery_agent-instructions').setValue('New packet')
    await choose(w, '#delivery_agent-host', 'Catalog Harness')
    await w.get('form').trigger('submit')
    await flushPromises()
    expect(defaults).toEqual({
      delivery_agent: { name: 'New builder', instructions: 'New packet', host_key: 'catalog-host' },
    })
    defaults = {
      delivery_agent: { name: 'Legacy', host_key: 'unavailable', model: 'legacy-model' },
    }
    await w
      .findAll('button')
      .find((button) => button.text() === 'Reload defaults')!
      .trigger('click')
    await flushPromises()
    expect(w.get('#delivery_agent-host').text()).toContain(
      'unavailable (saved; unavailable in catalog)',
    )
    expect(w.get('#delivery_agent-model').text()).toContain('legacy-model')
  })

  it('shows load and save failures and retains the draft without false success', async () => {
    call.mockRejectedValueOnce(new Error('Load failed'))
    const w = await setup()
    expect(w.text()).toContain('Load failed')
    expect(w.find('form').exists()).toBe(false)
    await w.get('button').trigger('click')
    await flushPromises()
    await w.get('#review_agent-instructions').setValue('Unsaved review')
    call.mockRejectedValueOnce(new Error('Save failed'))
    await w.get('form').trigger('submit')
    await flushPromises()
    expect(w.text()).toContain('Save failed')
    expect((w.get('#review_agent-instructions').element as HTMLTextAreaElement).value).toBe(
      'Unsaved review',
    )
    expect(w.text()).not.toContain('Defaults saved')
  })

  it('fails visibly when readback differs from the returned saved settings', async () => {
    const w = await setup()
    await w.get('#delivery_agent-name').setValue('My edit')
    call.mockResolvedValueOnce({ data: tracker(), project_id: 1 })
    call.mockResolvedValueOnce({ tracker: tracker(1, {}) })
    await w.get('form').trigger('submit')
    await flushPromises()
    expect(w.text()).toContain('changed before readback')
    expect((w.get('#delivery_agent-name').element as HTMLInputElement).value).toBe('My edit')
    expect(w.text()).not.toContain('Defaults saved')
  })

  it('rejects mismatched scope and ignores an old save response after project switching', async () => {
    const { w, router } = await setupProjectTab()
    await choose(w, '#delivery_agent-model', 'Custom model…')
    await w.get('#delivery_agent-name').setValue('Project one edit')
    let resolve!: (result: unknown) => void
    call.mockImplementationOnce(
      () =>
        new Promise((done) => {
          resolve = done
        }),
    )
    await w.get('form').trigger('submit')
    defaults = { host_key: 'catalog-host', review_agent: { name: 'Project two reviewer' } }
    await router.push('/projects/2/agent-defaults')
    await flushPromises()
    resolve({ data: tracker(1), project_id: 1 })
    await flushPromises()
    expect((w.get('#review_agent-name').element as HTMLInputElement).value).toBe(
      'Project two reviewer',
    )
    expect(w.text()).not.toContain('Defaults saved')
    expect(w.find('#delivery_agent-custom-model').exists()).toBe(false)
    expect(saves()[0]![1].project_id).toBe(1)
    call.mockResolvedValueOnce({ tracker: tracker(1) })
    await w
      .findAll('button')
      .find((button) => button.text() === 'Reload defaults')!
      .trigger('click')
    await flushPromises()
    expect(w.text()).toContain('belongs to another project')
    expect(w.find('form').exists()).toBe(false)
  })

  it('ignores a late load from the previous project', async () => {
    let resolve!: (result: unknown) => void
    call.mockImplementationOnce(
      () =>
        new Promise((done) => {
          resolve = done
        }),
    )
    const { w, router } = await setupProjectTab()
    defaults = { delivery_agent: { name: 'Project two builder' } }
    await router.push('/projects/2/agent-defaults')
    await flushPromises()
    resolve({ tracker: tracker(1, initial) })
    await flushPromises()
    expect((w.get('#delivery_agent-name').element as HTMLInputElement).value).toBe(
      'Project two builder',
    )
    expect(w.text()).not.toContain('Defaults saved')
  })
})
