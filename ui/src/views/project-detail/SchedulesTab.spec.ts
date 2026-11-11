import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import SchedulesTab from './SchedulesTab.vue'
import ScheduleEditor from './ScheduleEditor.vue'
import AgentUsageSummary from '@/components/domain/AgentUsageSummary.vue'
import RunAbortAction from '@/components/domain/RunAbortAction.vue'
import { UiFormField, UiInput } from '@/components/ui'
import { useSchedulesStore } from '@/stores/schedules'
import { agentStatsFixture } from '@/views/home/agentStatsFixtures'
import { openTrackerStatusStream } from '@/lib/task-tracker/liveEvents'

vi.mock('@/lib/task-tracker/liveEvents', () => ({ openTrackerStatusStream: vi.fn(() => ({ close: vi.fn() })) }))
const ORIG_FETCH = globalThis.fetch
let wrapper: VueWrapper | null = null
const job = {
  id: 1, project_id: 1, kind: 'stable-key', name: 'Daily learning review', template_key: 'core.review',
  cron_expr: '0 22 * * *', timezone: 'America/Los_Angeles', enabled: true,
  next_run_at: '2026-11-10T06:00:00Z', last_run_at: null, last_run_status: null,
  inputs_json: { source: 'fixture' }, execution_json: { project_dir: '/synthetic/project', host_key: 'codex' },
  last_request_id: null, setup_issue: null,
}

async function mountTab() {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/projects/:id/schedules', component: SchedulesTab }, { path: '/projects/:id/tasks', component: { template: '<div>Work graph</div>' } }] })
  await router.push('/projects/1/schedules')
  await router.isReady()
  wrapper = mount(RouterView, { global: { plugins: [router], stubs: { teleport: true } } })
  await flushPromises()
  return wrapper
}
function json(body: unknown, status = 200) { return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }) }
function mockFetch(jobs: unknown[] = []) {
  globalThis.fetch = vi.fn(async (input) => {
    const path = String(input)
    if (path.includes('schedule.runs')) return json({ items: [], next_cursor: null })
    if (path.includes('project.get')) return json({ id: 1, project_dir: '/synthetic/project' })
    if (path.includes('projectAgent.list')) return json({ data: { project_id: 1, project_dir: '/synthetic/project', items: [] } })
    if (path.includes('workflow-templates/')) return json({ spec: { inputs: [{ key: 'source', name: 'Source', type: 'string', required: true }], agent_requirements: [], steps: [] } })
    if (path.includes('workflow-templates')) return json({ templates: [{ key: 'core.review', name: 'Project review' }] })
    return json(jobs)
  }) as typeof fetch
}

