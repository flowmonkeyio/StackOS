import { expect, test } from '@playwright/test'

import {
  getAccountEditState,
  resetAccounts,
  resetProjects,
  storeAccount,
  trackConsoleErrors,
} from '../helpers'

test.describe('Telegram Account proxy setup', () => {
  test.beforeEach(async () => {
    await resetProjects()
    await resetAccounts()
  })

  test('shows a staged user setup with proxy settings collapsed by default', async ({ page }) => {
    const errors = trackConsoleErrors(page)
    await page.addInitScript(() => localStorage.setItem('cs:theme', 'dark'))
    await page.goto('/accounts')
    await page.getByRole('button', { name: 'Add Account' }).click()
    const panel = page.getByRole('dialog', { name: 'Add Account' })
    await panel.getByRole('combobox', { name: 'Service' }).click()
    await panel.getByRole('combobox', { name: 'Search options' }).fill('Telegram')
    await panel.getByRole('option', { name: /Telegram/ }).click()
    await panel.getByText('User account', { exact: true }).click()

    const telegramPanel = page.getByRole('dialog', { name: 'Connect Telegram' })
    const progress = telegramPanel.getByRole('navigation', { name: 'Telegram Account setup' })
    await expect(progress).toContainText('Account details')
    await expect(progress).toContainText('Sign in')
    await expect(progress).toContainText('Ready')
    await expect(telegramPanel.getByText('Your phone number and sign-in challenge come after saving this Account.')).toBeVisible()
    await expect(telegramPanel.getByRole('button', { name: 'Save and continue' })).toBeVisible()
    await expect(telegramPanel.getByRole('textbox', { name: 'Phone number' })).toHaveCount(0)
    await expect(telegramPanel.getByRole('button', { name: /Proxy settings \(optional\)/ })).toHaveAttribute(
      'aria-expanded',
      'false',
    )
    await page.screenshot({ path: '/private/tmp/stackos-telegram-user-setup.png' })
    errors.assertNone()
  })

  test('renders each dark-mode sign-in stage and resumes without connecting', async ({ page }) => {
    const errors = trackConsoleErrors(page)
    const requests: string[] = []
    const answers: Array<Record<string, string>> = []
    let credentialRef = ''
    let state: Record<string, unknown> | null = null
    let signInSaved = false
    let testCalls = 0
    const challenge = (kind: string, generation: number, field: string) => ({
      credential_ref: credentialRef,
      provider_key: 'telegram',
      status: 'challenge',
      generation,
      challenge: { generation, kind, fields: [field], metadata: {} },
    })
    page.on('request', (request) => {
      if (request.url().includes('/session/connect')) requests.push('connect')
    })
    await page.route('**/api/v1/auth/accounts/telegram/start', async (route) => {
      requests.push('start')
      credentialRef = route.request().postDataJSON().credential_ref
      state = challenge('phone_number', 1, 'phone_number')
      await route.fulfill({ status: 200, json: { data: state } })
    })
    await page.route('**/api/v1/auth/accounts/*/authorization', async (route) => {
      if (route.request().method() === 'GET') {
        await route.fulfill({ status: 200, json: state })
        return
      }
      const submitted = route.request().postDataJSON() as {
        generation: number
        answer: Record<string, string>
      }
      answers.push(submitted.answer)
      state = submitted.generation === 1
        ? challenge('code', 2, 'code')
        : submitted.generation === 2
          ? challenge('password', 3, 'password')
          : {
              credential_ref: credentialRef,
              provider_key: 'telegram',
              status: 'disconnected',
              generation: null,
              challenge: null,
            }
      if (submitted.generation === 3) signInSaved = true
      await route.fulfill({ status: 200, json: { data: state } })
    })
    await page.route('**/api/v1/auth/accounts', async (route) => {
      if (route.request().method() !== 'GET') return route.continue()
      const response = await route.fetch()
      const body = (await response.json()) as { accounts: Array<Record<string, unknown>> }
      for (const account of body.accounts ?? []) {
        if (!signInSaved || account.credential_ref !== credentialRef) continue
        account.status = 'disconnected'
        account.setup_required = false
        account.account = {
          provider_account_id: '246801357',
          display_name: '@e2e_telegram_user',
          metadata_json: { account_kind: 'user' },
        }
        if (testCalls) {
          account.last_tested_at = new Date().toISOString()
          account.last_test = {
            credential_ref: credentialRef,
            provider_key: 'telegram',
            ok: true,
            status: 'ok',
            summary: 'Telegram saved session is authorized and responsive.',
            checked_at: new Date().toISOString(),
            metadata: { account_kind: 'user', proxy_configured: false },
          }
        }
      }
      await route.fulfill({ response, json: body })
    })
    await page.route('**/api/v1/auth/accounts/*/test', async (route) => {
      testCalls += 1
      await route.fulfill({
        status: 200,
        json: {
          data: {
            credential_ref: credentialRef,
            provider_key: 'telegram',
            ok: true,
            status: 'ok',
            summary: 'Telegram saved session is authorized and responsive.',
            checked_at: new Date().toISOString(),
            metadata: { account_kind: 'user', proxy_configured: false },
          },
        },
      })
    })

    await page.addInitScript(() => localStorage.setItem('cs:theme', 'dark'))
    await page.goto('/accounts')
    await page.getByRole('button', { name: 'Add Account' }).click()
    const addPanel = page.getByRole('dialog', { name: 'Add Account' })
    await addPanel.getByRole('combobox', { name: 'Service' }).click()
    await addPanel.getByRole('combobox', { name: 'Search options' }).fill('Telegram')
    await addPanel.getByRole('option', { name: /Telegram/ }).click()
    await addPanel.getByText('User account', { exact: true }).click()
    const panel = page.getByRole('dialog', { name: 'Connect Telegram' })
    await panel.getByLabel('Application API ID').fill('12345')
    await panel.getByLabel('Application API hash').fill('e2e-private-application-hash')
    await panel.getByRole('button', { name: 'Save and continue' }).click()

    await expect(panel.getByRole('heading', { name: 'Enter your phone number' })).toBeVisible()
    expect(await page.content()).not.toContain('e2e-private-application-hash')
    await page.screenshot({ path: '/private/tmp/stackos-telegram-user-phone.png' })
    await panel.getByRole('textbox', { name: 'Phone number' }).fill('+15551234567')
    await panel.getByRole('textbox', { name: 'Phone number' }).press('Enter')

    await expect(panel.getByRole('heading', { name: 'Enter the Telegram code' })).toBeVisible()
    await page.screenshot({ path: '/private/tmp/stackos-telegram-user-code.png' })
    await panel.getByLabel('Telegram code', { exact: true }).fill('842679')
    await panel.getByLabel('Telegram code', { exact: true }).press('Enter')

    await expect(panel.getByRole('heading', { name: 'Enter your two-step verification password' })).toBeVisible()
    expect(await page.content()).not.toContain('842679')
    await page.screenshot({ path: '/private/tmp/stackos-telegram-user-password.png' })
    await panel.getByLabel('Password', { exact: true }).fill('e2e-private-two-step-password')
    await panel.getByLabel('Password', { exact: true }).press('Enter')

    await expect(panel.getByRole('heading', { name: 'Telegram sign-in saved' })).toBeVisible()
    expect(await page.content()).not.toContain('e2e-private-two-step-password')
    await page.screenshot({ path: '/private/tmp/stackos-telegram-user-ready.png' })
    await panel.getByRole('button', { name: 'Done' }).click()
    const row = page.getByRole('list', { name: 'Telegram Accounts' }).getByRole('listitem')
    await expect(row.getByText('Sign-in saved')).toBeVisible()
    await expect(row.getByText('Disconnected', { exact: true })).toBeVisible()
    await expect(row.getByRole('button', { name: 'Manage sign-in' })).toBeVisible()
    await row.getByRole('button', { name: 'Test' }).click()
    await expect(row.getByText('Telegram saved session is authorized and responsive.')).toBeVisible()
    await expect(row).not.toHaveClass(/bg-warning-subtle/)
    await page.screenshot({ path: '/private/tmp/stackos-telegram-user-saved-account.png' })
    expect(testCalls).toBe(1)
    await row.getByRole('button', { name: 'Manage sign-in' }).click()
    await expect(page.getByRole('dialog', { name: 'Edit Telegram Account' })).toContainText(
      'Telegram sign-in saved',
    )
    expect(requests).toEqual(['start'])
    expect(answers).toEqual([
      { phone_number: '+15551234567' },
      { code: '842679' },
      { password: 'e2e-private-two-step-password' },
    ])
    errors.assertNone()
  })

  test('retries failed bot verification locally and keeps the verified Account offline', async ({ page }) => {
    const errors = trackConsoleErrors(page)
    const credentialRef = 'cred_e2e_telegram_bot'
    const calls = { create: 0, retry: 0, test: 0, connect: 0 }
    let created = false
    let verified = false
    const account: Record<string, unknown> = {
      credential_ref: credentialRef,
      provider_key: 'telegram',
      auth_method_key: 'tdlib-bot-token',
      auth_type: 'tdlib-bot-token',
      display_name: 'Telegram bot setup',
      status: 'pending',
      setup_required: true,
      project_ids: [],
      revoked_at: null,
      account: null,
      last_test: null,
      last_tested_at: null,
    }
    page.on('request', (request) => {
      if (request.url().includes('/session/connect')) calls.connect += 1
    })
    await page.route('**/api/v1/auth/accounts/telegram', async (route) => {
      calls.create += 1
      created = true
      await route.fulfill({
        status: 201,
        json: { data: { credential_ref: credentialRef, status: 'pending', setup_required: true } },
      })
    })
    await page.route('**/api/v1/auth/accounts', async (route) => {
      if (route.request().method() !== 'GET') return route.continue()
      const response = await route.fetch()
      const body = (await response.json()) as { accounts: Array<Record<string, unknown>> }
      if (created) body.accounts.push(account)
      await route.fulfill({ response, json: body })
    })
    await page.route('**/api/v1/auth/accounts/*/authorization', async (route) => {
      await route.fulfill({
        status: 200,
        json: {
          credential_ref: credentialRef,
          provider_key: 'telegram',
          status: verified ? 'disconnected' : 'pending',
          generation: null,
          challenge: null,
          repair_hint: verified ? null : 'Telegram could not verify this bot token. Check it and retry.',
        },
      })
    })
    await page.route('**/api/v1/auth/accounts/telegram/start', async (route) => {
      calls.retry += 1
      verified = true
      account.status = 'disconnected'
      account.setup_required = false
      account.account = { provider_account_id: '135792468', display_name: '@e2e_telegram_bot' }
      await route.fulfill({
        status: 200,
        json: {
          data: {
            credential_ref: credentialRef,
            provider_key: 'telegram',
            status: 'disconnected',
            generation: null,
            challenge: null,
          },
        },
      })
    })
    await page.route('**/api/v1/auth/accounts/*/test', async (route) => {
      calls.test += 1
      account.last_tested_at = new Date().toISOString()
      account.last_test = {
        credential_ref: credentialRef,
        provider_key: 'telegram',
        ok: true,
        status: 'ok',
        summary: 'Telegram saved session is authorized and responsive.',
        checked_at: new Date().toISOString(),
        metadata: { account_kind: 'bot' },
      }
      await route.fulfill({ status: 200, json: { data: account.last_test } })
    })

    await page.addInitScript(() => localStorage.setItem('cs:theme', 'dark'))
    await page.goto('/accounts')
    await page.getByRole('button', { name: 'Add Account' }).click()
    const addPanel = page.getByRole('dialog', { name: 'Add Account' })
    await addPanel.getByRole('combobox', { name: 'Service' }).click()
    await addPanel.getByRole('combobox', { name: 'Search options' }).fill('Telegram')
    await addPanel.getByRole('option', { name: /Telegram/ }).click()
    await addPanel.getByText('Bot', { exact: true }).click()
    const panel = page.getByRole('dialog', { name: 'Set up Telegram bot' })
    const progress = panel.getByRole('navigation', { name: 'Telegram Account setup' })
    await expect(progress).toContainText('Account details')
    await expect(progress).toContainText('Verify bot')
    await expect(progress).toContainText('Ready')
    await panel.getByLabel('Application API ID').fill('12345')
    await panel.getByLabel('Application API hash').fill('e2e-private-application-hash')
    await panel.locator('#connection-field-bot_token').fill('12345:e2e-private-bot-token')
    await page.screenshot({ path: '/private/tmp/stackos-telegram-bot-setup.png' })
    await panel.getByRole('button', { name: 'Save and verify' }).click()

    await expect(panel.getByRole('heading', { name: 'Verify Telegram bot' })).toBeVisible()
    await expect(panel.getByText('Telegram could not verify this bot token. Check it and retry.')).toBeVisible()
    await expect(panel.getByRole('button', { name: 'Retry bot verification' })).toBeVisible()
    await page.screenshot({ path: '/private/tmp/stackos-telegram-bot-retry.png' })
    expect(await page.content()).not.toContain('12345:e2e-private-bot-token')
    expect(calls).toEqual({ create: 1, retry: 0, test: 0, connect: 0 })

    await panel.getByRole('button', { name: 'Retry bot verification' }).click()
    await expect(panel.getByRole('heading', { name: 'Telegram bot authorization saved' })).toBeVisible()
    await expect(progress).toContainText('Ready')
    await page.screenshot({ path: '/private/tmp/stackos-telegram-bot-ready.png' })
    await panel.getByRole('button', { name: 'Done' }).click()
    const row = page.getByRole('list', { name: 'Telegram Accounts' }).getByRole('listitem')
    await expect(row.getByText('Disconnected', { exact: true })).toBeVisible()
    await expect(row.getByText('Authorization saved', { exact: true })).toBeVisible()
    await expect(row.getByText('@e2e_telegram_bot')).toBeVisible()
    await expect(row).not.toHaveClass(/bg-warning-subtle/)
    await row.getByRole('button', { name: 'Test' }).click()
    await expect(row.getByText('Telegram saved session is authorized and responsive.')).toBeVisible()
    await page.screenshot({ path: '/private/tmp/stackos-telegram-bot-saved-account.png' })
    expect(calls).toEqual({ create: 1, retry: 1, test: 1, connect: 0 })
    errors.assertNone()
  })

  for (const kind of ['bot', 'user'] as const) {
    test(`${kind} Account edits proxy fields without exposing saved credentials`, async ({
      page,
    }) => {
      const errors = trackConsoleErrors(page)
      const account = await storeAccount({
        providerKey: 'telegram',
        authMethodKey: kind === 'bot' ? 'tdlib-bot-token' : 'tdlib-user-session',
        displayName: `Telegram ${kind} proxy`,
        fields: {
          api_id: 12345,
          api_hash: 'e2e-private-application-hash',
          ...(kind === 'bot' ? { bot_token: '12345:e2e-private-token' } : {}),
          proxy_enabled: true,
          proxy_type: 'socks5',
          proxy_host: 'proxy.example.test',
          proxy_port: 1080,
          proxy_username: 'e2e-private-proxy-user',
          proxy_password: 'e2e-private-proxy-password',
        },
      })
      await page.goto(`/accounts?account=${encodeURIComponent(account.credentialRef)}`)
      await expect(page.getByRole('heading', { name: 'Accounts', level: 1 })).toBeVisible()
      const panel = page.getByRole('dialog', { name: 'Edit Telegram Account' })
      await expect(panel).toBeVisible()
      await panel.getByRole('button', { name: 'Edit details' }).click()
      const proxyDisclosure = panel.getByRole('button', { name: /Proxy settings \(optional\)/ })
      await expect(proxyDisclosure).toBeVisible()
      if ((await proxyDisclosure.getAttribute('aria-expanded')) !== 'true') await proxyDisclosure.click()
      await expect(panel.getByRole('checkbox', { name: 'Use a proxy' })).toBeChecked()
      await expect(panel.getByLabel('Proxy host', { exact: true })).toHaveValue(
        'proxy.example.test',
      )
      await expect(panel.getByLabel('Proxy port', { exact: true })).toHaveValue('1080')
      for (const secret of [
        'e2e-private-application-hash',
        'e2e-private-token',
        'e2e-private-proxy-user',
        'e2e-private-proxy-password',
      ]) {
        expect(await page.content()).not.toContain(secret)
      }
      await panel.getByLabel('Proxy host', { exact: true }).fill('new-proxy.example.test')
      await panel.getByRole('button', { name: 'Save changes' }).click()
      await expect
        .poll(async () => (await getAccountEditState(account.credentialRef)).values.proxy_host)
        .toBe('new-proxy.example.test')
      const saved = await getAccountEditState(account.credentialRef)
      expect(saved.secret_present.proxy_password).toBe(true)
      expect(saved.secret_present.proxy_username).toBe(true)
      errors.assertNone()
      await page.screenshot({ path: `/private/tmp/stackos-telegram-${kind}-proxy.png` })
    })
  }
})
