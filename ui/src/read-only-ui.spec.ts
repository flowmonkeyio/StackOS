import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = dirname(fileURLToPath(import.meta.url))
const SCOPES = ['components', 'lib', 'stores', 'views']
const WRITE_PATTERNS = [
  /\bapiWrite\b/,
  /method:\s*['"`](POST|PATCH|PUT|DELETE)['"`]/,
]
const AUTH_SETUP_STORE = join(ROOT, 'stores', 'plugins.ts')

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
        if (WRITE_PATTERNS.some((pattern) => pattern.test(text)) && file !== AUTH_SETUP_STORE) {
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

    expect(methodBlocks.length).toBe(4)
    expect(methodBlocks.every((block) => /method:\s*'POST'/.test(block))).toBe(true)
    expect(methodBlocks.map((block) => block.match(/`([^`]+)`/)?.[1])).toEqual([
      '/api/v1/projects/${projectId}/auth/${providerKey}/credentials',
      '/api/v1/projects/${projectId}/auth/${providerKey}/start',
      '/api/v1/projects/${projectId}/auth/test',
      '/api/v1/projects/${projectId}/auth/revoke',
    ])
  })
})
