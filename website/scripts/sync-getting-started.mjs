import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const websiteRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const sourcePath = resolve(websiteRoot, 'content/guides/getting-started.md')
const publicPath = resolve(websiteRoot, 'public/getting-started.md')
const checkOnly = process.argv.includes('--check')

const source = await readFile(sourcePath, 'utf8')
let current = null

try {
  current = await readFile(publicPath, 'utf8')
} catch (error) {
  if (error?.code !== 'ENOENT') throw error
}

if (checkOnly) {
  if (current !== source) {
    throw new Error('public/getting-started.md is stale; run pnpm guide:sync')
  }
} else if (current !== source) {
  await mkdir(dirname(publicPath), { recursive: true })
  await writeFile(publicPath, source, 'utf8')
}
