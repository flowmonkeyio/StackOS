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
})
