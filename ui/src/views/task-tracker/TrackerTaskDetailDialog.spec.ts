import { DOMWrapper, flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/lib/client'
import { formatDateTime } from '@/lib/stackos/json'
import { agentStatsFixture } from '@/views/home/agentStatsFixtures'
import SourceDetailRenderer from '@/components/renderers/SourceDetailRenderer.vue'
import ArtifactRenderer from '@/components/renderers/ArtifactRenderer.vue'
import RunAbortAction from '@/components/domain/RunAbortAction.vue'
import { useRunAbort } from '@/composables/useRunAbort'
import type { SchemaTaskResultItem, SchemaTaskActivityItem } from '@/api'

import type { ActiveExecution, TrackerTask, TrackerTicket } from '@/lib/task-tracker/types'

import TrackerTaskDetailDialog from './TrackerTaskDetailDialog.vue'

const callOperation = vi.hoisted(() => vi.fn())
vi.mock('@/lib/operations', () => ({ callOperation }))

const popup = () => new DOMWrapper(document.body)

describe('TrackerTaskDetailDialog', () => {
  it('refreshes the shared task priority badge while leaving the selected ticket priority unchanged', async () => {
    const wrapper = mount(TrackerTaskDetailDialog, { props: { modelValue: true, task: { ...task(), priority_key: 'p0' }, tickets: [sourceTicket()] } })
    expect(popup().get('[aria-label="Priority P0"]').classes()).toContain('text-danger-fg')
    await wrapper.setProps({ task: { ...task(), priority_key: 'p1' } })
    expect(popup().get('[aria-label="Priority P1"]').classes()).toContain('text-warning-fg')
    expect(wrapper.props('tickets')![0]!.priority_key).toBe(sourceTicket().priority_key)
    wrapper.unmount()
  })
  it('keeps task and shared ticket elapsed distinct after completion without visible Started facts', async () => {
    const record = { ...task(), status: 'complete' as const, started_at: '2026-11-08T01:00:00', completed_at: '2026-11-09T05:00:00' }
    const source = { ...sourceTicket(), status: 'complete' as const, started_at: '2026-11-08T02:00:00', completed_at: '2026-11-08T04:22:00' }
    const wrapper = mount(TrackerTaskDetailDialog, { props: { modelValue: true, task: record, tickets: [source] } })
    expect(popup().get('.active-execution-line__elapsed').text()).toBe('1d 4h')
    expect(popup().findAll('dt').map(label => label.text())).not.toContain('Started')
    expect(popup().findAll('dt').map(label => label.text())).toContain('Completed')
    await wrapper.setProps({ ticketKey: source.key, ticket: source })
    await flushPromises()
    expect(popup().get('.tracker-task-detail + div .active-execution-line__elapsed').text()).toBe('2h 22m')
    expect(popup().findAll('dt').map(label => label.text())).not.toContain('Started')
    expect(popup().findAll('dt').map(label => label.text())).toContain('Claimed')
    wrapper.unmount()
  })
  beforeEach(() => callOperation.mockReset().mockResolvedValue({ data: {} }))
  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('opens its footer run menu above the trigger and preserves keyboard access to the selected run', async () => {
    const wrapper = mount(TrackerTaskDetailDialog, { props: { modelValue: true, task: task() }, attachTo: document.body })
    await flushPromises()
    expect(wrapper.getComponent(RunAbortAction).props('placement')).toBe('top-end')
    await popup().get('.ui-dialog__footer [aria-label^="Run actions for"]').trigger('click')
    await popup().get('[role="menu"]').trigger('keydown', { key: 'Enter' })
    expect(useRunAbort().open.value).toBe(true)
    expect(useRunAbort().selected.value).toMatchObject({ projectId: task().project_id, runPlanId: 39, title: task().title })
    expect(callOperation).not.toHaveBeenCalled()
    useRunAbort().close()
    wrapper.unmount()
  })

  it('opens exact result identities in shared Library details and returns to the retained draft, focus and reading position', async () => {
    const record = { source_kind: 'resource_record', source_id: 31, occurred_at: '2026-11-05T23:00:00Z', associations: [], data: {
      id: 31, project_id: 1, resource_id: 8, resource_key: 'evidence', plugin_slug: 'engineering',
      title: 'Browser review', data_json: { summary: 'Recorded outcome' },
      created_at: '2026-11-05T23:00:00Z', updated_at: '2026-11-05T23:00:00Z', external_id: null, provenance_json: null,
    } } as SchemaTaskResultItem
    const file = { source_kind: 'artifact', source_id: 32, data: {
      id: 32, project_id: 1, name: 'Summary', kind: 'document', plugin_slug: null,
      uri: 'https://example.invalid/?token=private-result', status: 'superseded',
    } } as SchemaTaskResultItem
    const results = { items: [record, file], nextCursor: 'next', loaded: true, loading: false, error: null, warnings: [], coverage: 'Explicit links only' }
    const wrapper = mount(TrackerTaskDetailDialog, {
      props: { modelValue: true, task: task(), results, refreshContent: vi.fn() }, attachTo: document.body,
    })
    await flushPromises()
    const shell = popup().get('[role="dialog"]').element
    await popup().get('[data-edit-content]').trigger('click')
    await popup().get('[data-description-editor]').setValue('Retained draft')
    const body = popup().get<HTMLElement>('.ui-dialog__body').element
    body.scrollTop = 200
    const opener = popup().get('[data-result-source="resource_record:31"]')
    ;(opener.element as HTMLElement).focus()
    await opener.trigger('click')
    await flushPromises()
    expect(popup().get('[role="dialog"]').element).toBe(shell)
    expect(wrapper.findComponent(SourceDetailRenderer).props('source')).toMatchObject({ source_kind: 'resource_record', source_id: 31, payload: record.data })
    expect(popup().get('[data-task-result-detail]').text()).toContain('Recorded outcome')
    expect(document.activeElement).toBe(popup().get('h2').element)
    expect(wrapper.emitted('inspectResult')?.[0]?.[0]).toMatchObject({ source_id: 31 })
    await popup().findAll('button').find(button => button.text() === 'Back to task')!.trigger('click')
    await flushPromises()
    expect(body.scrollTop).toBe(200)
    expect(document.activeElement).toBe(opener.element)
    expect(popup().get<HTMLTextAreaElement>('[data-description-editor]').element.value).toBe('Retained draft')
    await popup().get('[data-result-source="artifact:32"]').trigger('click')
    await flushPromises()
    expect(wrapper.findComponent(ArtifactRenderer).props('artifact')).toEqual(file.data)
    const details = popup().get('[data-task-result-detail]')
    expect(details.find('a').exists()).toBe(false)
    expect(details.text()).not.toContain('private-result')
    await wrapper.setProps({ task: { ...task(), key: 'other' } })
    expect(popup().find('[data-task-result-detail]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('shows actual history rows, source details and paging without inventing phases or navigating away', async () => {
    const first = { source_kind: 'tracker_revision', source_id: 90, occurred_at: '2026-11-05T23:00:00Z',
      kind: 'delivery.stopped', summary: 'Verification stopped', task_key: task().key, ticket_key: 'source-ticket', phase: null, run_id: null, run_plan_id: null,
      details: { reason: 'Required browser unavailable', api_key: 'private-history' },
    } as SchemaTaskActivityItem
    const activity = { items: [first], nextCursor: 'older', loaded: true, loading: false, error: null, warnings: [], coverage: 'Recorded sources; not a complete audit.' }
    const wrapper = mount(TrackerTaskDetailDialog, { props: { modelValue: true, task: task(), activity } })
    const history = popup().get('[aria-label="Task history"]')
    expect(history.text()).toContain('Verification stopped')
    expect(history.text()).toContain('tracker_revision #90')
    expect(history.text()).toContain('Required browser unavailable')
    expect(history.text()).not.toContain('private-history')
    await history.findAll('button').find(button => button.text() === 'View full history')!.trigger('click')
    expect(wrapper.emitted('moreHistory')).toHaveLength(1)
    await wrapper.setProps({ activity: { ...activity, items: [first, { ...first, source_kind: first.source_kind, source_id: 89, summary: 'Review completed' }], nextCursor: null } })
    expect(history.text()).toContain('Review completed')
    expect(history.findAll('button').some(button => button.text().includes('history'))).toBe(false)
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    expect(callOperation).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('explains the stopped ticket in task and ticket details without changing their lifecycle', async () => {
    const source = sourceTicket()
    const agent = { name: 'worker', instructions: 'Execute the assigned contract.' }
    const automatic = { ...task(), metadata_json: { delivery: {
      status: 'stopped', ticket_key: source.key, reason: 'Browser access denied.',
      job_ref: 'task-delivery:1:1', delivery_agent: agent, review_agent: agent,
    } } }
    const wrapper = mount(TrackerTaskDetailDialog, { props: { modelValue: true, task: automatic, tickets: [source] } })
    const stop = popup().get('.task-delivery-stop')
    expect(stop.text()).toContain('Delivery stopped')
    expect(stop.text()).toContain(source.title)
    expect(popup().get('.task-delivery-stop__reason').text()).toBe('Browser access denied.')
    expect(popup().text()).toContain('In Progress')
    await wrapper.setProps({ ticketKey: source.key, ticket: source })
    await flushPromises()
    const ticketStop = popup().get('.tracker-task-detail + div .task-delivery-stop')
    expect(ticketStop.text()).toContain('Browser access denied.')
    await wrapper.setProps({ task: { ...automatic, metadata_json: { delivery: { ...automatic.metadata_json.delivery, status: 'running' } } } })
    expect(popup().find('.task-delivery-stop').exists()).toBe(false)
    wrapper.unmount()
  })

  it('shows Automated beside status and updates when switching tasks or refreshing metadata', async () => {
    const agent = { name: 'worker', instructions: 'Execute the assigned contract.' }
    const automatic = { ...task(), metadata_json: { delivery: {
      status: 'stopped', job_ref: 'task-delivery:1:1', delivery_agent: agent, review_agent: agent,
    } } }
    const wrapper = mount(TrackerTaskDetailDialog, { props: { modelValue: true, task: automatic } })
    const badge = popup().findAll('.ui-badge').find(badge => badge.text() === 'Automated')!
    expect(badge.text()).toBe('Automated')
    expect(badge.classes()).toContain('text-info-fg')
    expect(badge.element.previousElementSibling?.textContent).toContain('Delivery stopped')
    expect(popup().find('[data-kind="tracker"][data-status="in-progress"]').exists()).toBe(true)
    await wrapper.setProps({ task: { ...task(), key: 'terminal-managed', owner: 'Codex' } })
    expect(popup().text()).not.toContain('Automated')
    await wrapper.setProps({ task: { ...automatic, status: 'complete', metadata_json: {
      delivery: { ...automatic.metadata_json.delivery, status: 'complete' },
    } } })
    expect(popup().findAll('.ui-badge').some(badge => badge.text() === 'Automated')).toBe(true)
    await wrapper.setProps({ task: { ...automatic, metadata_json: { delivery: [] } } })
    expect(popup().text()).not.toContain('Automated')
    await wrapper.setProps({ task: null })
    expect(popup().text()).not.toContain('Automated')
    wrapper.unmount()
  })

  it('uses the lifetime task projection while shared ticket content uses only the selected ticket', async () => {
    const totals = agentStatsFixture().totals
    const own = structuredClone(totals)
    own.reported_usage.input = 12
    own.reported_usage.remaining_input = 12
    own.reported_usage.cache_read = 0
    const execution: ActiveExecution = {
      invocation_id: 'active-call', agent: 'Delivery', host_key: 'codex', model: 'gpt-6.1-sol',
      ticket_key: sourceTicket().key, started_at: '2026-11-08T01:00:00Z', live_usage: null,
    }
    const source = { ...sourceTicket(), agent_stats: own, active_execution: execution }
    const agent = { name: 'worker', instructions: 'Execute the assigned contract.' }
    const automatic = { ...task(), agent_stats: totals, active_execution: execution, metadata_json: { delivery: { status: 'running', job_ref: 'synthetic:never-execute', delivery_agent: agent, review_agent: agent } } }
    const wrapper = mount(TrackerTaskDetailDialog, { props: { modelValue: true, task: automatic, tickets: [source] } })
    expect(popup().get('[aria-label="Task total usage"] [data-usage-field="input"] dd').text()).toBe('30')
    expect(popup().findAll('[data-usage-field="input"]')).toHaveLength(1)
    expect(popup().find('.active-execution-line').exists()).toBe(true)
    expect(popup().text()).not.toContain('Current call')
    await wrapper.setProps({ ticketKey: source.key, ticket: source })
    await flushPromises()
    const usage = popup().get('[aria-label="Ticket total usage"]')
    expect(usage.get('[data-usage-field="input"] dd').text()).toBe('12')
    expect(popup().text()).not.toContain('Current call')
    await wrapper.setProps({ task: { ...automatic, metadata_json: null } })
    expect(popup().find('[aria-label="Ticket total usage"]').exists()).toBe(false)
    await wrapper.setProps({ ticketKey: '', ticket: null })
    expect(popup().find('[aria-label="Task total usage"]').exists()).toBe(false)
    expect(usage.get('[data-usage-field="cache_read"] dd').text()).toBe('0')
    expect(usage.get('[data-usage-field="output"] dd').text()).toBe('30')
    expect(usage.text()).not.toContain('Partial')
    wrapper.unmount()
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

  it('shows multiple read-only followups with their origins alongside operator input', async () => {
    const record = taskWithFollowups()
    const source = sourceTicket()
    const wrapper = mount(TrackerTaskDetailDialog, {
      props: { modelValue: true, task: record, tickets: [source], refreshContent: vi.fn().mockResolvedValue(record) },
      attachTo: document.body,
    })

    const notes = popup().get('[aria-label="Task followups"]')
    expect(notes.findAll('li')).toHaveLength(2)
    expect(notes.text()).toContain('Consider a later cleanup.')
    expect(notes.get('strong').text()).toBe('later cleanup')
    expect(notes.text()).toContain('Attempt 1')
    expect(notes.text()).toContain('Attempt 2')
    expect(notes.text()).toContain(formatDateTime('2026-11-05T23:00:00Z'))
    expect(popup().text()).toContain('Notes outside current work')
    expect(popup().get('[aria-label="Additional input history"]').text()).toContain('Keep the existing operator input.')
    expect(notes.findAll('textarea,input,select,[contenteditable="true"]')).toHaveLength(0)
    expect(popup().findAll('textarea')).toHaveLength(1)

    await notes.findAll('button')[0]!.trigger('click')
    expect(wrapper.emitted('openTicket')).toEqual([[source]])
    expect(callOperation).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('preserves task drafts, scroll, disclosure and focus through followup source inspection and back', async () => {
    const record = taskWithFollowups()
    const source = sourceTicket()
    const wrapper = mount(TrackerTaskDetailDialog, {
      props: { modelValue: true, task: record, tickets: [source], refreshContent: vi.fn().mockResolvedValue(record) },
      attachTo: document.body,
    })
    await flushPromises()
    const frame = popup().get('[role="dialog"]').element
    const body = popup().get<HTMLElement>('.ui-dialog__body').element
    await popup().get('[data-edit-content]').trigger('click')
    await popup().get('[data-description-editor]').setValue('Unsaved description')
    await popup().findAll('textarea')[1]!.setValue('Unsaved additional input')
    const details = popup().get<HTMLDetailsElement>('.tracker-task-detail details').element
    details.open = true
    body.scrollTop = 240
    const origin = popup().get('[aria-label="Task followups"] button')
    ;(origin.element as HTMLElement).focus()
    await origin.trigger('click')
    expect(wrapper.emitted('openTicket')?.[0]).toEqual([source])

    // The existing route owner prepares inspection before updating the dialog props.
    wrapper.vm.prepareInspection(source.key)
    await wrapper.setProps({ ticketKey: source.key, ticket: source })
    await flushPromises()
    expect(popup().get('[role="dialog"]').element).toBe(frame)
    expect(body.scrollTop).toBe(0)
    expect(document.activeElement).toBe(popup().get('h2').element)
    await popup().findAll('button').find(button => button.text() === 'Back to task')!.trigger('click')
    expect(wrapper.emitted('backToTask')).toHaveLength(1)
    await wrapper.setProps({ ticketKey: '', ticket: null })
    await flushPromises()

    expect(popup().get('[role="dialog"]').element).toBe(frame)
    expect(popup().get<HTMLTextAreaElement>('[data-description-editor]').element.value).toBe('Unsaved description')
    expect(popup().findAll('textarea')[1]!.element.value).toBe('Unsaved additional input')
    expect(body.scrollTop).toBe(240)
    expect(details.open).toBe(true)
    expect(document.activeElement).toBe(origin.element)
    expect(callOperation).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('sanitizes followup Markdown and keeps an unavailable source readable without a navigation action', () => {
    const record = taskWithFollowups()
    record.context_json!.followups = [{
      text: '**Safe note** <script>alert(1)</script><img src="x" onerror="alert(2)"> [unsafe](javascript:alert(3))',
      source_ticket_key: 'removed-source', source_attempt: 3, created_at: '2026-11-05T23:00:00Z',
    }]
    const wrapper = mount(TrackerTaskDetailDialog, {
      props: { modelValue: true, task: record }, attachTo: document.body,
    })
    const notes = popup().get('[aria-label="Task followups"]')
    expect(notes.get('strong').text()).toBe('Safe note')
    expect(notes.find('script,[onerror],a[href^="javascript:"]').exists()).toBe(false)
    expect(notes.text()).toContain('removed-source')
    expect(notes.text()).toContain('Source ticket unavailable')
    expect(notes.text()).toContain('Attempt 3')
    expect(notes.find('button').exists()).toBe(false)
    expect(callOperation).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it.each([null, {}, { followups: [] }, { followups: 'invalid' }, {
    followups: [null, {}, 'note', { text: 'Missing origin' }, {
      text: ' ', source_ticket_key: 'source-ticket', source_attempt: 1, created_at: '2026-11-05T23:00:00Z',
    }],
  }])('handles empty, missing or malformed followups safely: %j', (context) => {
    const wrapper = mount(TrackerTaskDetailDialog, {
      props: { modelValue: true, task: { ...task(), context_json: context } }, attachTo: document.body,
    })
    expect(popup().find('[aria-label="Task followups"]').exists()).toBe(false)
    expect(popup().get('h2').text()).toBe(task().title)
    expect(callOperation).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})

function sourceTicket(): TrackerTicket {
  return {
    id: 101,
    project_id: 1,
    tracker_id: 1,
    task_id: task().id,
    task_key: task().key,
    parent_ticket_id: null,
    parent_ticket_key: null,
    run_plan_id: null,
    run_plan_step_id: null,
    run_id: null,
    agent_request_id: null,
    key: 'source-ticket',
    title: 'Source ticket',
    goal: '',
    status: 'complete',
    kind: 'ticket',
    assignee: null,
    priority_key: 'p1',
    lane_key: 'done',
    order_index: 1,
    blocker_reason: null,
    outcome: null,
    effort: null,
    source_kind: 'manual',
    source_json: null,
    definition_of_done_json: [],
    constraints_json: [],
    expected_changes_json: [],
    allowed_paths_json: [],
    completion_evidence_json: null,
    context_json: null,
    metadata_json: null,
    created_by: 'codex',
    claimed_at: null,
    created_at: '2026-11-05T22:00:00Z',
    updated_at: '2026-11-05T22:00:00Z',
    started_at: null,
    completed_at: '2026-11-05T22:00:00Z',
    dependency_keys: [],
    blocked_by: [],
    reference_count: 0,
    link_count: 0,
  }
}

function taskWithFollowups(): TrackerTask {
  return {
    ...task(),
    context_json: {
      operator_inputs: [{ id: 'input-1', text: 'Keep the existing operator input.', created_at: '2026-11-05T22:00:00Z', actor: 'operator' }],
      followups: [
        { text: 'Consider a **later cleanup**.', source_ticket_key: 'source-ticket', source_attempt: 1, created_at: '2026-11-05T23:00:00Z' },
        { text: 'Inspect the next release separately.', source_ticket_key: 'source-ticket', source_attempt: 2, created_at: '2026-11-05T23:30:00Z' },
      ],
    },
  }
}

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
