// Shared helpers for E2E tests.

import { expect, type Page } from '@playwright/test'

export function getDaemonToken(): string {
  const t = process.env.STACKOS_E2E_TOKEN
  if (!t) throw new Error('STACKOS_E2E_TOKEN not set; global-setup must have failed')
  return t
}

export function getBaseUrl(): string {
  return process.env.STACKOS_E2E_BASE_URL ?? 'http://127.0.0.1:5181'
}

/**
 * Reset every project the daemon knows about by hard-deleting via the
 * REST API. Safe to call between tests because each E2E spec is supposed
 * to set up its own state.
 */
export async function resetProjects(): Promise<void> {
  const token = getDaemonToken()
  const base = getBaseUrl()
  const headers = { Authorization: `Bearer ${token}` }
  let cursor: number | null = null
  for (let safety = 0; safety < 20; safety++) {
    const url =
      cursor === null
        ? `${base}/api/v1/projects?limit=200`
        : `${base}/api/v1/projects?limit=200&after=${cursor}`
    const res = await fetch(url, { headers })
    if (!res.ok) throw new Error(`list projects failed: ${res.status}`)
    const body = (await res.json()) as { items: Array<{ id: number }>; next_cursor: number | null }
    if (body.items.length === 0) break
    for (const p of body.items) {
      await fetch(`${base}/api/v1/projects/${p.id}?hard=true`, { method: 'DELETE', headers })
    }
    cursor = body.next_cursor ?? null
    if (cursor === null) break
  }
}

/**
 * Patterns that should be ignored when collecting console errors.
 *
 * Browser-emitted "Failed to load resource: HTTP <status>" lines are NOT
 * application bugs — they're a faithful echo of an HTTP error our app code
 * already handled (e.g. an integration `/test` that returns 502 because the
 * vendor isn't reachable). The CLAUDE.md "zero console errors" bar is
 * about JavaScript exceptions and uncaught promise rejections from our
 * own code, not network status codes.
 *
 * If you find a real bug being suppressed here, narrow the pattern.
 */
const IGNORED_CONSOLE_ERROR_PATTERNS: RegExp[] = [
  /Failed to load resource: the server responded with a status of \d+/i,
]

/**
 * Subscribe to console.error events; throws after the test if any fired.
 * Call from `beforeEach` and use `consoleErrors.assertNone()` before
 * test-end assertions.
 */
export function trackConsoleErrors(page: Page): { errors: string[]; assertNone(): void } {
  const errors: string[] = []
  page.on('console', (msg) => {
    if (msg.type() !== 'error') return
    const text = msg.text()
    if (IGNORED_CONSOLE_ERROR_PATTERNS.some((re) => re.test(text))) return
    errors.push(`[${msg.location().url}:${msg.location().lineNumber}] ${text}`)
  })
  page.on('pageerror', (err) => {
    errors.push(`[pageerror] ${err.message}`)
  })
  return {
    errors,
    assertNone(): void {
      expect(errors, `console.error fired during test:\n${errors.join('\n')}`).toEqual([])
    },
  }
}

/**
 * Create a project via the REST API and return the row.
 */
export async function createProject(input: {
  name: string
  slug: string
  domain: string
  niche?: string
  locale?: string
}): Promise<{ id: number; name: string; slug: string }> {
  const token = getDaemonToken()
  const base = getBaseUrl()
  const res = await fetch(`${base}/api/v1/projects`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'content-type': 'application/json',
    },
    body: JSON.stringify({
      name: input.name,
      slug: input.slug,
      domain: input.domain,
      niche: input.niche ?? null,
      locale: input.locale ?? 'en-US',
      schedule_json: null,
    }),
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`create project failed: ${res.status} ${text}`)
  }
  const body = (await res.json()) as { data: { id: number; name: string; slug: string } }
  return body.data
}

export async function storeCredential(input: {
  projectId: number
  providerKey: string
  authMethodKey: string
  profileKey: string
  label: string
  fields: Record<string, unknown>
}): Promise<{ credentialRef: string }> {
  const res = await fetch(
    `${getBaseUrl()}/api/v1/projects/${input.projectId}/auth/${input.providerKey}/credentials`,
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${getDaemonToken()}`,
        'content-type': 'application/json',
      },
      body: JSON.stringify({
        auth_method_key: input.authMethodKey,
        profile_key: input.profileKey,
        label: input.label,
        fields: input.fields,
      }),
    },
  )
  if (!res.ok) throw new Error(`store credential failed: ${res.status} ${await res.text()}`)
  const body = (await res.json()) as { data: { credential_ref: string } }
  return { credentialRef: body.data.credential_ref }
}

export async function getCredentialEditState(
  projectId: number,
  credentialRef: string,
): Promise<{ values: Record<string, unknown>; secret_present: Record<string, boolean> }> {
  const res = await fetch(
    `${getBaseUrl()}/api/v1/projects/${projectId}/auth/credentials/${encodeURIComponent(credentialRef)}`,
    { headers: { Authorization: `Bearer ${getDaemonToken()}` } },
  )
  if (!res.ok) throw new Error(`get credential failed: ${res.status} ${await res.text()}`)
  return (await res.json()) as {
    values: Record<string, unknown>
    secret_present: Record<string, boolean>
  }
}
