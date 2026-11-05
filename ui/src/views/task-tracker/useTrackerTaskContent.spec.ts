import { mount } from '@vue/test-utils'
import { defineComponent, nextTick, ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/lib/client'
import type { TrackerTask } from '@/lib/task-tracker/types'
import { useTrackerTaskContent } from './useTrackerTaskContent'

const callOperation = vi.hoisted(() => vi.fn())
vi.mock('@/lib/operations', () => ({ callOperation }))

const task = {
  id: 1, project_id: 7, key: 'task-a', title: 'Read-only title', goal: 'Read-only goal',
  description: 'Original description', updated_at: 'revision-1', status: 'in-progress',
} as TrackerTask

function setup() {
  callOperation.mockReset().mockResolvedValue({ data: { task: { ...task, ticket_summary: null }, replayed: false } })
  const current = ref<TrackerTask | null>({ ...task })
  const refresh = vi.fn().mockResolvedValue({ ...task, description: 'Current saved description', updated_at: 'revision-2' })
  let content!: ReturnType<typeof useTrackerTaskContent>
  const wrapper = mount(defineComponent({
    setup() { content = useTrackerTaskContent({ task: current, refresh }); return () => null },
  }))
  return { wrapper, content, current, refresh }
}

afterEach(() => vi.restoreAllMocks())

describe('task content persistence', () => {
  it('saves only changed description and input, supports clearing, and reads current state after the receipt', async () => {
    const { wrapper, content, refresh } = setup()
    content.beginEdit()
    expect(content.descriptionChanged.value).toBe(false)
    content.description.value = ''
    expect(content.descriptionChanged.value).toBe(true)
    content.additionalInput.value = 'Please include keyboard support.'
    await content.save()
    expect(callOperation).toHaveBeenCalledWith('tracker.updateTaskContent', {
      project_id: 7, task_key: 'task-a', expected_updated_at: 'revision-1',
      idempotency_key: expect.any(String), description: '',
      additional_input: 'Please include keyboard support.', response_mode: 'raw',
    })
    expect(refresh).toHaveBeenCalledWith('task-a')
    expect(content.description.value).toBe('Current saved description')
    expect(content.additionalInput.value).toBe('')
    expect(content.dirty.value).toBe(false)
    expect(content.descriptionChanged.value).toBe(false)
    expect(content.notice.value).toBe('Changes saved.')
    wrapper.unmount()
  })

  it('retries an uncertain submission with identical arguments and uses a new key for a new intent', async () => {
    const { wrapper, content } = setup()
    callOperation.mockRejectedValueOnce(new TypeError('Network disconnected'))
    content.additionalInput.value = 'Keep this input exactly once.'
    await content.save()
    const original = callOperation.mock.calls[0]![1]
    expect(content.additionalInput.value).toBe('Keep this input exactly once.')
    expect(content.locked.value).toBe(true)
    expect(content.error.value).toContain('could not be confirmed')
    await content.save()
    expect(callOperation.mock.calls[1]![1]).toEqual(original)
    content.additionalInput.value = 'A different input.'
    await content.save()
    expect(callOperation.mock.calls[2]![1].idempotency_key).not.toBe(original.idempotency_key)
    expect(callOperation.mock.calls[2]![1].expected_updated_at).toBe('revision-2')
    wrapper.unmount()
  })

  it('retains drafts on conflicts, requires a current read, and only saves against the reviewed revision', async () => {
    const { wrapper, content, refresh } = setup()
    callOperation.mockRejectedValueOnce(new ApiError('stale', 409, { detail: 'stale' }))
    content.beginEdit()
    content.description.value = 'My draft'
    content.additionalInput.value = 'My input'
    await content.save()
    const rejected = callOperation.mock.calls[0]![1]
    expect(content.conflict.value).toBe(true)
    expect(content.canSave.value).toBe(false)
    expect(refresh).not.toHaveBeenCalled()
    await content.save()
    expect(callOperation).toHaveBeenCalledTimes(1)
    await content.refreshLatest()
    expect(content.description.value).toBe('My draft')
    expect(content.additionalInput.value).toBe('My input')
    expect(content.latestDescription.value).toBe('Current saved description')
    expect(content.canSave.value).toBe(true)
    await content.save()
    expect(callOperation.mock.calls[1]![1]).toMatchObject({ expected_updated_at: 'revision-2', description: 'My draft', additional_input: 'My input' })
    expect(callOperation.mock.calls[1]![1].idempotency_key).not.toBe(rejected.idempotency_key)
    wrapper.unmount()
  })

  it('does not duplicate a saved input if current readback fails or the receipt is a replay', async () => {
    const { wrapper, content, refresh } = setup()
    refresh.mockRejectedValueOnce(new Error('read failed'))
    content.additionalInput.value = 'Saved but response unavailable'
    await content.save()
    expect(content.error.value).toContain('Changes were saved')
    expect(content.additionalInput.value).toBe('Saved but response unavailable')
    callOperation.mockResolvedValueOnce({ data: { task, replayed: true } })
    await content.save()
    expect(callOperation.mock.calls[1]![1]).toEqual(callOperation.mock.calls[0]![1])
    expect(refresh).toHaveBeenCalledTimes(2)
    expect(content.description.value).toBe('Current saved description')
    wrapper.unmount()
  })

  it('preserves the editing baseline during background refresh and blocks leaving while a save is in flight', async () => {
    const { wrapper, content, current } = setup()
    content.beginEdit()
    content.description.value = 'Unsaved description'
    current.value = { ...task, description: 'Changed elsewhere', updated_at: 'revision-other' }
    content.syncTask(current.value)
    await nextTick()
    expect(content.description.value).toBe('Unsaved description')
    let resolve!: () => void
    callOperation.mockReturnValueOnce(new Promise<void>(done => { resolve = done }))
    const request = content.save()
    expect(callOperation.mock.calls[0]![1].expected_updated_at).toBe('revision-1')
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    expect(content.confirmLeave()).toBe(false)
    expect(confirm).not.toHaveBeenCalled()
    expect(content.error.value).toContain('Please wait')
    const unload = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(unload)
    expect(unload.defaultPrevented).toBe(true)
    resolve()
    await request
    expect(content.confirmLeave()).toBe(true)
    wrapper.unmount()
  })

  it('keeps drafts through denied navigation and validation errors, and explicitly discards only on confirmation', async () => {
    const { wrapper, content } = setup()
    content.additionalInput.value = 'Draft to keep'
    callOperation.mockRejectedValueOnce(new ApiError('invalid', 422, { detail: 'invalid' }))
    await content.save()
    expect(content.additionalInput.value).toBe('Draft to keep')
    expect(content.locked.value).toBe(false)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    expect(content.confirmLeave()).toBe(false)
    expect(content.additionalInput.value).toBe('Draft to keep')
    confirm.mockReturnValue(true)
    expect(content.confirmLeave()).toBe(true)
    expect(content.additionalInput.value).toBe('')
    wrapper.unmount()
    const unload = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(unload)
    expect(unload.defaultPrevented).toBe(false)
  })
})
