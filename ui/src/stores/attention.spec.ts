import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiFetch } from '@/lib/client'
import { callOperation } from '@/lib/operations'
import { listAgentRequests } from '@/lib/stackos/agentRequest'

import { useAttentionStore } from './attention'

vi.mock('@/lib/client', () => ({ apiFetch: vi.fn() }))
vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
vi.mock('@/lib/stackos/agentRequest', () => ({ listAgentRequests: vi.fn() }))

const mockedApiFetch = vi.mocked(apiFetch)
const mockedCallOperation = vi.mocked(callOperation)
const mockedListAgentRequests = vi.mocked(listAgentRequests)

describe('attention supervision projection', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.resetAllMocks()
  })

  it('merges human decisions and repair signals without creating execution controls', async () => {
    mockedListAgentRequests.mockResolvedValue({
      items: [
        {
          id: 8,
          title: 'Choose a source policy',
          body_preview: 'The agent needs a human policy decision.',
          created_at: '2026-07-10T10:00:00Z',
        },
      ],
      next_cursor: null,
      total_estimate: 1,
    } as never)
    mockedCallOperation.mockResolvedValue({ blocked_ticket_count: 1 })
    mockedApiFetch.mockImplementation(async (url) => {
      if (url.includes('/runs?status=failed')) {
        return {
          items: [
            {
              id: 12,
              last_step: 'Validate sources',
              error: 'Source unavailable',
              ended_at: '2026-07-10T11:00:00Z',
              started_at: '2026-07-10T10:30:00Z',
            },
          ],
        }
      }
      if (url.endsWith('/auth/status')) {
        return {
          connections: [
            {
              credential_ref: 'cred_slack',
              provider_key: 'slack',
              label: 'Slack',
              status: 'expired',
              revoked_at: null,
              last_tested_at: '2026-07-10T09:00:00Z',
            },
          ],
        }
      }
      if (url.endsWith('/budgets')) return []
      if (url.includes('/cost?month=')) return { by_integration: {} }
      throw new Error(`unexpected URL ${url}`)
    })

    const store = useAttentionStore()
    await store.refresh(9)

    expect(store.items.map((item) => item.kind)).toEqual([
      'failed-run',
      'connection',
      'blocked',
      'question',
    ])
    expect(store.items.every((item) => item.to.startsWith('/projects/9/'))).toBe(true)
    expect(store.items.map((item) => item.cta)).toEqual([
      'Inspect failed run',
      'Repair connection',
      'Review blocked work',
      'Open request',
    ])
    expect(store.items.every((item) => item.impact && item.ownership && item.after)).toBe(true)
    expect(store.items.find((item) => item.kind === 'question')?.to).toBe(
      '/projects/9/agent-requests?request=8&attention_status=unread',
    )
    expect(store.items.find((item) => item.kind === 'connection')?.to).toContain(
      'connections?section=services&provider_key=slack',
    )
  })

  it('degrades one failed source without hiding the remaining attention state', async () => {
    mockedListAgentRequests.mockRejectedValue(new Error('requests unavailable'))
    mockedCallOperation.mockResolvedValue({ blocked_ticket_count: 2 })
    mockedApiFetch.mockImplementation(async (url) => {
      if (url.includes('/runs?status=failed')) return { items: [] }
      if (url.endsWith('/auth/status')) return { connections: [] }
      if (url.endsWith('/budgets')) return []
      if (url.includes('/cost?month=')) return { by_integration: {} }
      throw new Error(`unexpected URL ${url}`)
    })

    const store = useAttentionStore()
    await store.refresh(9)

    expect(store.degraded).toBe(true)
    expect(store.items).toHaveLength(1)
    expect(store.items[0]).toMatchObject({ kind: 'blocked', title: '2 tasks are blocked' })
  })
})
