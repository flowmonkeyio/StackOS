import { generateKeyPairSync } from 'node:crypto'
import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

import { getAccountEditState, resetAccounts, resetProjects, trackConsoleErrors } from '../helpers'

// This fixture contains an ephemeral private key: do not capture request bodies in traces/video.
test.use({ trace: 'off', video: 'off' })

test('saves service-account JSON locally and edits delegation through the generic Account panel', async ({ page }, testInfo) => {
  await resetProjects()
  await resetAccounts()
  const errors = trackConsoleErrors(page)
  const { privateKey } = generateKeyPairSync('rsa', { modulusLength: 2048 })
  const keyJson = JSON.stringify({
    type: 'service_account', client_email: 'browser@example.iam.gserviceaccount.com',
    private_key: privateKey.export({ type: 'pkcs8', format: 'pem' }).toString(),
    token_uri: 'https://oauth2.googleapis.com/token',
  }, null, 2)
  const summary = 'Google service-account token acquired; resource access is unverified.'
  const nextAction = 'Verify access to the intended Google resource before relying on this Account.'
  let credentialRef = ''
  let testCalls = 0
  let oauthStarts = 0
  page.on('request', (request) => {
    if (request.url().endsWith('/start')) oauthStarts += 1
  })
  // Intercept the local daemon test route BEFORE Save. Intercepting Google in
  // the browser would not intercept server-side token acquisition.
  await page.route('**/api/v1/auth/accounts/*/test', async (route) => {
    testCalls += 1
    credentialRef = new URL(route.request().url()).pathname.split('/').at(-2)!
    await route.fulfill({ json: { data: {
      credential_ref: credentialRef, provider_key: 'google-workspace', ok: true,
      status: 'connected', summary, next_action: nextAction,
      checked_at: '2026-09-25T00:00:00Z', retryable: false,
      metadata: { verification: 'token_acquisition_only', resource_access: 'unverified' },
    } } })
  })
  await page.goto('/accounts')
  await page.getByRole('button', { name: 'Add Account' }).first().click()
  const panel = page.getByRole('dialog', { name: 'Add Account' })
  await panel.getByRole('combobox', { name: 'Service' }).click()
  await panel.getByRole('combobox', { name: 'Search options' }).fill('Google Workspace')
  await panel.getByRole('option', { name: 'Google Workspace oauth' }).click()
  await expect(panel.getByRole('button', { name: 'Save and verify' })).toBeDisabled()
  const serviceAccount = panel.getByRole('radio', { name: /Service account/ })
  await serviceAccount.focus()
  await page.keyboard.press('Space')
  await expect(serviceAccount).toBeChecked()
  await panel.getByLabel('Account name').fill('Synthetic Workspace service account')
  const keyInput = panel.getByRole('textbox', { name: 'Service-account JSON key', exact: true })
  await expect(keyInput).toHaveAttribute('type', 'password')
  await keyInput.fill(keyJson)
  await panel.getByLabel('Delegated Workspace user', { exact: true }).fill('operator@example.com')
  await panel.screenshot({ path: testInfo.outputPath('masked-create.png') })
  await panel.getByRole('button', { name: 'Save and verify' }).click()
  await expect(page.getByText(summary, { exact: false })).toBeVisible()
  await expect(page.getByText(nextAction, { exact: false })).toBeVisible()
  expect(testCalls).toBe(1)
  expect(oauthStarts).toBe(0)
  expect(await page.content()).not.toContain('BEGIN PRIVATE KEY')
  let edit = await getAccountEditState(credentialRef)
  expect(edit.secret_present).toEqual({ service_account_json: true })
  expect(edit.values.delegated_subject).toBe('operator@example.com')

  await page.getByRole('button', { name: 'Edit', exact: true }).click()
  const saved = page.getByRole('dialog', { name: 'Edit Account' })
  await expect(saved.getByRole('radio', { name: /Service account/ })).toBeDisabled()
  await expect(saved.getByRole('radio', { name: /Connect with Google/ })).toBeDisabled()
  await expect(saved.getByRole('textbox', { name: 'Service-account JSON key', exact: true })).toHaveValue('')
  await expect(saved).toContainText('Saved — leave blank to keep it.')
  await saved.getByLabel('Delegated Workspace user', { exact: true }).fill('')
  await saved.screenshot({ path: testInfo.outputPath('saved-edit.png') })
  const axe = await new AxeBuilder({ page }).include('[role="dialog"]')
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze()
  expect(axe.violations.map(({ id, help }) => ({ id, help }))).toEqual([])
  await saved.getByRole('button', { name: 'Save changes' }).click()
  await expect(page.getByText('Account updated.')).toBeVisible()
  edit = await getAccountEditState(credentialRef)
  expect(edit.values.delegated_subject ?? '').toBe('')
  expect(edit.secret_present).toEqual({ service_account_json: true })
  expect(testCalls).toBe(1)
  expect(oauthStarts).toBe(0)
  errors.assertNone()
})
