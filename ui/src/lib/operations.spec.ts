// @vitest-environment node

import { afterEach, describe, expect, it, vi } from 'vitest'

import { callOperation } from './operations'

const ORIG_FETCH = globalThis.fetch

describe('callOperation', () => {
  afterEach(() => {
    globalThis.fetch = ORIG_FETCH
    vi.restoreAllMocks()
  })

  it('requests raw operation responses for browser UI calls by default', async () => {
    globalThis.fetch = vi.fn(async (_input, init) => {
      const body = JSON.parse(String(init?.body))
      expect(body.arguments).toEqual({ response_mode: 'raw', project_id: 1 })
      return json({ ok: true })
    }) as typeof fetch

    await callOperation('tracker.get', { project_id: 1 })
  })

  it('preserves an explicit response_mode from a caller', async () => {
    globalThis.fetch = vi.fn(async (_input, init) => {
      const body = JSON.parse(String(init?.body))
      expect(body.arguments).toEqual({ response_mode: 'compact', project_id: 1 })
      return json({ ok: true })
    }) as typeof fetch

    await callOperation('tracker.get', { project_id: 1, response_mode: 'compact' })
  })

  it('passes cancellation to the fetch boundary without serializing it into operation arguments', async () => {
    const controller = new AbortController()
    globalThis.fetch = vi.fn((_input, init) => new Promise((_resolve, reject) => {
      expect(init?.signal).toBe(controller.signal)
      expect(JSON.parse(String(init?.body)).arguments).toEqual({ response_mode: 'raw', project_id: 1 })
      init!.signal!.addEventListener('abort', () => reject(init!.signal!.reason), { once: true })
    })) as typeof fetch
    const request = callOperation('tracker.get', { project_id: 1 }, { signal: controller.signal })
    controller.abort()
    await expect(request).rejects.toMatchObject({ name: 'AbortError' })
    expect(globalThis.fetch).toHaveBeenCalledOnce()
  })
})

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  })
}
