import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiFetch } from '@/lib/client'
import { callOperation } from '@/lib/operations'

import { useReadinessStore } from './readiness'

vi.mock('@/lib/client', () => ({ apiFetch: vi.fn() }))
vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))

const mockedApiFetch = vi.mocked(apiFetch)
const mockedCallOperation = vi.mocked(callOperation)

describe('readiness supervision projection', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.resetAllMocks()
  })

  it('summarizes project-scoped runtime, connection, and integration readiness', async () => {
    mockedApiFetch.mockImplementation(async (url) => {
      if (url === '/api/v1/health') {
        return { db_status: 'ok', scheduler_running: true, version: '1.2.3', daemon_uptime_s: 90 }
      }
      if (url === '/api/v1/projects/7/connections/accounts') {
        return {
          providers: [{ provider_key: 'slack' }],
          accounts: [{ status: 'connected', revoked_at: null }],
        }
      }
      throw new Error(`unexpected URL ${url}`)
    })
    mockedCallOperation.mockResolvedValue({ count: 2, connected_count: 1, hidden_action_count: 0 })

    const store = useReadinessStore()
    await store.refresh(7)

    expect(store.ready).toBe(true)
    expect(store.headline).toBe('Ready for connected agents')
    expect(store.checks.map((check) => check.key)).toEqual([
      'daemon',
      'automation',
      'connections',
      'actions',
    ])
    expect(
      store.checks
        .filter((check) => check.to)
        .every((check) => check.to?.startsWith('/projects/7/')),
    ).toBe(true)
  })

  it('keeps a failed local service probe explicit and blocking', async () => {
    mockedApiFetch.mockImplementation(async (url) => {
      if (url === '/api/v1/health') throw new Error('offline')
      if (url === '/api/v1/projects/7/connections/accounts') {
        return { providers: [], accounts: [] }
      }
      throw new Error(`unexpected URL ${url}`)
    })
    mockedCallOperation.mockResolvedValue({ count: 1, hidden_action_count: 0 })

    const store = useReadinessStore()
    await store.refresh(7)

    expect(store.ready).toBe(false)
    expect(store.headline).toBe('Not ready for agents')
    expect(store.blocker).toMatchObject({ key: 'daemon', state: 'blocked' })
  })

  it('keeps repair-required integrations out of the ready actions state', async () => {
    mockedApiFetch.mockImplementation(async (url) => {
      if (url === '/api/v1/health') {
        return { db_status: 'ok', scheduler_running: true }
      }
      if (url === '/api/v1/projects/7/connections/accounts') {
        return {
          providers: [{ provider_key: 'mock-provider' }],
          accounts: [{ status: 'repair-required', revoked_at: null }],
        }
      }
      throw new Error(`unexpected URL ${url}`)
    })
    mockedCallOperation.mockResolvedValue({
      count: 1,
      connected_count: 0,
      ready_count: 0,
      hidden_action_count: 0,
      items: [{ provider_key: 'mock-provider', state: 'repair_required' }],
    })

    const store = useReadinessStore()
    await store.refresh(7)

    expect(store.checks.find((check) => check.key === 'actions')).toMatchObject({
      state: 'attention',
      hint: '1 integration needs Account repair.',
      to: '/projects/7/plugins',
    })
  })

  it('can skip the expensive action inventory on the Home fast path', async () => {
    mockedApiFetch.mockImplementation(async (url) => {
      if (url === '/api/v1/health') {
        return { db_status: 'ok', scheduler_running: true }
      }
      throw new Error(`unexpected URL ${url}`)
    })
    const sharedAuth = Promise.resolve({
      project_id: 7,
      provider_key: null,
      providers: [],
      accounts: [],
    })

    const store = useReadinessStore()
    await store.refresh(7, { authStatus: sharedAuth, includeActions: false })

    expect(store.checks.map((check) => check.key)).toEqual(['daemon', 'automation', 'connections'])
    expect(mockedCallOperation).not.toHaveBeenCalled()
    expect(mockedApiFetch).toHaveBeenCalledTimes(1)
  })

  it('does not let previous-project checks or cache replace the new scope', async () => {
    const projectOne = deferred<void>()
    let healthRequest = 0
    mockedApiFetch.mockImplementation(async (url) => {
      if (url === '/api/v1/health') {
        healthRequest += 1
        if (healthRequest === 1) await projectOne.promise
        return {
          db_status: 'ok',
          scheduler_running: true,
          version: healthRequest === 1 ? 'project-one-waited' : 'project-two-current',
        }
      }
      if (url === '/api/v1/projects/1/connections/accounts') {
        await projectOne.promise
        return { providers: [], accounts: [] }
      }
      if (url === '/api/v1/projects/2/connections/accounts') {
        return {
          providers: [{ provider_key: 'slack' }],
          accounts: [{ status: 'connected', revoked_at: null }],
        }
      }
      throw new Error(`unexpected URL ${url}`)
    })
    mockedCallOperation.mockImplementation(async (_operation, args) => {
      if (args.project_id === 1) await projectOne.promise
      return { count: 1, connected_count: 1, hidden_action_count: 0 }
    })
    const store = useReadinessStore()

    const older = store.refresh(1)
    await store.refresh(2)
    projectOne.resolve()
    await older

    expect(store.projectId).toBe(2)
    expect(store.version).toBe('project-two-current')
    expect(
      store.checks
        .filter((check) => check.to)
        .every((check) => check.to?.startsWith('/projects/2/')),
    ).toBe(true)
  })
})

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  const promise = new Promise<T>((next) => {
    resolve = next
  })
  return { promise, resolve }
}
