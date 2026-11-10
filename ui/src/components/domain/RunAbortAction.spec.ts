import { afterEach, describe, expect, it, vi } from 'vitest'
import { DOMWrapper, flushPromises, mount } from '@vue/test-utils'
import { defineComponent, ref } from 'vue'
import RunAbortAction from './RunAbortAction.vue'
import RunAbortDialog from './RunAbortDialog.vue'
import { taskRunAbortTarget, useRunAbort, type RunAbortTarget } from '@/composables/useRunAbort'
import type { TrackerTask } from '@/lib/task-tracker/types'
import { ApiError } from '@/lib/client'
import type { UiDropdownMenuProps } from '@/components/ui/UiDropdownMenu.vue'

const call = vi.hoisted(() => vi.fn())
vi.mock('@/lib/operations', () => ({ callOperation: call }))
const target: RunAbortTarget = { projectId: 7, runPlanId: 123, title: 'A very long selected workflow title', status: 'in-progress', automated: false }
const popup = () => new DOMWrapper(document.body)
let dispose = () => {}
afterEach(() => { dispose(); useRunAbort().close(); document.body.innerHTML = ''; call.mockReset() })

async function render(automated = false, placement?: UiDropdownMenuProps['placement'], keyboard = false) {
  const selected = ref({ ...target, automated })
  const refresh = vi.fn(() => { selected.value.status = 'aborted' })
  const wrapper = mount(defineComponent({
    components: { RunAbortAction, RunAbortDialog },
    setup: () => ({ selected, refresh, placement }),
    template: '<RunAbortAction :target="selected" :placement="placement" @refresh="refresh" /><RunAbortDialog />',
  }), { attachTo: document.body })
  dispose = () => wrapper.unmount()
  await wrapper.get('button[aria-label^="Run actions"]').trigger('click')
  if (keyboard) await wrapper.get('[role="menu"]').trigger('keydown', { key: 'Enter' })
  else await wrapper.get('[role="menuitem"]').trigger('click')
  return { wrapper, selected, refresh }
}

function confirm() { return popup().findAll('button').find(button => button.text() === 'Abort run')! }

describe('shared Abort run action', () => {
  it.each([undefined, 'top-end'] as const)('retains the canonical confirmation and keyboard selection for placement %s', async (placement) => {
    const { wrapper } = await render(false, placement, true)
    expect(wrapper.getComponent(RunAbortAction).props('placement')).toBe(placement ?? 'bottom-end')
    expect(popup().get('[role="dialog"]').text()).toContain('Abort run?')
    expect(useRunAbort().selected.value).toMatchObject(target)
    await popup().get('[role="dialog"]').trigger('keydown', { key: 'Escape' })
    expect(useRunAbort().open.value).toBe(false)
    expect(call).not.toHaveBeenCalled()
  })

  it.each([true, false])('names the selected run, prevents repeat/close while pending and retains the truthful result after refresh (automated=%s)', async (automated) => {
    let resolve!: (value: unknown) => void
    call.mockImplementation(() => new Promise(done => { resolve = done }))
    const { wrapper, refresh } = await render(automated)
    expect(popup().text()).toContain(target.title)
    expect(popup().text()).toContain(automated ? 'Stops only StackOS-owned automated invocations' : 'Changes run status only')
    expect(popup().text()).toContain('Future schedules stay enabled')
    await confirm().trigger('click')
    await flushPromises()
    expect(popup().text()).toContain('Aborting…')
    expect(wrapper.get('button[aria-label^="Run actions"]').attributes('disabled')).toBeDefined()
    await popup().get('[role="dialog"]').trigger('keydown', { key: 'Escape' })
    expect(popup().find('[role="dialog"]').exists()).toBe(true)
    await useRunAbort().abort()
    expect(call).toHaveBeenCalledTimes(1)
    expect(call).toHaveBeenCalledWith('runPlan.abort', expect.objectContaining({ project_id: 7, run_plan_id: 123 }))
    resolve({ data: { cancellation: { status: automated ? 'managed_stopped' : 'state_only' } } })
    await flushPromises()
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(wrapper.find('button[aria-label^="Run actions"]').exists()).toBe(false)
    expect(popup().text()).toContain(automated ? 'automated work stopped' : 'No managed invocation was stopped')
  })

  it('shows unconfirmed-stop errors after the canonical abort and can retry the same identity', async () => {
    call.mockRejectedValueOnce(new ApiError('unconfirmed', 409, { detail: 'Run aborted; managed stop could not be confirmed.' }))
      .mockResolvedValueOnce({ data: { cancellation: { status: 'state_only' } } })
    await render(true)
    await confirm().trigger('click')
    await flushPromises()
    expect(popup().text()).toContain('managed stop could not be confirmed')
    expect(popup().find('[role="dialog"]').exists()).toBe(true)
    await popup().findAll('button').find(button => button.text() === 'Retry abort')!.trigger('click')
    await flushPromises()
    expect(call).toHaveBeenCalledTimes(2)
    expect(popup().text()).toContain('No managed invocation was stopped')
  })

  it('requires a real workflow source link and omits all terminal actions', () => {
    expect(taskRunAbortTarget({ source_kind: 'manual', source_json: null } as TrackerTask)).toBeNull()
    expect(taskRunAbortTarget({ source_kind: 'workflow', source_json: { run_plan_id: '123' } } as unknown as TrackerTask)).toBeNull()
    const linked = { source_kind: 'workflow', source_json: { run_plan_id: 123 }, project_id: 7, title: 'Actual' } as unknown as TrackerTask
    expect(taskRunAbortTarget(linked)).toMatchObject({ projectId: 7, runPlanId: 123, title: 'Actual' })
    for (const status of ['complete', 'completed', 'failed', 'aborted', 'skipped']) {
      const wrapper = mount(RunAbortAction, { props: { target: { ...target, status } } })
      expect(wrapper.find('button').exists()).toBe(false)
      wrapper.unmount()
    }
  })
})
