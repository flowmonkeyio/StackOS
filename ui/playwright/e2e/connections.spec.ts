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
})
