import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

import {
  createProject,
  getCredentialEditState,
  resetProjects,
  storeCredential,
  trackConsoleErrors,
} from '../helpers'

test.describe('Connections — credential lifecycle', () => {
  test.beforeEach(async () => {
    await resetProjects()
  })

  test('edits a credential through the real UI-token boundary', async ({ page }) => {
    const errors = trackConsoleErrors(page)
    const project = await createProject({
      name: 'Connections Project',
      slug: 'connections-project',
      domain: 'connections.example.test',
    })
    const credential = await storeCredential({
      projectId: project.id,
      providerKey: 'ftp',
      authMethodKey: 'ftp-password',
      profileKey: 'primary',
      label: 'Production FTP',
      fields: {
        host: 'ftp.example.test',
        username: 'deploy',
        password: 'ftp-test-secret',
        tls_mode: 'none',
      },
    })

    await page.goto(`/projects/${project.id}/connections`)
    await expect(page.getByRole('heading', { level: 1, name: 'Connections' })).toBeVisible()

    const connection = page.getByRole('listitem').filter({ hasText: 'Production FTP' })
    await connection.getByRole('button', { name: 'Edit' }).click()
    await expect(page.getByText('Edit connection', { exact: true })).toBeVisible()

    await page.getByLabel('Display label').fill('Updated FTP')
    await page.getByRole('button', { name: 'Save changes' }).click()

    await expect(page.getByText('Connection settings updated.')).toBeVisible()
    await expect(page.getByText('Updated FTP', { exact: true })).toBeVisible()

    const editState = await getCredentialEditState(project.id, credential.credentialRef)
    expect(editState.values.host).toBe('ftp.example.test')
    expect(editState.secret_present).toEqual({ password: true })
    errors.assertNone()
  })

  test('renders manifest-driven OAuth readiness and setup guidance accessibly', async ({ page }) => {
    const errors = trackConsoleErrors(page)
    const project = await createProject({
      name: 'OAuth Readiness Project',
      slug: 'oauth-readiness-project',
      domain: 'oauth-readiness.example.test',
    })
    await storeCredential({
      projectId: project.id,
      providerKey: 'hubspot',
      authMethodKey: 'oauth2_authorization_code',
      profileKey: 'primary',
      label: 'HubSpot OAuth Draft',
      fields: {
        client_id: 'e2e-client-id',
        client_secret: 'e2e-client-secret',
        scope_bundles: ['sales'],
      },
    })

    await page.goto(`/projects/${project.id}/connections`)
    await expect(page.getByRole('heading', { level: 1, name: 'Connections' })).toBeVisible()
    const connection = page.getByRole('listitem').filter({ hasText: 'HubSpot OAuth Draft' })
    await expect(connection.getByText('Capability readiness', { exact: true })).toBeVisible()
    for (const label of [
      'CRM Core',
      'Sales',
      'Marketing',
      'Bulk',
      'Webhooks',
      'Custom Workflow Automation',
      'Transactional Communications',
    ]) {
      await expect(connection.getByText(label, { exact: true })).toBeVisible()
    }

    await connection.getByRole('button', { name: 'Edit' }).click()
    const panel = page.getByRole('dialog', { name: 'Edit connection' })
    await expect(panel).toBeVisible()
    await expect(
      panel.getByText('https://auth.stackos.flowmonkey.io/api/v1/auth/oauth/callback', {
        exact: true,
      }),
    ).toBeVisible()
    await expect(panel.getByRole('link', { name: /Provider console/ })).toHaveAttribute(
      'href',
      'https://app.hubspot.com/developer-projects',
    )
    await expect(panel.getByText('Reconnect guidance', { exact: true })).toBeVisible()
    await expect(panel.getByRole('button', { name: 'Reconnect' })).toBeVisible()

    const axe = await new AxeBuilder({ page })
      .include('[role="dialog"]')
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze()
    expect(
      axe.violations,
      axe.violations.map((violation) => `${violation.id}: ${violation.help}`).join('\n'),
    ).toEqual([])
    errors.assertNone()
  })

  test('chooses a multi-method provider with the keyboard and clears switched drafts', async ({
    page,
  }) => {
    const errors = trackConsoleErrors(page)
    const project = await createProject({
      name: 'Auth Method Choice Project',
      slug: 'auth-method-choice-project',
      domain: 'auth-method-choice.example.test',
    })

    await page.goto(`/projects/${project.id}/connections`)
    await page.getByRole('button', { name: 'Add connection' }).first().click()

    const panel = page.getByRole('dialog', { name: 'Add connection' })
    await expect(panel).toBeVisible()
    await panel.getByRole('combobox', { name: 'Service' }).click()
    await panel.getByRole('combobox', { name: 'Search options' }).fill('HubSpot')
    await panel.getByRole('option', { name: 'HubSpot oauth-or-api-key' }).click()

    await expect(
      panel.getByText('Choose an authentication method to review its setup and continue.'),
    ).toBeVisible()
    await expect(panel.getByRole('button', { name: 'Save and verify' })).toBeDisabled()
    await expect(panel.getByLabel('OAuth Client ID')).toHaveCount(0)
    await expect(
      panel.getByRole('textbox', { name: 'Private App Access Token', exact: true }),
    ).toHaveCount(0)

    const oauth = panel.getByRole('radio', { name: /Connect with HubSpot/ })
    const privateApp = panel.getByRole('radio', { name: /Private app access token/ })
    await oauth.focus()
    await page.keyboard.press('Space')
    await expect(oauth).toBeChecked()
    await panel.getByLabel('OAuth Client ID').fill('draft-that-must-be-cleared')

    await oauth.focus()
    await page.keyboard.press('ArrowRight')
    await expect(privateApp).toBeChecked()
    await expect(panel.getByLabel('OAuth Client ID')).toHaveCount(0)
    await expect(
      panel.getByRole('textbox', { name: 'Private App Access Token', exact: true }),
    ).toBeVisible()

    await privateApp.focus()
    await page.keyboard.press('ArrowLeft')
    await expect(oauth).toBeChecked()
    await expect(panel.getByLabel('OAuth Client ID')).toHaveValue('')
    await expect(panel).not.toContainText('draft-that-must-be-cleared')

    const axe = await new AxeBuilder({ page })
      .include('[role="dialog"]')
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze()
    expect(
      axe.violations,
      axe.violations.map((violation) => `${violation.id}: ${violation.help}`).join('\n'),
    ).toEqual([])
    errors.assertNone()
  })

  test('pins the saved method, keeps readiness secret-free, and revokes locally', async ({
    page,
  }) => {
    const errors = trackConsoleErrors(page)
    const secretCanary = 'hubspot-e2e-private-token-canary'
    const project = await createProject({
      name: 'Auth Method Lifecycle Project',
      slug: 'auth-method-lifecycle-project',
      domain: 'auth-method-lifecycle.example.test',
    })
    await storeCredential({
      projectId: project.id,
      providerKey: 'hubspot',
      authMethodKey: 'private_app_token',
      profileKey: 'private-app',
      label: 'HubSpot Private App',
      fields: {
        access_token: secretCanary,
      },
    })

    await page.goto(`/projects/${project.id}/connections`)
    const connection = page.getByRole('listitem').filter({ hasText: 'HubSpot Private App' })
    await expect(connection.getByText('Capability readiness', { exact: true })).toBeVisible()
    await expect(connection.getByText('Private app access token', { exact: true })).toBeVisible()
    expect(await page.content()).not.toContain(secretCanary)

    await connection.getByRole('button', { name: 'Edit' }).click()
    const panel = page.getByRole('dialog', { name: 'Edit connection' })
    const oauth = panel.getByRole('radio', { name: /Connect with HubSpot/ })
    const privateApp = panel.getByRole('radio', { name: /Private app access token/ })
    await expect(privateApp).toBeChecked()
    await expect(privateApp).toBeDisabled()
    await expect(oauth).toBeDisabled()
    await expect(panel).toContainText('Authentication method is locked to Private app access token.')
    await expect(panel).toContainText('create a separate named profile')
    await expect(panel).toContainText('reassign exact consumers')
    await expect(panel).toContainText('locally revoke the old profile')
    expect(await page.content()).not.toContain(secretCanary)

    const axe = await new AxeBuilder({ page })
      .include('[role="dialog"]')
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze()
    expect(
      axe.violations,
      axe.violations.map((violation) => `${violation.id}: ${violation.help}`).join('\n'),
    ).toEqual([])

    await panel.getByRole('button', { name: 'Cancel' }).click()
    await connection.getByRole('button', { name: 'Revoke' }).click()
    await page.getByRole('dialog', { name: 'Revoke this connection?' }).getByRole('button', {
      name: 'Revoke connection',
    }).click()
    await expect(page.getByText('No services connected')).toBeVisible()
    expect(await page.content()).not.toContain(secretCanary)
    errors.assertNone()
  })
})
