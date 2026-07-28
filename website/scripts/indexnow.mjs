import { readFile, readdir } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const websiteRoot = join(dirname(fileURLToPath(import.meta.url)), '..')
const outputRoot = join(websiteRoot, '.output', 'public')
const publicRoot = join(websiteRoot, 'public')
const origin = 'https://stackos.flowmonkey.io'
const endpoint = 'https://api.indexnow.org/indexnow'
const submit = process.argv.includes('--submit')

function decodeXml(value) {
  return value
    .replace(/&amp;/g, '&')
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
}

const keyFiles = (await readdir(publicRoot)).filter((name) => /^[a-f0-9]{32}\.txt$/.test(name))
if (keyFiles.length !== 1) {
  throw new Error(`Expected exactly one 32-character IndexNow key file in public/, found ${keyFiles.length}.`)
}

const keyFile = keyFiles[0]
const key = keyFile.replace(/\.txt$/, '')
const keyContent = (await readFile(join(publicRoot, keyFile), 'utf8')).trim()
if (keyContent !== key) throw new Error('IndexNow key filename and file content must match.')

const sitemap = await readFile(join(outputRoot, 'sitemap.xml'), 'utf8')
const urlList = [...sitemap.matchAll(/<loc>([\s\S]*?)<\/loc>/gi)]
  .map((match) => decodeXml(match[1].trim()))
  .filter((value) => value.startsWith(origin))

if (!urlList.length) throw new Error('The generated sitemap contains no canonical StackOS URLs.')
if (urlList.length > 10_000) throw new Error(`IndexNow accepts at most 10,000 URLs per request; found ${urlList.length}.`)
if (new Set(urlList).size !== urlList.length) throw new Error('The generated sitemap contains duplicate URLs.')

for (const value of urlList) {
  const url = new URL(value)
  if (url.origin !== origin) throw new Error(`IndexNow URL uses the wrong origin: ${value}`)
  if (url.pathname !== '/' && !url.pathname.endsWith('/')) {
    throw new Error(`IndexNow URL is not a canonical trailing-slash HTML route: ${value}`)
  }
}

const payload = {
  host: new URL(origin).host,
  key,
  keyLocation: `${origin}/${keyFile}`,
  urlList,
}

if (!submit) {
  console.log(JSON.stringify({
    mode: 'dry-run',
    endpoint,
    urlCount: urlList.length,
    payload,
  }, null, 2))
  process.exit(0)
}

if (process.env.INDEXNOW_ALLOW_SUBMIT !== '1') {
  throw new Error('IndexNow submission denied. Set INDEXNOW_ALLOW_SUBMIT=1 as well as passing --submit.')
}

const response = await fetch(endpoint, {
  method: 'POST',
  headers: { 'content-type': 'application/json; charset=utf-8' },
  body: JSON.stringify(payload),
})
const responseBody = await response.text()
if (!response.ok) {
  throw new Error(`IndexNow returned HTTP ${response.status}: ${responseBody || response.statusText}`)
}

console.log(JSON.stringify({
  mode: 'submitted',
  endpoint,
  urlCount: urlList.length,
  status: response.status,
  responseBody,
}, null, 2))
