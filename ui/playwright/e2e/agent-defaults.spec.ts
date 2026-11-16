import { expect, test, type Locator } from '@playwright/test'
import { spawnSync } from 'node:child_process'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

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

function selectionErrors(delivery: unknown): Record<string, string | null> {
  const repoRoot = fileURLToPath(new URL('../../../', import.meta.url))
  const env = { ...process.env }
  delete env.PYTHONHOME
  delete env.PYTHONPATH
  const result = spawnSync(
    process.env.STACKOS_E2E_PYTHON ?? join(repoRoot, '.venv/bin/python'),
    [
      '-c',
      `
import json, sys
from stackos.host_mcp.service import agent_review_sandbox, agent_selection_error
from stackos.operations.tracker.schemas import TaskDeliveryAgent, TrackerConfigureDeliveryInput
defaults = TrackerConfigureDeliveryInput.model_validate({"project_id": 1, "delivery": json.load(sys.stdin)}).delivery.model_dump(exclude_unset=True)
errors = {}
for role in ("delivery_agent", "review_agent"):
    agent = TaskDeliveryAgent.model_validate(defaults[role]).model_dump(exclude_none=True)
    host = agent["host_key"]
    sandbox = agent_review_sandbox(host) if role == "review_agent" else "workspace-write"
    errors[role] = agent_selection_error(host, sandbox=sandbox, model=agent.get("model"), provider=agent.get("provider"), profile=agent.get("profile"))
print(json.dumps(errors))
`,
    ],
    { cwd: repoRoot, env, input: JSON.stringify(delivery), encoding: 'utf8', timeout: 30_000 },
  )
  expect(result.error, 'canonical validator process').toBeUndefined()
  expect(result.status, result.stderr).toBe(0)
  return JSON.parse(result.stdout)
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
  await delivery
    .getByRole('textbox', { name: 'Custom model', exact: true })
    .fill('unsaved-custom-model')
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

test('harness transitions preserve selectors until explicit clearing and pass canonical selection validation', async ({
  page,
}, testInfo) => {
  expect(new URL(getBaseUrl()).port).not.toBe('5180')
  await resetProjects()
  const errors = trackConsoleErrors(page)
  const project = await createProject({
    name: 'Harness Transition Proof',
    slug: 'harness-transition-proof',
    domain: 'transition.test',
  })
  const hosts = await operation('hostMcp.status', {})
  const codex = hosts.items.find((host: { host_key: string }) => host.host_key === 'codex')
  const claude = hosts.items.find((host: { host_key: string }) => host.host_key === 'claude-code')
  const hermes = hosts.items.find((host: { host_key: string }) => host.host_key === 'hermes')
  expect(hermes.agent_execution.profile_selection).toBe(true)
  expect(hermes.agent_execution.provider_selection).toBe(true)
  expect(codex.agent_execution.profile_selection).toBe(false)
  expect(claude.agent_execution.provider_selection).toBe(false)
  expect(claude.agent_execution.profile_selection).toBe(false)
  const initial = {
    host_key: 'hermes',
    delivery_agent: {
      name: 'Transition builder',
      instructions: 'Preserve delivery instructions.\nSecond line.',
      model: 'fixture-model',
      provider: 'openai',
      profile: 'work',
    },
    review_agent: {
      name: 'Transition reviewer',
      instructions: 'Preserve independent review instructions.',
      model: 'fixture-model',
      provider: 'openai',
      profile: 'review-work',
    },
  }
  await operation('tracker.configureDelivery', { project_id: project.id, delivery: initial })
  const before = await operation('tracker.status', { project_id: project.id })
  await page.goto(`/projects/${project.id}/agent-defaults`)
  const delivery = page.getByRole('region', { name: 'Delivery agent', exact: true })
  const review = page.getByRole('region', { name: 'Review agent', exact: true })
  await expect(delivery.getByRole('textbox', { name: 'Profile', exact: true })).toHaveValue('work')
  await choose(delivery, 'Harness', codex.display_name)
  await choose(delivery, 'Model', codex.agent_execution.models[0])
  await choose(review, 'Harness', claude.display_name)
  await choose(review, 'Model', claude.agent_execution.models[0])
  for (const [region, profile] of [
    [delivery, 'work'],
    [review, 'review-work'],
  ] as const) {
    await expect(region.getByRole('textbox', { name: 'Provider', exact: true })).toHaveValue(
      'openai',
    )
    await expect(region.getByRole('textbox', { name: 'Profile', exact: true })).toHaveValue(profile)
    await expect(region.getByRole('textbox', { name: 'Profile', exact: true })).toBeEnabled()
  }
  await expect(review.getByRole('textbox', { name: 'Provider', exact: true })).toBeEnabled()
  await expect(review.getByText('Clear the saved provider', { exact: false })).toBeVisible()
  const retained = {
    ...initial,
    delivery_agent: {
      ...initial.delivery_agent,
      host_key: 'codex',
      model: codex.agent_execution.models[0],
    },
    review_agent: {
      ...initial.review_agent,
      host_key: 'claude-code',
      model: claude.agent_execution.models[0],
    },
  }
  await page.getByRole('button', { name: 'Save defaults', exact: true }).click()
  await expect(
    page.getByText('Saved and reloaded project defaults', { exact: false }),
  ).toBeVisible()
  const savedRetained = await operation('tracker.status', { project_id: project.id })
  expect(savedRetained.tracker.config_json.delivery).toEqual(retained)
  const incompatible = selectionErrors(savedRetained.tracker.config_json.delivery)
  expect(incompatible.delivery_agent).toContain('profile selector')
  expect(incompatible.review_agent).toContain('provider selector')
  await page.screenshot({
    path: testInfo.outputPath('harness-selectors-retained.png'),
    fullPage: true,
  })

  for (const [region, field] of [
    [delivery, 'Profile'],
    [review, 'Provider'],
    [review, 'Profile'],
  ] as const) {
    const input = region.getByRole('textbox', { name: field, exact: true })
    await input.locator('..').getByRole('button', { name: 'Clear input', exact: true }).click()
    await expect(input).toHaveCount(0)
  }
  await page.getByRole('button', { name: 'Save defaults', exact: true }).click()
  await expect(
    page.getByText('Saved and reloaded project defaults', { exact: false }),
  ).toBeVisible()
  await page.reload()
  await expect(delivery.getByRole('textbox', { name: 'Instructions', exact: true })).toHaveValue(
    initial.delivery_agent.instructions,
  )
  await expect(delivery.getByRole('textbox', { name: 'Profile', exact: true })).toHaveCount(0)
  await expect(review.getByRole('textbox', { name: 'Provider', exact: true })).toHaveCount(0)
  await expect(review.getByRole('textbox', { name: 'Profile', exact: true })).toHaveCount(0)
  const after = await operation('tracker.status', { project_id: project.id })
  const cleared = {
    ...retained,
    delivery_agent: { ...retained.delivery_agent, profile: null },
    review_agent: { ...retained.review_agent, provider: null, profile: null },
  }
  expect(after.tracker.config_json).toEqual({ ...before.tracker.config_json, delivery: cleared })
  expect(after.task_counts).toEqual(before.task_counts)
  expect(after.ticket_counts).toEqual(before.ticket_counts)
  const accepted = selectionErrors(after.tracker.config_json.delivery)
  expect(accepted).toEqual({ delivery_agent: null, review_agent: null })
  await testInfo.attach('canonical-transition-validation', {
    body: JSON.stringify(
      {
        retained: savedRetained.tracker.config_json.delivery,
        incompatible,
        cleared: after.tracker.config_json.delivery,
        accepted,
      },
      null,
      2,
    ),
    contentType: 'application/json',
  })
  await page.screenshot({
    path: testInfo.outputPath('harness-selectors-cleared.png'),
    fullPage: true,
  })
  errors.assertNone()
})