describe('Automations and Runs', () => {
  beforeEach(() => { setActivePinia(createPinia()); vi.mocked(openTrackerStatusStream).mockClear(); mockFetch() })
  afterEach(() => { wrapper?.unmount(); wrapper = null; globalThis.fetch = ORIG_FETCH; vi.restoreAllMocks() })

  it('passes the recorded run-plan identity to the same shared run action', async () => {
    const w = await mountTab()
    useSchedulesStore().runs = [{ project_id: 1, request_id: 77, run_plan_id: 456,
      schedule_name: 'Actual scheduled run', status: 'in-progress', task_key: 'workflow-456' } as never]
    await w.get('#cs-tab-runs').trigger('click')
    expect(w.getComponent(RunAbortAction).props('target')).toEqual({ projectId: 1, runPlanId: 456,
      title: 'Actual scheduled run', status: 'in-progress', automated: true })
  })

  it('has exactly two standard tabs, explicit empty states and keyboard activation', async () => {
    const w = await mountTab()
    expect(w.findAll('[role="tab"]').map(tab => tab.text())).toEqual(['Automations', 'Runs'])
    expect(w.text()).toContain('No automations yet')
    expect(w.text()).toContain('Runs only while StackOS is open. Missed runs are skipped.')
    await w.get('#cs-tab-runs').trigger('keydown', { key: 'Enter' })
    expect(w.get('#cs-tab-runs').attributes('aria-selected')).toBe('true')
    expect(w.text()).toContain('No runs yet')
  })

  it('renders actual task usage without summing, exact Work links and missing links', async () => {
    const w = await mountTab()
    const store = useSchedulesStore()
    const metrics = agentStatsFixture().totals
    store.runs = [
      { request_id: 1, schedule_name: 'Very long review name '.repeat(10), task_key: 'workflow-123', status: 'complete', agent_stats: metrics, duration_seconds: 0 },
      { request_id: 2, schedule_name: 'Unknown usage', task_key: null, status: 'failed', agent_stats: null, duration_seconds: null, ended_at: '2026-11-09T00:00:00Z' },
    ] as typeof store.runs
    await w.get('#cs-tab-runs').trigger('click')
    expect(w.findAllComponents(AgentUsageSummary)[0].props('metrics')).toEqual(metrics)
    expect(w.get('a').attributes('href')).toBe('/projects/1/tasks?view=graph&task=workflow-123')
    expect(w.get('table').text()).toContain('0s')
    expect(w.get('table').text()).toContain('N/A')
    expect(w.get('table').text()).toContain('Work unavailable')
    expect(w.get('table').findAll('canvas')).toHaveLength(6)
    await w.get('input[aria-label="Search runs"]').setValue('Unknown')
    expect(w.get('table').findAll('tbody tr')).toHaveLength(1)
    expect(w.find('a').exists()).toBe(false)
  })

  it('patches live and retained traces through one shared subscription and releases it', async () => {
    const w = await mountTab()
    const store = useSchedulesStore()
    store.runs = [{ request_id: 1, schedule_name: 'Actual run', task_key: 'workflow-1', status: 'in-progress', agent_stats: null }] as typeof store.runs
    const stream = vi.mocked(openTrackerStatusStream).mock.calls[0][0]
    const metrics = agentStatsFixture().totals
    stream.onUsage?.({ tasks: [{ key: 'workflow-1', agent_stats: metrics, active_execution: null }], tickets: [] })
    await w.get('#cs-tab-runs').trigger('click')
    expect(w.findAllComponents(AgentUsageSummary)[0].props('metrics')).toEqual(metrics)
    expect(openTrackerStatusStream).toHaveBeenCalledTimes(1)
    expect(globalThis.fetch).toHaveBeenCalledTimes(2)
    const close = vi.mocked(openTrackerStatusStream).mock.results[0].value.close
    w.unmount(); wrapper = null
    expect(close).toHaveBeenCalledOnce()
  })

  it('shows readable configuration and safely reports a rejected enable', async () => {
    mockFetch([job])
    const w = await mountTab()
    expect(w.get('table').text()).toContain('Every day at 10:00 PM')
    globalThis.fetch = vi.fn(async () => json({ detail: 'Workflow needs setup' }, 422)) as typeof fetch
    await w.get('table [role="switch"]').trigger('click')
    await flushPromises()
    expect(w.text()).toContain('Workflow needs setup')
    expect(w.get('table [role="switch"]').attributes('aria-checked')).toBe('true')
  })

  it('preserves live usage arriving while the initial history response is pending', async () => {
    let respond!: (response: Response) => void
    globalThis.fetch = vi.fn(async input => String(input).includes('schedule.runs') ? new Promise<Response>(resolve => { respond = resolve }) : json([])) as typeof fetch
    const w = await mountTab()
    const metrics = agentStatsFixture().totals
    const stream = vi.mocked(openTrackerStatusStream).mock.calls[0][0]
    stream.onUsage?.({ tasks: [{ key: 'workflow-1', agent_stats: metrics, active_execution: null }], tickets: [] })
    respond(json({ items: [{ request_id: 1, task_key: 'workflow-1', schedule_name: 'Run', status: 'complete', agent_stats: null }], next_cursor: null }))
    await flushPromises()
    await w.get('#cs-tab-runs').trigger('click')
    expect(w.findAllComponents(AgentUsageSummary)[0].props('metrics')).toEqual(metrics)
  })

  it('saves an edited draft under the stable key and preserves execution overrides', async () => {
    mockFetch([job])
    const w = await mountTab()
    await w.get('table [data-dropdown-trigger]').trigger('click')
    await w.get('table [role="menuitem"]').trigger('click')
    await flushPromises()
    const save = vi.spyOn(useSchedulesStore(), 'save').mockResolvedValue()
    const editor = w.getComponent(ScheduleEditor)
    await editor.get('form').trigger('submit')
    await flushPromises()
    expect(save).toHaveBeenCalledWith(1, expect.objectContaining({ kind: 'stable-key', name: job.name, inputs_json: job.inputs_json, execution_json: job.execution_json }))
    expect(w.findComponent(ScheduleEditor).exists()).toBe(false)
  })

  it('keeps missing workflow inputs in the editor with a local field error and makes no write', async () => {
    mockFetch([job])
    const w = await mountTab()
    await w.get('table [data-dropdown-trigger]').trigger('click')
    await w.get('table [role="menuitem"]').trigger('click')
    await flushPromises()
    const save = vi.spyOn(useSchedulesStore(), 'save')
    const editor = w.getComponent(ScheduleEditor)
    const source = editor.findAllComponents(UiFormField).find(field => field.props('label') === 'Source')!
    await source.getComponent(UiInput).get('input').setValue('')
    await editor.get('form').trigger('submit')
    await flushPromises()
    expect(source.props('error')).toContain('required')
    expect(editor.find('textarea').exists()).toBe(false)
    expect(save).not.toHaveBeenCalled()
  })

  it('does not present a failed initial read as an empty history', async () => {
    globalThis.fetch = vi.fn(async () => json({ detail: 'Read unavailable' }, 503)) as typeof fetch
    const w = await mountTab()
    expect(w.text()).toContain('Automation list unavailable. Refresh to retry.')
    expect(w.text()).not.toContain('No automations yet')
    await w.get('#cs-tab-runs').trigger('click')
    expect(w.text()).toContain('Run history unavailable. Refresh to retry.')
    expect(w.text()).not.toContain('No runs yet')
  })
})
