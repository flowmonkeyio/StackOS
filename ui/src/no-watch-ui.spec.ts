import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = dirname(fileURLToPath(import.meta.url))
const SOURCE_SCOPES = ['App.vue', 'components', 'composables', 'design', 'lib', 'router.ts', 'stores', 'views']
const WATCH_IMPORT_PATTERN =
  /import\s*\{[^}]*\b(?:watch|watchEffect|watchPostEffect|watchSyncEffect)\b[^}]*\}\s*from\s*['"]vue['"]/m
const WATCH_CALL_PATTERN = /\b(?:watch|watchEffect|watchPostEffect|watchSyncEffect)\s*\(/m

function filesUnder(path: string): string[] {
  const stat = statSync(path)
  if (!stat.isDirectory()) return /\.(ts|vue)$/.test(path) && !path.endsWith('.spec.ts') ? [path] : []

  const out: string[] = []
  for (const entry of readdirSync(path)) {
    out.push(...filesUnder(join(path, entry)))
  }
  return out
}

describe('no Vue watcher UI contract', () => {
  it('keeps UI source free of Vue watch APIs', () => {
    const offenders: string[] = []

    for (const scope of SOURCE_SCOPES) {
      for (const file of filesUnder(join(ROOT, scope))) {
        const text = readFileSync(file, 'utf8')
        if (WATCH_IMPORT_PATTERN.test(text) || WATCH_CALL_PATTERN.test(text)) {
          offenders.push(relative(ROOT, file))
        }
      }
    }

    expect(offenders).toEqual([])
  })
})
