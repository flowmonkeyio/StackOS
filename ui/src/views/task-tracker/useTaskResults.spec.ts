// @vitest-environment node
import { effectScope, ref } from 'vue'
import { flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useTaskResults } from './useTaskResults'

const call = vi.hoisted(() => vi.fn())
vi.mock('@/lib/operations', () => ({ callOperation: call }))
const page = (items: unknown[], next_cursor: string | null = null) => ({ project_id: 1, task_key: 'alpha', items, next_cursor, warnings: [], coverage: 'Explicit links' })
describe('task results reader', () => {
  beforeEach(() => call.mockReset())
  it('keeps same-title sources distinct, deduplicates overlapping pages and retains coverage warnings', async () => {
    const record = { source_kind: 'resource_record', source_id: 1, data: { title: 'Review' } }
    const artifact = { source_kind: 'artifact', source_id: 1, data: { name: 'Review' } }
    call.mockResolvedValueOnce(page([record], 'next'))
    const scope = effectScope()
    const reader = scope.run(() => useTaskResults({ projectId: ref(1), taskKey: ref('alpha') }))!
    await reader.refresh()
    await flushPromises()
    call.mockResolvedValueOnce({ ...page([record, artifact]), warnings: ['Missing linked source'] })
    await reader.more()
    expect(reader.items.value).toEqual([record, artifact])
    expect(reader.warnings.value).toEqual(['Missing linked source'])
    expect(reader.coverage.value).toBe('Explicit links')
    scope.stop()
  })
  it('rejects mismatched source scope and can retry without presenting it as an empty success', async () => {
    call.mockResolvedValueOnce({ ...page([]), task_key: 'other' })
    const scope = effectScope()
    const reader = scope.run(() => useTaskResults({ projectId: ref(1), taskKey: ref('alpha') }))!
    await reader.refresh()
    await flushPromises()
    expect(reader.error.value).toContain('scope')
    expect(reader.loaded.value).toBe(false)
    call.mockResolvedValueOnce(page([]))
    await reader.refresh()
    expect(reader.loaded.value).toBe(true)
    expect(reader.error.value).toBeNull()
    scope.stop()
  })
})
