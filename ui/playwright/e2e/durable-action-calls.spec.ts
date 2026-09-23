import { expect, test } from '@playwright/test'

import { createProject, resetProjects, trackConsoleErrors } from '../helpers'

test('Action Calls shows receipts and confirms resume before changing delivery', async ({
  page,
}) => {
  await resetProjects()
  const project = await createProject({
    name: 'Durable delivery proof',
    slug: 'durable-delivery-proof',
    domain: 'example.test',
  })
  const errors = trackConsoleErrors(page)
  let state = 'paused'
  const controls: Array<{ operation: string; arguments: Record<string, unknown> }> = []
  const job = () => ({
    id: 8,
    project_id: project.id,
    action_call_id: 42,
    credential_ref: 'cred_e2e',
    action_ref: 'communications.telegram.message.broadcast',
    input_digest: 'fixture-digest',
    state,
    due_at: '2026-09-22T08:00:00Z',
    expires_at: '2099-01-01T00:00:00Z',
    can_cancel: state !== 'cancelled',
    pacing_json: { account_interval_seconds: 2, destination_interval_seconds: 1 },
    item_count: 3,
    pending_count: state === 'cancelled' ? 0 : 1,
    leased_count: 0,
    completed_count: 1,
    failed_count: 0,
    cancelled_count: state === 'cancelled' ? 1 : 0,
    unknown_count: 1,
    next_eligible_at: '2026-09-22T08:00:30Z',
  })
  const items = () =>
    ['succeeded', 'unknown-hold', state === 'cancelled' ? 'cancelled' : 'pending'].map(
      (itemState, ordinal) => ({
        id: ordinal + 101,
        project_id: project.id,
        job_id: 8,
        ordinal,
        destination_ref: `telegram-chat:${100 + ordinal}`,
        correlation_ref: `delivery:${ordinal}`,
        input_json: {},
        state: itemState,
        next_eligible_at: '2026-09-22T08:00:30Z',
        attempt_ref: ordinal < 2 ? `attempt-${ordinal}` : null,
        lease_ref: null,
        lease_expires_at: null,
        attempt_count: ordinal < 2 ? 1 : 0,
        result_json: ordinal === 0 ? { message_ref: 'message:confirmed:500' } : null,
        can_retry: false,
        error: ordinal === 1 ? 'Waiting for the provider receipt' : null,
        progress_json: {},
        temporary_message_ref: null,
        final_message_ref: ordinal === 0 ? 'message:confirmed:500' : null,
        provider_sending_id: String(101 + ordinal),
      }),
    )
  // Provider state is a deterministic fixture; browser controls still exercise
  // the real view, dialogs and operation request serialization. Backend grant
  // and repository transitions have separate integration tests.
  await page.route(`**/api/v1/projects/${project.id}/action-calls?*`, async (route) => {
    await route.fulfill({
      json: {
        items: [
          {
            id: 42,
            project_id: project.id,
            run_id: null,
            run_plan_id: null,
            run_plan_step_id: null,
            action_key: 'telegram.message.broadcast',
            plugin_slug: 'communications',
            provider_key: 'telegram',
            connector_key: 'telegram',
            operation: 'message.broadcast',
            status: 'running',
            dry_run: false,
            credential_ref: 'cred_e2e',
            request_json: {},
            response_json: null,
            metadata_json: {},
            provider_context_json: {},
            cost_cents: 0,
            duration_ms: null,
            error: null,
            created_at: '2026-09-22T08:00:00Z',
            completed_at: null,
          },
        ],
        next_cursor: null,
        total_estimate: 1,
      },
    })
  })
  await page.route('**/api/v1/operations/actionCall.*/call', async (route) => {
    const operation = route.request().url().split('/').at(-2)!
    if (operation === 'actionCall.items') {
      await route.fulfill({ json: { action_call_id: 42, job: job(), items: items(), count: 3 } })
      return
    }
    controls.push({ operation, arguments: route.request().postDataJSON().arguments })
    state =
      operation === 'actionCall.resume'
        ? 'running'
        : operation === 'actionCall.pause'
          ? 'paused'
          : 'cancelled'
    await route.fulfill({ json: { data: job(), project_id: project.id, run_id: null } })
  })
  await page.goto(`/projects/${project.id}/action-calls`)
  await page.getByRole('row').filter({ hasText: 'telegram.message.broadcast' }).click()
  const drawer = page.getByRole('dialog', { name: /Action call #42/ })
  await expect(drawer.getByRole('heading', { name: 'Durable delivery' })).toBeVisible()
  await expect(
    drawer.getByRole('paragraph').filter({ hasText: 'message:confirmed:500' }),
  ).toBeVisible()
  await expect(drawer.getByText(/will not replay automatically/)).toBeVisible()
  await expect(drawer.getByText('1 / 3 settled')).toBeVisible()
  const panelBounds = await drawer.boundingBox()
  const cancelBounds = await drawer.getByRole('button', { name: 'Cancel delivery' }).boundingBox()
  expect(panelBounds).not.toBeNull()
  expect(cancelBounds).not.toBeNull()
  expect(cancelBounds!.x + cancelBounds!.width).toBeLessThanOrEqual(
    panelBounds!.x + panelBounds!.width,
  )
  await drawer.getByRole('button', { name: 'Resume', exact: true }).click()
  expect(controls).toHaveLength(0)
  await page.getByRole('button', { name: 'Resume delivery', exact: true }).click()
  await expect(drawer.getByRole('button', { name: 'Pause', exact: true })).toBeVisible()
  expect(controls[0]).toMatchObject({
    operation: 'actionCall.resume',
    arguments: {
      project_id: project.id,
      action_call_id: 42,
      confirm_direct: true,
    },
  })
  expect(String(controls[0]?.arguments.intent_summary)).toContain('#42')
  await drawer.getByRole('button', { name: 'Pause', exact: true }).click()
  await expect(drawer.getByRole('button', { name: 'Resume', exact: true })).toBeVisible()
  await page.screenshot({ path: '/private/tmp/stackos-durable-action-call.png' })
  errors.assertNone()
})
