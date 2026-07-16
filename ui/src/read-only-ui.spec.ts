import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = dirname(fileURLToPath(import.meta.url))
const SCOPES = ['components', 'lib', 'stores', 'views']
const WRITE_PATTERNS = [/\bapiWrite\b/, /method:\s*['"`](POST|PATCH|PUT|DELETE)['"`]/]
const AUTH_SETUP_STORE = join(ROOT, 'stores', 'plugins.ts')
const PROJECTS_STORE = join(ROOT, 'stores', 'projects.ts')
const OPERATIONS_CLIENT = join(ROOT, 'lib', 'operations.ts')
const ALLOWED_WRITE_FILES = new Set([AUTH_SETUP_STORE, PROJECTS_STORE, OPERATIONS_CLIENT])

function filesUnder(dir: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry)
    const stat = statSync(path)
    if (stat.isDirectory()) out.push(...filesUnder(path))
    else if (/\.(ts|vue)$/.test(entry) && !entry.endsWith('.spec.ts')) out.push(path)
  }
  return out
}

describe('restricted UI write contract', () => {
  it('keeps product views and stores free of non-auth write calls', () => {
    const offenders: string[] = []
    for (const scope of SCOPES) {
      for (const file of filesUnder(join(ROOT, scope))) {
        const text = readFileSync(file, 'utf8')
        if (
          WRITE_PATTERNS.some((pattern) => pattern.test(text)) &&
          !ALLOWED_WRITE_FILES.has(file)
        ) {
          offenders.push(file)
        }
      }
    }
    expect(offenders).toEqual([])
  })

  it('limits the auth setup store writes to provider credential controls', () => {
    const text = readFileSync(AUTH_SETUP_STORE, 'utf8')
    const methodBlocks = [...text.matchAll(/apiFetch<[\s\S]*?\n\s*\)/g)]
      .map((match) => match[0])
      .filter((block) => /method:\s*['"`](POST|PATCH|PUT|DELETE)['"`]/.test(block))

    expect(methodBlocks.length).toBe(5)
    expect(methodBlocks.map((block) => block.match(/method:\s*'([^']+)'/)?.[1])).toEqual([
      'POST',
      'PATCH',
      'POST',
      'POST',
      'POST',
    ])
    expect(methodBlocks.map((block) => block.match(/`([^`]+)`/)?.[1])).toEqual([
      '/api/v1/projects/${projectId}/auth/${providerKey}/credentials',
      '/api/v1/projects/${projectId}/auth/credentials/${encodeURIComponent(credentialRef)}',
      '/api/v1/projects/${projectId}/auth/${providerKey}/start',
      '/api/v1/projects/${projectId}/auth/test',
      '/api/v1/projects/${projectId}/auth/revoke',
    ])
  })

  it('limits project store writes to first-project creation', () => {
    const text = readFileSync(PROJECTS_STORE, 'utf8')

    expect([...text.matchAll(/method:\s*['"`](POST|PATCH|PUT|DELETE)['"`]/g)]).toHaveLength(1)
    expect(text).toContain("apiFetch<SchemaWriteResponseProjectOut>('/api/v1/projects'")
    expect(text).toContain("method: 'POST'")
  })

  it('limits operation transport writes to the generic operation-call helper', () => {
    const text = readFileSync(OPERATIONS_CLIENT, 'utf8')

    expect([...text.matchAll(/method:\s*['"`](POST|PATCH|PUT|DELETE)['"`]/g)]).toHaveLength(1)
    expect(text).toContain('`/api/v1/operations/${operationName}/call`')
    expect(text).toContain("method: 'POST'")
  })
})
