import { DOMWrapper, flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/lib/client'

import type { TrackerTask, TrackerTicket } from '@/lib/task-tracker/types'

import TrackerTaskDetailDialog from './TrackerTaskDetailDialog.vue'

const callOperation = vi.hoisted(() => vi.fn())
vi.mock('@/lib/operations', () => ({ callOperation }))

const popup = () => new DOMWrapper(document.body)

describe('TrackerTaskDetailDialog', () => {
  beforeEach(() => callOperation.mockReset().mockResolvedValue({ data: {} }))
  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('keeps description-save feedback and focus in the footer even below a long input history', async () => {
    const record = { ...task(), context_json: { operator_inputs: Array.from({ length: 6 }, (_, index) => ({
      id: `input-${index}`, text: 'Long history '.repeat(80), created_at: '2026-10-28T00:00:00Z', actor: 'operator',
    })) } }
    const wrapper = mount(TrackerTaskDetailDialog, {
      props: { modelValue: true, task: record, refreshContent: vi.fn().mockResolvedValue({ ...record, description: 'Updated description' }) },
      attachTo: document.body,
    })
    const frame = popup().get('[role="dialog"]').element
    const body = popup().get('.ui-dialog__body').element as HTMLElement
    await popup().get('[data-edit-content]').trigger('click')
    await popup().get('[data-description-editor]').setValue('Updated description')
    const save = popup().findAll('.ui-dialog__footer button').find(button => button.text().startsWith('Save'))!
    ;(save.element as HTMLElement).focus()
    await save.trigger('click')
    await flushPromises()
    const footer = popup().get('.ui-dialog__footer')
    expect(footer.text()).toContain('Changes saved.')
    expect(footer.element.contains(document.activeElement)).toBe(true)
    expect(popup().get('[role="dialog"]').element).toBe(frame)
    expect(body.scrollTop).toBe(0)
    expect(callOperation.mock.calls[0]![1]).toMatchObject({ description: 'Updated description' })
    expect(callOperation.mock.calls[0]![1]).not.toHaveProperty('additional_input')
    wrapper.unmount()
  })

  it.each([
    { name: 'uncertain retry', cause: new TypeError('Disconnected'), message: 'could not be confirmed', action: 'Retry save' },
    { name: 'conflict refresh', cause: new ApiError('stale', 409, {}), message: 'This task changed', action: 'Load latest content' },
  ])('keeps $name feedback and repair action together in the footer', async ({ cause, message, action }) => {
    callOperation.mockRejectedValueOnce(cause)
    const record = task()
    const wrapper = mount(TrackerTaskDetailDialog, {
      props: { modelValue: true, task: record, refreshContent: vi.fn().mockResolvedValue({ ...record, updated_at: 'new-revision' }) },
      attachTo: document.body,
    })
    await popup().get('textarea').setValue('Retain this input')
    await popup().findAll('.ui-dialog__footer button').find(button => button.text() === 'Add input')!.trigger('click')
    await flushPromises()
    const footer = popup().get('.ui-dialog__footer')
    expect(footer.text()).toContain(message)
    expect(footer.element.contains(document.activeElement)).toBe(true)
    expect(popup().get('textarea').element.value).toBe('Retain this input')
    await footer.findAll('button').find(button => button.text() === action)!.trigger('click')
    await flushPromises()
    expect(footer.text()).toContain(action === 'Retry save' ? 'Changes saved.' : 'Latest content loaded.')
    expect(footer.element.contains(document.activeElement)).toBe(true)
    if (action === 'Retry save') expect(callOperation.mock.calls[1]![1]).toEqual(callOperation.mock.calls[0]![1])
    wrapper.unmount()
  })

  it('labels the precise atomic intent and keeps focus in ticket inspection when a save finishes', async () => {
    let resolve!: () => void
    callOperation.mockReturnValueOnce(new Promise<void>(done => { resolve = done }))
    const record = task()
    const wrapper = mount(TrackerTaskDetailDialog, {
      props: { modelValue: true, task: record, refreshContent: vi.fn().mockResolvedValue(record) },
      attachTo: document.body,
    })
    await popup().get('[data-edit-content]').trigger('click')
    expect(popup().get<HTMLTextAreaElement>('[data-description-editor]').element.value).toBe(record.description)
    expect(popup().get('.ui-dialog__footer').text()).toContain('Add input')
    await popup().get('[data-description-editor]').setValue('Changed description')
    expect(popup().get('.ui-dialog__footer').text()).toContain('Save description')
    const input = popup().findAll('textarea')[1]!
    await input.setValue('Extra context')
    const save = popup().findAll('.ui-dialog__footer button').find(button => button.text() === 'Save description and input')!
    await save.trigger('click')
    expect(popup().get('.ui-dialog__footer').text()).toContain('Saving…')
    expect(document.activeElement).toBe(popup().get('[data-content-feedback]').element)
    expect(callOperation.mock.calls[0]![1]).toMatchObject({ description: 'Changed description', additional_input: 'Extra context' })
    await wrapper.setProps({ ticketKey: 'ticket-a' })
    await flushPromises()
    const heading = popup().get('h2').element
    expect(document.activeElement).toBe(heading)
    resolve()
    await flushPromises()
    expect(popup().get('.ui-dialog__footer').text()).toContain('Changes saved.')
    expect(document.activeElement).toBe(heading)
    wrapper.unmount()
  })

  it.each([0, 1, 6, 7, 30])('keeps all %i ticket records reachable in a six-row scrolling region', async (count) => {
    const tickets = Array.from({ length: count }, (_, index) => ({
      id: index + 1, key: `ticket-${index + 1}`, title: `Ticket ${index + 1}`,
      task_key: task().key, status: 'pending', blocked_by: [], blocker_reason: null,
    } as unknown as TrackerTicket))
    const wrapper = mount(TrackerTaskDetailDialog, {
      props: { modelValue: true, task: task(), tickets },
    })
    const list = popup().find('[aria-label="Task tickets"]')
    if (!count) {
      expect(list.exists()).toBe(false)
      expect(popup().text()).toContain('No tickets yet.')
    } else {
      expect(list.classes()).toEqual(expect.arrayContaining(['max-h-60', 'overflow-y-auto']))
      expect(list.attributes('tabindex')).toBe('0')
      const rows = list.findAll('button')
      expect(rows).toHaveLength(count)
      expect(rows.every(row => row.classes().includes('h-10'))).toBe(true)
      await rows.at(-1)!.trigger('click')
      expect(wrapper.emitted('openTicket')?.[0]).toEqual([tickets.at(-1)])
    }
    wrapper.unmount()
  })

  it('keeps the detail frame through loading, failure and loaded content and focuses editor / Cancel', async () => {
    const record = task()
    const wrapper = mount(TrackerTaskDetailDialog, {
      props: { modelValue: true, task: null, loading: true, refreshContent: vi.fn().mockResolvedValue(record) },
      attachTo: document.body,
    })
    const frame = popup().get('[role="dialog"]').element
    expect(popup().text()).toContain('Loading task details')
    await wrapper.setProps({ loading: false, error: 'Try again' })
    expect(popup().get('[role="dialog"]').element).toBe(frame)
    expect(popup().text()).toContain('Try again')
    await wrapper.setProps({ task: record, error: null })
    wrapper.vm.syncTask(record)
    expect(popup().get('[role="dialog"]').element).toBe(frame)
    await popup().get('[data-edit-content]').trigger('click')
    expect(document.activeElement).toBe(popup().get('[data-description-editor]').element)
    expect(popup().get('[data-description-editor]').attributes('style')).toContain('resize: none')
    await popup().findAll('button').find(button => button.text() === 'Cancel edit')!.trigger('click')
    expect(document.activeElement).toBe(popup().get('[data-edit-content]').element)
    expect(popup().get('[role="dialog"]').element).toBe(frame)
    wrapper.unmount()
  })

  it('shows when task contexts and files are truncated previews', async () => {
    const wrapper = mount(TrackerTaskDetailDialog, {
      props: {
        modelValue: true,
        task: task(),
        contexts: [
          {
            id: 1,
            context_ref: 'ctx_reporting',
            name: 'Reporting context',
            plugin_slug: 'provider',
            provider_key: 'provider',
            status: 'active',
            artifact_count: 12,
          },
        ],
        contextPageInfo: {
          limit: 20,
          nextCursor: 20,
          totalEstimate: 24,
        },
        contextArtifacts: {
          ctx_reporting: [
            {
              id: 1,
              context_ref: 'ctx_reporting',
              artifact_id: 7,
              action_call_id: 12,
              semantic_name: 'current-results.json',
              action_ref: 'provider.reporting.list',
              metadata_json: {
                absolute_path: '/tmp/current-results.json',
                bytes: 4096,
                content_type: 'application/json',
              },
              created_at: '2026-06-06T00:00:00Z',
              artifact: {
                id: 7,
                name: 'current-results.json',
                uri: '/tmp/current-results.json',
              },
            },
          ],
        },
        contextArtifactPageInfo: {
          ctx_reporting: {
            limit: 5,
            nextCursor: 5,
            totalEstimate: 12,
          },
        },
      },
      attachTo: document.body,
    })

    await nextTick()

    expect(document.body.textContent).toContain('Showing first 1 of 24 contexts.')
    expect(document.body.textContent).toContain('Reporting context')
    expect(document.body.textContent).toContain('current-results.json')
    expect(document.body.textContent).toContain('Showing first 1 of 12 files.')

    wrapper.unmount()
  })

  it('renders agent-owned task status as read-only context', async () => {
    const wrapper = mount(TrackerTaskDetailDialog, {
      props: {
        modelValue: true,
        task: task(),
      },
      attachTo: document.body,
    })

    await nextTick()

    expect(document.body.textContent).toContain(
      'Status is managed by the originating agent or run-plan lifecycle.',
    )
    expect(popup().find('[aria-label="Task status"]').exists()).toBe(false)

    wrapper.unmount()
  })

  it('offers only description and additional input fields while preserving read-only goal, title and status', async () => {
    const record = { ...task(), goal: 'Keep this goal read-only.', description: 'Editable description.' }
    const wrapper = mount(TrackerTaskDetailDialog, {
      props: { modelValue: true, task: record, refreshContent: vi.fn().mockResolvedValue(record) },
      attachTo: document.body,
    })
    const edit = popup().findAll('button').find(button => button.text() === 'Edit description')!
    await edit.trigger('click')
    const fields = popup().findAll('textarea')
    expect(fields).toHaveLength(2)
    expect(fields[0]!.element.value).toBe('Editable description.')
    expect(popup().text()).toContain('Keep this goal read-only.')
    expect(popup().get('h2').text()).toBe(record.title)
    expect(popup().findAll('input,select')).toHaveLength(0)
    expect(popup().find('[aria-label="Task status"]').exists()).toBe(false)
    wrapper.unmount()
  })
})

function task(): TrackerTask {
  return {
    id: 76,
    project_id: 1,
    tracker_id: 1,
    key: 'workflow-39',
    title: 'Platform UI drawers',
    goal: 'Polish task drawer details.',
    description: 'Polish task drawer details.',
    status: 'in-progress',
    priority_key: 'p1',
    lane_key: 'implementation',
    owner: 'codex',
    task_type: 'workflow',
    order_index: 1,
    source_kind: 'workflow',
    source_json: { run_plan_id: 39 },
    definition_of_done_json: ['Drawer renders context previews truthfully.'],
    constraints_json: [],
    expected_outcomes_json: [],
    completion_evidence_json: null,
    context_json: null,
    metadata_json: null,
    created_by: 'codex',
    created_at: '2026-06-06T00:00:00Z',
    updated_at: '2026-06-06T00:00:00Z',
    started_at: '2026-06-06T00:00:00Z',
    completed_at: null,
  }
}
