import { expect, test } from '@playwright/test'

import {
  attachAccount,
  createProject,
  resetAccounts,
  resetProjects,
  storeAccount,
  trackConsoleErrors,
} from '../helpers'

test.describe('Telegram shared session controls', () => {
  test.beforeEach(async () => {
    await resetProjects()
    await resetAccounts()
  })

  test('connects and disconnects a shared Account from its attached project', async ({ page }) => {
    const errors = trackConsoleErrors(page)
    const project = await createProject({
      name: 'Telegram session owner',
      slug: 'telegram-session-owner',
      domain: 'telegram-owner.example.test',
    })
    const otherProject = await createProject({
      name: 'Telegram session collaborator',
      slug: 'telegram-session-collaborator',
      domain: 'telegram-collaborator.example.test',
    })
    const account = await storeAccount({
      providerKey: 'telegram',
      authMethodKey: 'tdlib-bot-token',
      displayName: 'Shared Telegram bot',
      attachProjectId: project.id,
      fields: {
        api_id: 12345,
        api_hash: 'e2e-private-application-hash',
        bot_token: '12345:e2e-private-bot-token',
        proxy_enabled: false,
      },
    })
    await attachAccount(otherProject.id, account.credentialRef)

    // The transport is simulated; present the saved bot authorization that a
    // successful TDLib setup would have persisted before session controls open.
    await page.route(
      `**/api/v1/auth/accounts/${encodeURIComponent(account.credentialRef)}/authorization`,
      async (route) => {
        await route.fulfill({
          status: 200,
          json: {
            credential_ref: account.credentialRef,
            provider_key: 'telegram',
            status: 'disconnected',
            generation: null,
            challenge: null,
          },
        })
      },
    )

    const sessionPath = `/api/v1/projects/${project.id}/connections/accounts/${encodeURIComponent(account.credentialRef)}/session`
    let connectRequests = 0
    let disconnectRequests = 0
    let session = {
      credential_ref: account.credentialRef,
      provider_key: 'telegram',
      desired_connected: false,
      connected: false,
      status: 'disconnected',
      project_ids: [project.id, otherProject.id],
      affects_other_projects: true,
      next_action: 'Connect this shared Account when an agent needs it.',
    }
    await page.route(`**${sessionPath}`, async (route) => {
      if (route.request().method() !== 'GET') return route.fallback()
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify(session) })
    })
    await page.route(`**${sessionPath}/connect`, async (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      connectRequests += 1
      session = {
        ...session,
        desired_connected: true,
        connected: true,
        status: 'connected',
        next_action: 'The shared session is ready for permitted delivery.',
      }
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ data: session, run_id: 1, project_id: project.id }),
      })
    })
    await page.route(`**${sessionPath}/disconnect`, async (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      disconnectRequests += 1
      session = {
        ...session,
        desired_connected: false,
        connected: false,
        status: 'disconnected',
        next_action: 'The shared session is stopped until an agent connects it again.',
      }
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ data: session, run_id: 2, project_id: project.id }),
      })
    })

    await page.goto(`/projects/${project.id}/connections`)
    await expect(page.getByRole('heading', { level: 1, name: 'Connections' })).toBeVisible()
    const connection = page.getByRole('listitem').filter({ hasText: 'Shared Telegram bot' })
    await connection.getByRole('button', { name: 'Manage Account' }).click()

    const panel = page.getByRole('dialog', { name: 'Edit Telegram Account' })
    await expect(panel.getByRole('heading', { name: 'Telegram session', level: 3 })).toBeVisible()
    await expect(panel).toContainText('This Account is attached to other projects')
    await expect(panel).toContainText('Connecting or disconnecting changes their shared session too')

    await panel.getByRole('button', { name: 'Edit details' }).click()
    await panel.getByLabel('Account name').fill('Shared Telegram bot draft')
    await expect(panel).toContainText(
      'Save or discard Account changes before connecting or continuing Telegram authorization',
    )
    await expect(panel.getByRole('button', { name: 'Connect session' })).toHaveCount(0)
    expect(connectRequests).toBe(0)
    await panel
      .getByText('Save or discard Account changes before connecting or continuing Telegram authorization')
      .scrollIntoViewIfNeeded()
    await page.screenshot({ path: '/private/tmp/stackos-telegram-dirty-session-guard.png' })

    await panel.getByRole('button', { name: 'Cancel' }).click()
    await expect(panel).toBeHidden()
    await connection.getByRole('button', { name: 'Manage Account' }).click()
    await expect(panel.getByRole('button', { name: 'Connect session' })).toBeEnabled()

    await panel.getByRole('button', { name: 'Connect session' }).click()
    await expect.poll(() => connectRequests).toBe(1)
    await expect(panel.getByText('Connected', { exact: true })).toBeVisible()
    await expect(panel).toContainText('disconnect first, save the changes, then connect again')

    await panel.getByRole('button', { name: 'Disconnect session' }).click()
    await expect.poll(() => disconnectRequests).toBe(1)
    await expect(panel.getByText('Disconnected', { exact: true })).toBeVisible()
    expect(await page.content()).not.toContain('e2e-private-application-hash')
    expect(await page.content()).not.toContain('e2e-private-bot-token')
    errors.assertNone()
    await page.screenshot({ path: '/private/tmp/stackos-telegram-shared-session-controls.png' })
  })
})
