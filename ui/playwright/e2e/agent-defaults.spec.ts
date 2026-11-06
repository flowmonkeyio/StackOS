import { expect, test, type Locator } from '@playwright/test'

import {
  createProject,
  getBaseUrl,
  getDaemonToken,
  resetProjects,
  trackConsoleErrors,
} from '../helpers'

async function operation(name: string, args: Record<string, unknown>) {
  const response = await fetch(`${getBaseUrl()}/api/v1/operations/${name}/call`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${getDaemonToken()}`, 'content-type': 'application/json' },
    body: JSON.stringify({ arguments: { response_mode: 'raw', ...args } }),
  })
  expect(response.ok, name).toBe(true)
  return response.json()
}

async function choose(region: Locator, field: string, option: string) {
  await region.getByRole('combobox', { name: field, exact: true }).click()
  await region.getByRole('option', { name: option, exact: true }).click()
}

test('project defaults save/reload preserves roles and optional choices without starting delivery', async ({
  page,
}, testInfo) => {
  // This suite owns a temporary daemon through global-setup. Fail closed on the live port.
  expect(new URL(getBaseUrl()).port).not.toBe('5180')
  await resetProjects()
  const errors = trackConsoleErrors(page)
  const project = await createProject({
    name: 'Agent Defaults Proof',
    slug: 'agent-defaults-proof',
    domain: 'defaults.test',
  })
  const other = await createProject({
    name: 'Empty Defaults Project',
    slug: 'empty-defaults-project',
    domain: 'empty.test',
  })
  const hosts = await operation('hostMcp.status', {})
  const codex = hosts.items.find((host: { host_key: string }) => host.host_key === 'codex')
  const profileHost = hosts.items.find(
    (host: { agent_execution?: { profile_selection: boolean; custom_models: boolean } }) =>
      host.agent_execution?.profile_selection && host.agent_execution.custom_models,
  )
  expect(codex.agent_execution.provider_selection).toBe(true)
  expect(profileHost).toBeTruthy()
  const [model] = codex.agent_execution.models
  expect(model).toBeTruthy()
  const before = await operation('tracker.status', { project_id: project.id })

  await page.goto(`/projects/${project.id}`)
  await page.getByRole('link', { name: 'Agent defaults', exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`/projects/${project.id}/agent-defaults$`))
  await expect(page.getByRole('heading', { name: 'Agent defaults', exact: true })).toBeVisible()
  await expect(page.getByText('No project agent defaults saved.', { exact: false })).toBeVisible()
  const delivery = page.getByRole('region', { name: 'Delivery agent', exact: true })
  const review = page.getByRole('region', { name: 'Review agent', exact: true })
  const deliveryInstructions =
    'Fixture delivery instructions.\nPreserve this entire packet.\nNo execution is requested by saving.'
  const reviewInstructions = 'Fixture independent review.\nKeep this second line exactly.'
  for (const [region, name, instructions] of [
    [delivery, 'Fixture builder', deliveryInstructions],
    [review, 'Fixture reviewer', reviewInstructions],
  ] as const) {
    await region.getByRole('textbox', { name: 'Name', exact: true }).fill(name)
    await region.getByRole('textbox', { name: 'Instructions', exact: true }).fill(instructions)
  }
  await choose(delivery, 'Harness', codex.display_name)
  await choose(delivery, 'Model', model)
  await choose(review, 'Harness', profileHost.display_name)
  await choose(review, 'Model', 'Custom model…')
  await review
    .getByRole('textbox', { name: 'Custom model', exact: true })
    .fill('fixture-custom-model')
  await delivery.getByRole('textbox', { name: 'Provider', exact: true }).fill('fixture-provider')
  await review
    .getByRole('textbox', { name: 'Provider', exact: true })
    .fill('fixture-review-provider')
  await review.getByRole('textbox', { name: 'Profile', exact: true }).fill('fixture-profile')
  await page.getByRole('button', { name: 'Save defaults', exact: true }).click()
  await expect(
    page.getByText('Saved and reloaded project defaults', { exact: false }),
  ).toBeVisible()
  await page.reload()
  await expect(delivery.getByRole('textbox', { name: 'Instructions', exact: true })).toHaveValue(
    deliveryInstructions,
  )
  await expect(review.getByRole('textbox', { name: 'Instructions', exact: true })).toHaveValue(
    reviewInstructions,
  )
  await expect(review.getByRole('textbox', { name: 'Profile', exact: true })).toHaveValue(
    'fixture-profile',
  )
  const first = await operation('tracker.status', { project_id: project.id })
  const expected = {
    delivery_agent: {
      name: 'Fixture builder',
      instructions: deliveryInstructions,
      host_key: 'codex',
      model,
      provider: 'fixture-provider',
    },
    review_agent: {
      name: 'Fixture reviewer',
      instructions: reviewInstructions,
      host_key: profileHost.host_key,
      model: 'fixture-custom-model',
      provider: 'fixture-review-provider',
      profile: 'fixture-profile',
    },
  }
  expect(first.tracker.config_json.delivery).toEqual(expected)
  expect(first.tracker.config_json).toEqual({
    ...(before.tracker.config_json ?? {}),
    delivery: expected,
  })
  await page.screenshot({ path: testInfo.outputPath('agent-defaults-loaded.png'), fullPage: true })

  await delivery.getByRole('textbox', { name: 'Name', exact: true }).fill('Edited builder')
  // A failed save response retains the rendered draft and never reports success.
  await page.route('**/operations/tracker.configureDelivery/call', async (route) => {
    await route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'Fixture save unavailable' }),
    })
  })
  await page.getByRole('button', { name: 'Save defaults', exact: true }).click()
  await expect(page.getByText('Fixture save unavailable', { exact: false })).toBeVisible()
  await expect(delivery.getByRole('textbox', { name: 'Name', exact: true })).toHaveValue(
    'Edited builder',
  )
  await expect(page.getByText('Defaults saved', { exact: true })).toHaveCount(0)
  await page.unroute('**/operations/tracker.configureDelivery/call')
  await page.getByRole('button', { name: 'Save defaults', exact: true }).click()
  await expect(
    page.getByText('Saved and reloaded project defaults', { exact: false }),
  ).toBeVisible()
  await page.reload()
  await expect(delivery.getByRole('textbox', { name: 'Name', exact: true })).toHaveValue(
    'Edited builder',
  )
  expected.delivery_agent.name = 'Edited builder'
  const after = await operation('tracker.status', { project_id: project.id })
  expect(after.tracker.config_json.delivery).toEqual(expected)
  expect(after.task_counts).toEqual(before.task_counts)
  expect(after.ticket_counts).toEqual(before.ticket_counts)
  await testInfo.attach('defaults-api-readback', {
    body: JSON.stringify({ project_id: project.id, before, first, after }, null, 2),
    contentType: 'application/json',
  })

  // Exercise the app's project-keyed lifecycle without a document reload.
  await choose(delivery, 'Model', 'Custom model…')
  await delivery.getByRole('textbox', { name: 'Custom model', exact: true }).fill('unsaved-custom-model')
  await page.getByRole('button', { name: /Switch project/ }).click()
  await page.getByRole('option', { name: /Empty Defaults Project/ }).click()
  await expect(page).toHaveURL(new RegExp(`/projects/${other.id}/agent-defaults$`))
  await expect(page.getByText('No project agent defaults saved.', { exact: false })).toBeVisible()
  await expect(delivery.getByRole('textbox', { name: 'Name', exact: true })).toHaveValue('')
  await page.getByRole('button', { name: /Switch project/ }).click()
  await page.getByRole('option', { name: /Agent Defaults Proof/ }).click()
  await expect(page).toHaveURL(new RegExp(`/projects/${project.id}/agent-defaults$`))
  await expect(delivery.getByRole('textbox', { name: 'Name', exact: true })).toHaveValue(
    'Edited builder',
  )
  await expect(delivery.getByRole('textbox', { name: 'Custom model', exact: true })).toHaveCount(0)
  await expect(delivery.getByRole('combobox', { name: 'Model', exact: true })).toHaveText(model)
  await page.screenshot({ path: testInfo.outputPath('agent-defaults-saved.png'), fullPage: true })
  errors.assertNone()
})
