import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'

import OperationsView from './OperationsView.vue'

const ORIG_FETCH = globalThis.fetch

describe('OperationsView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  afterEach(() => {
    globalThis.fetch = ORIG_FETCH
    document.body.innerHTML = ''
    vi.restoreAllMocks()
  })

  it('renders operation catalog and detail from the generic operation docs API', async () => {
    const requestedUrls: string[] = []
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      requestedUrls.push(url)
      if (url.includes('/api/v1/operations/action.describe')) {
        return json(operationDetail('action.describe', 'direct-read'))
      }
      if (url.includes('/api/v1/operations/runPlan.claimStep')) {
        return json(operationDetail('runPlan.claimStep', 'run-plan-controller'))
      }
      if (url.includes('/api/v1/operations')) {
        return json({
          items: [
            operationSummary('action.describe', 'direct-read'),
            operationSummary('runPlan.claimStep', 'run-plan-controller'),
          ],
        })
      }
      return json({})
    }) as typeof fetch

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/operations', component: OperationsView }],
    })
    await router.push('/projects/1/operations')
    await router.isReady()

    const wrapper = mount(
      { template: '<RouterView />' },
      {
        global: {
          plugins: [router],
          stubs: { teleport: true },
        },
      },
    )

    await vi.waitFor(() => expect(wrapper.text()).toContain('action.describe'))
    await clickRow(wrapper, 'action.describe')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Operation purpose: action.describe'))
    expect(wrapper.text()).toContain('Operation purpose: action.describe')
    expect(wrapper.text()).toContain('direct-read')

    await clickRow(wrapper, 'runPlan.claimStep')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Operation purpose: runPlan.claimStep'))
    expect(requestedUrls.some((url) => url.includes('/api/v1/operations/runPlan.claimStep'))).toBe(
      true,
    )

    await clickButton(wrapper, 'CLI')
    await vi.waitFor(() => expect(requestedUrls.some((url) => url.includes('surface=cli'))).toBe(true))
  })
})

function operationSummary(name: string, grantPolicy: string) {
  return {
    name,
    summary: `Operation summary: ${name}`,
    read_only: !name.includes('claimStep'),
    mutating: name.includes('claimStep'),
    surfaces: {
      mcp: { enabled: true },
      rest: { enabled: true, path: `/api/v1/operations/${name}/call` },
      cli: { enabled: true, command: name },
    },
    grant_policy: grantPolicy,
    secret_policy: 'no-secret-output',
  }
}

function operationDetail(name: string, grantPolicy: string) {
  return {
    ...operationSummary(name, grantPolicy),
    purpose: `Operation purpose: ${name}`,
    when_to_use: ['When the caller needs this operation.'],
    prerequisites: ['Pass safe references only.'],
    returns: ['Structured output.'],
    input_schema: { type: 'object', properties: { run_token: { type: 'string' } } },
    output_schema: { type: 'object', properties: { ok: { type: 'boolean' } } },
    examples: [{ title: 'Example', arguments: { operation: name } }],
  }
}

async function clickRow(wrapper: ReturnType<typeof mount>, label: string): Promise<void> {
  const row = wrapper
    .findAll('tr')
    .find((candidate) => candidate.text().includes(label))
  expect(row, `${label} row`).toBeDefined()
  await row?.trigger('click')
}

async function clickButton(wrapper: ReturnType<typeof mount>, label: string): Promise<void> {
  const button = wrapper
    .findAll('button')
    .find((candidate) => candidate.text().trim() === label)
  expect(button, `${label} button`).toBeDefined()
  await button?.trigger('click')
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  })
}
