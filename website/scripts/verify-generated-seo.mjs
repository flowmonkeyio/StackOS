import { createHash } from 'node:crypto'
import { existsSync } from 'node:fs'
import { readFile, readdir } from 'node:fs/promises'
import { dirname, extname, join, relative, sep } from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const websiteRoot = join(dirname(fileURLToPath(import.meta.url)), '..')
const outputRoot = join(websiteRoot, '.output', 'public')
const publicRoot = join(websiteRoot, 'public')
const siteOrigin = 'https://stackos.flowmonkey.io'
const githubUrl = 'https://github.com/flowmonkeyio/StackOS'
const legacyIntegrationRoutes = new Map([
  ['/library/integrations/plugins/core/', '/library/integrations/local-daemon/'],
  ['/library/integrations/plugins/shopify/', '/library/integrations/shopify/'],
  ['/library/integrations/plugins/trackbooth/', '/library/integrations/trackbooth/'],
])
const topProviderSlugs = [
  'trackbooth',
  'hubspot',
  'shopify',
  'linear',
  'meta-ads',
  'telegram-bot',
  'google-ads',
  'slack-bot',
  'taboola',
  'outbrain',
]
const publicAgentFields = [
  'mission',
  'responsibilities',
  'must_do',
  'must_not_do',
  'handoff_inputs',
  'handoff_outputs',
  'success_criteria',
]
const violations = new Map()

function addViolation(id, detail) {
  const items = violations.get(id) || []
  items.push(detail)
  violations.set(id, items)
}

function decodeHtml(value = '') {
  return String(value)
    .replace(/&#x([0-9a-f]+);/gi, (_, code) => String.fromCodePoint(Number.parseInt(code, 16)))
    .replace(/&#([0-9]+);/g, (_, code) => String.fromCodePoint(Number.parseInt(code, 10)))
    .replace(/&quot;/g, '"')
    .replace(/&#39;|&apos;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&')
}

function stripTags(value = '') {
  return decodeHtml(
    String(value)
      .replace(/<script\b[\s\S]*?<\/script>/gi, ' ')
      .replace(/<style\b[\s\S]*?<\/style>/gi, ' ')
      .replace(/<[^>]+>/g, ' ')
      .replace(/\s+/g, ' ')
      .trim(),
  )
}

function normalizeText(value = '') {
  return stripTags(value).replace(/\s+/g, ' ').trim()
}

function attributes(tag = '') {
  const result = {}
  for (const match of tag.matchAll(/([:@\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')/g)) {
    result[match[1].toLowerCase()] = decodeHtml(match[2] ?? match[3] ?? '')
  }
  return result
}

function tags(html, name) {
  return [...html.matchAll(new RegExp(`<${name}\\b[^>]*>`, 'gi'))].map((match) => match[0])
}

function metaValue(head, key) {
  const wanted = key.toLowerCase()
  for (const tag of tags(head, 'meta')) {
    const attrs = attributes(tag)
    if ((attrs.name || attrs.property || '').toLowerCase() === wanted) return attrs.content || ''
  }
  return ''
}

function canonicalValues(head) {
  return tags(head, 'link')
    .map(attributes)
    .filter((attrs) => (attrs.rel || '').toLowerCase().split(/\s+/).includes('canonical'))
    .map((attrs) => attrs.href || '')
}

function titleValue(head) {
  const match = head.match(/<title\b[^>]*>([\s\S]*?)<\/title>/i)
  return normalizeText(match?.[1] || '')
}

function routeFromHtmlFile(path) {
  const rel = relative(outputRoot, path).split(sep).join('/')
  if (rel === 'index.html') return '/'
  return `/${rel.replace(/\/index\.html$/, '')}/`
}

function isCanonicalHtmlPath(pathname) {
  if (pathname === '/') return true
  const last = pathname.split('/').filter(Boolean).at(-1) || ''
  return !extname(last) && pathname.endsWith('/')
}

function canonicalUrl(pathname) {
  return new URL(pathname, `${siteOrigin}/`).toString()
}

function sha256(value) {
  return createHash('sha256').update(value).digest('hex')
}

async function walkFiles(root) {
  if (!existsSync(root)) return []
  const entries = await readdir(root, { withFileTypes: true })
  const files = []
  for (const entry of entries) {
    const path = join(root, entry.name)
    if (entry.isDirectory()) files.push(...(await walkFiles(path)))
    else files.push(path)
  }
  return files
}

async function readIfExists(path) {
  return existsSync(path) ? readFile(path, 'utf8') : ''
}

function xmlTag(block, name) {
  const match = block.match(new RegExp(`<${name}>([\\s\\S]*?)<\\/${name}>`, 'i'))
  return decodeHtml(match?.[1]?.trim() || '')
}

function collectJsonLd(html, route) {
  const nodes = []
  for (const match of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)) {
    const attrs = attributes(match[1])
    if ((attrs.type || '').toLowerCase() !== 'application/ld+json') continue
    let value
    try {
      value = JSON.parse(match[2])
    } catch {
      try {
        value = JSON.parse(decodeHtml(match[2]))
      } catch (error) {
        addViolation('SEO_JSON_LD_VALID', `${route}: ${error.message}`)
        continue
      }
    }
    const visit = (item) => {
      if (Array.isArray(item)) return item.forEach(visit)
      if (!item || typeof item !== 'object') return
      if (item['@type']) nodes.push(item)
      if (Array.isArray(item['@graph'])) item['@graph'].forEach(visit)
    }
    visit(value)
  }
  return nodes
}

function nodeHasType(node, type) {
  const types = Array.isArray(node?.['@type']) ? node['@type'] : [node?.['@type']]
  return types.includes(type)
}

function valuesWithDataAttribute(html, attribute) {
  const values = []
  const pattern = new RegExp(`<[^>]+\\b${attribute}(?:=(?:"[^"]*"|'[^']*'))?[^>]*>([\\s\\S]*?)<\\/[^>]+>`, 'gi')
  for (const match of html.matchAll(pattern)) values.push(normalizeText(match[1]))
  return values
}

function frontmatterValue(source, key) {
  const frontmatter = source.match(/^---\r?\n([\s\S]*?)\r?\n---/)
  if (!frontmatter) return ''
  const match = frontmatter[1].match(new RegExp(`^${key}:\\s*['"]?([^'"\\r\\n]+)['"]?\\s*$`, 'm'))
  return match?.[1]?.trim() || ''
}

if (!existsSync(outputRoot)) {
  console.error(`Generated output is missing: ${outputRoot}`)
  console.error('Run pnpm --dir website generate first.')
  process.exit(2)
}

const allOutputFiles = await walkFiles(outputRoot)
const htmlFiles = allOutputFiles.filter((path) => path.endsWith(`${sep}index.html`) || path === join(outputRoot, 'index.html'))
const htmlByRoute = new Map()
const jsonLdByRoute = new Map()

for (const path of htmlFiles) {
  const route = routeFromHtmlFile(path)
  const html = await readFile(path, 'utf8')
  htmlByRoute.set(route, html)

  const head = html.match(/<head\b[^>]*>([\s\S]*?)<\/head>/i)?.[1] || ''
  const expectedCanonical = canonicalUrl(route)
  const canonicals = canonicalValues(head)
  if (canonicals.length !== 1 || canonicals[0] !== expectedCanonical) {
    addViolation(
      'SEO_CANONICAL_TRAILING_SLASH',
      `${route}: expected ${expectedCanonical}; found ${canonicals.join(', ') || 'none'}`,
    )
  }

  const documentTitle = titleValue(head)
  const ogTitle = metaValue(head, 'og:title')
  const twitterTitle = metaValue(head, 'twitter:title')
  if (!documentTitle || documentTitle !== ogTitle || documentTitle !== twitterTitle) {
    addViolation(
      'SEO_TITLE_SOCIAL_PARITY',
      `${route}: title=${JSON.stringify(documentTitle)} og=${JSON.stringify(ogTitle)} twitter=${JSON.stringify(twitterTitle)}`,
    )
  }
  if (/(?:\||—)\s*StackOS(?:\s*(?:\||—)\s*StackOS)+$/i.test(documentTitle)) {
    addViolation('SEO_TITLE_SINGLE_BRAND', `${route}: ${documentTitle}`)
  }

  const description = metaValue(head, 'description')
  const ogDescription = metaValue(head, 'og:description')
  const twitterDescription = metaValue(head, 'twitter:description')
  if (!description || description !== ogDescription || description !== twitterDescription) {
    addViolation(
      'SEO_DESCRIPTION_SOCIAL_PARITY',
      `${route}: description/social descriptions differ or are missing`,
    )
  }
  if (metaValue(head, 'og:url') !== expectedCanonical) {
    addViolation('SEO_OG_URL_CANONICAL', `${route}: ${metaValue(head, 'og:url') || 'missing'}`)
  }

  if (/<style\b/i.test(html)) {
    addViolation('SEO_EXTERNAL_STYLES', `${route}: contains inline <style> output`)
  }
  const stylesheetLinks = tags(head, 'link')
    .map(attributes)
    .filter((attrs) => (attrs.rel || '').toLowerCase().split(/\s+/).includes('stylesheet'))
    .map((attrs) => attrs.href || '')
  if (!stylesheetLinks.some((href) => /^\/_nuxt\/.+\.css(?:\?|$)/.test(href))) {
    addViolation('SEO_EXTERNAL_STYLES', `${route}: no hashed /_nuxt/*.css stylesheet`)
  }

  for (const tag of tags(html, 'a')) {
    const href = attributes(tag).href
    if (!href || /^(?:#|mailto:|tel:|javascript:)/i.test(href)) continue
    let url
    try {
      url = new URL(href, `${siteOrigin}${route}`)
    } catch {
      continue
    }
    if (url.origin !== siteOrigin) continue
    if (/^\/(?:_nuxt|__|api)\//.test(url.pathname)) continue
    if (!isCanonicalHtmlPath(url.pathname)) {
      const last = url.pathname.split('/').filter(Boolean).at(-1) || ''
      if (!extname(last)) addViolation('SEO_INTERNAL_LINK_TRAILING_SLASH', `${route} -> ${href}`)
    }
  }

  if (/\b1 providers\b/i.test(stripTags(html))) {
    addViolation('SEO_SINGULAR_PLURAL', `${route}: renders "1 providers"`)
  }

  jsonLdByRoute.set(route, collectJsonLd(html, route))
}

const notFoundHtml = await readIfExists(join(outputRoot, '404.html'))
const notFoundHead = notFoundHtml.match(/<head\b[^>]*>([\s\S]*?)<\/head>/i)?.[1] || ''
const notFoundTitle = titleValue(notFoundHead)
if (
  !notFoundTitle
  || !/\|\s*StackOS$/i.test(notFoundTitle)
  || /(?:\||—)\s*StackOS(?:\s*(?:\||—)\s*StackOS)+$/i.test(notFoundTitle)
  || metaValue(notFoundHead, 'robots') !== 'noindex, nofollow'
  || canonicalValues(notFoundHead).length
) {
  addViolation('SEO_ERROR_METADATA', '404.html must have one brand suffix, noindex/nofollow, and no canonical')
}

const expectedIndexableRoutes = new Set(
  [...htmlByRoute.keys()].filter((route) => !legacyIntegrationRoutes.has(route)),
)

for (const legacyRoute of legacyIntegrationRoutes.keys()) {
  if (htmlByRoute.has(legacyRoute)) {
    addViolation('SEO_LEGACY_ROUTE_REMOVED', `${legacyRoute}: generated an index.html`)
  }
}

const sitemapPath = join(outputRoot, 'sitemap.xml')
const sitemap = await readIfExists(sitemapPath)
if (!sitemap) addViolation('SEO_SITEMAP_PRESENT', 'sitemap.xml is missing')
const sitemapEntries = [...sitemap.matchAll(/<url>([\s\S]*?)<\/url>/gi)].map((match) => ({
  loc: xmlTag(match[1], 'loc'),
  lastmod: xmlTag(match[1], 'lastmod'),
  priority: xmlTag(match[1], 'priority'),
}))
const sitemapLocs = new Set()
const expectedLastmod = new Map()

const guideSource = await readIfExists(join(websiteRoot, 'content', 'guides', 'getting-started.md'))
const guideUpdatedAt = frontmatterValue(guideSource, 'updatedAt')
if (guideUpdatedAt) expectedLastmod.set('/getting-started/', guideUpdatedAt)
const articleRoot = join(websiteRoot, 'content', 'articles')
for (const path of (await walkFiles(articleRoot)).filter((item) => item.endsWith('.md'))) {
  const source = await readFile(path, 'utf8')
  const updatedAt = frontmatterValue(source, 'updatedAt')
  const slug = relative(articleRoot, path).split(sep).join('/').replace(/\.md$/, '')
  if (updatedAt) expectedLastmod.set(`/library/articles/${slug}/`, updatedAt)
}

for (const entry of sitemapEntries) {
  let url
  try {
    url = new URL(entry.loc)
  } catch {
    addViolation('SEO_SITEMAP_CANONICAL_SET', `Invalid loc: ${entry.loc}`)
    continue
  }
  if (url.origin !== siteOrigin || !isCanonicalHtmlPath(url.pathname)) {
    addViolation('SEO_SITEMAP_TRAILING_SLASH', entry.loc)
  }
  if (sitemapLocs.has(entry.loc)) addViolation('SEO_SITEMAP_UNIQUE', entry.loc)
  sitemapLocs.add(entry.loc)
  if (!expectedIndexableRoutes.has(url.pathname)) {
    addViolation('SEO_SITEMAP_CANONICAL_SET', `Unexpected loc: ${entry.loc}`)
  }
  if (!entry.priority || !Number.isFinite(Number(entry.priority)) || Number(entry.priority) < 0 || Number(entry.priority) > 1) {
    addViolation('SEO_SITEMAP_PRIORITY', `${entry.loc}: ${entry.priority || 'missing'}`)
  }
  const sourceDate = expectedLastmod.get(url.pathname)
  if (sourceDate && entry.lastmod !== sourceDate) {
    addViolation('SEO_SITEMAP_LASTMOD_TRUTH', `${entry.loc}: expected ${sourceDate}; found ${entry.lastmod || 'missing'}`)
  }
  if (!sourceDate && entry.lastmod) {
    addViolation('SEO_SITEMAP_LASTMOD_TRUTH', `${entry.loc}: unsupported ${entry.lastmod}`)
  }
}

for (const route of expectedIndexableRoutes) {
  const loc = canonicalUrl(route)
  if (!sitemapLocs.has(loc)) addViolation('SEO_SITEMAP_CANONICAL_SET', `Missing loc: ${loc}`)
}

const feedPath = join(outputRoot, 'feed.xml')
const feed = await readIfExists(feedPath)
if (!feed) {
  addViolation('SEO_FEED_CONTRACT', 'feed.xml is missing')
} else {
  const xmlCheck = spawnSync('xmllint', ['--noout', feedPath], { encoding: 'utf8' })
  if (xmlCheck.status !== 0) addViolation('SEO_FEED_CONTRACT', xmlCheck.stderr.trim() || 'xmllint failed')
  for (const match of feed.matchAll(new RegExp(`${siteOrigin.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}[^<\\s]+`, 'g'))) {
    const value = decodeHtml(match[0])
    const url = new URL(value)
    if (!isCanonicalHtmlPath(url.pathname)) addViolation('SEO_FEED_CONTRACT', `Noncanonical URL: ${value}`)
  }
  if (sitemap.includes(`${siteOrigin}/feed.xml`)) addViolation('SEO_FEED_CONTRACT', 'feed.xml is in sitemap')
}

const allGeneratedMarkup = [...htmlByRoute.values(), sitemap, feed].join('\n')
for (const [legacyRoute] of legacyIntegrationRoutes) {
  if (allGeneratedMarkup.includes(legacyRoute)) {
    addViolation('SEO_LEGACY_ROUTE_REMOVED', `${legacyRoute}: survives in generated HTML/XML/JSON-LD`)
  }
}
if (!allGeneratedMarkup.includes('/library/integrations/plugins/linear/')) {
  addViolation('SEO_LINEAR_RETAINED', 'Linear plugin route is not linked in generated output')
}

const homeHtml = htmlByRoute.get('/') || ''
const homeNodes = jsonLdByRoute.get('/') || []
const organizations = homeNodes.filter((node) => nodeHasType(node, 'Organization'))
const softwareApps = homeNodes.filter((node) => nodeHasType(node, 'SoftwareApplication'))
const faqPages = homeNodes.filter((node) => nodeHasType(node, 'FAQPage'))
const questions = homeNodes.filter((node) => nodeHasType(node, 'Question'))
if (organizations.length !== 1) {
  addViolation('SEO_HOME_SCHEMA', `Expected one Organization; found ${organizations.length}`)
} else {
  const sameAs = Array.isArray(organizations[0].sameAs) ? organizations[0].sameAs : [organizations[0].sameAs]
  if (!sameAs.includes(githubUrl)) addViolation('SEO_HOME_SCHEMA', `Organization sameAs is missing ${githubUrl}`)
}
if (softwareApps.length !== 1) addViolation('SEO_HOME_SCHEMA', `Expected one SoftwareApplication; found ${softwareApps.length}`)
if (faqPages.length !== 1) addViolation('SEO_HOME_SCHEMA', `Expected one FAQPage; found ${faqPages.length}`)
const visibleQuestions = valuesWithDataAttribute(homeHtml, 'data-faq-question')
const visibleAnswers = valuesWithDataAttribute(homeHtml, 'data-faq-answer')
const schemaQuestions = questions.map((node) => normalizeText(node.name || node.question || ''))
const schemaAnswers = questions.map((node) => {
  const answer = node.acceptedAnswer || node.answer
  return normalizeText(typeof answer === 'string' ? answer : answer?.text || '')
})
if (
  !visibleQuestions.length
  || JSON.stringify(visibleQuestions) !== JSON.stringify(schemaQuestions)
  || JSON.stringify(visibleAnswers) !== JSON.stringify(schemaAnswers)
) {
  addViolation(
    'SEO_HOME_FAQ_PARITY',
    `visible=${visibleQuestions.length}/${visibleAnswers.length} schema=${schemaQuestions.length}/${schemaAnswers.length}`,
  )
}

const integrationCatalog = JSON.parse(await readFile(join(websiteRoot, 'app', 'data', 'integration-catalog.generated.json'), 'utf8'))
const integrationIndex = htmlByRoute.get('/library/integrations/') || ''
for (const provider of integrationCatalog.providers) {
  const path = `/library/integrations/${provider.slug}/`
  if (!integrationIndex.includes(`href="${path}"`)) {
    addViolation('SEO_PROVIDER_FLAT_DISCOVERY', `${provider.slug}: direct SSR anchor missing`)
  }
}
for (const slug of topProviderSlugs) {
  const provider = integrationCatalog.providers.find((item) => item.slug === slug)
  const html = htmlByRoute.get(`/library/integrations/${slug}/`) || ''
  if (!provider || !html.includes(`data-provider-facts="${slug}"`)) {
    addViolation('SEO_PROVIDER_FACT_ENRICHMENT', `${slug}: fact section missing`)
    continue
  }
  if (provider.setupNote && !normalizeText(html).includes(normalizeText(provider.setupNote))) {
    addViolation('SEO_PROVIDER_FACT_ENRICHMENT', `${slug}: setup note is not rendered`)
  }
}

const libraryCatalog = JSON.parse(await readFile(join(websiteRoot, 'app', 'data', 'library-catalog.generated.json'), 'utf8'))
for (const agent of libraryCatalog.agents) {
  for (const field of publicAgentFields) {
    const value = agent[field]
    if (field === 'mission' ? !String(value || '').trim() : !Array.isArray(value) || !value.length) {
      addViolation('SEO_AGENT_PUBLIC_ALLOWLIST', `${agent.slug}: ${field} is missing or empty`)
    }
  }
  for (const key of Object.keys(agent)) {
    if (/prompt|credential|secret|auth|hidden|system_context/i.test(key)) {
      addViolation('SEO_AGENT_PUBLIC_ALLOWLIST', `${agent.slug}: forbidden field ${key}`)
    }
  }
  const html = htmlByRoute.get(`/library/agents/${agent.slug}/`) || ''
  if (!html.includes(`data-agent-contract="${agent.slug}"`)) {
    addViolation('SEO_AGENT_FACT_ENRICHMENT', `${agent.slug}: public contract section missing`)
  } else if (agent.mission && !normalizeText(html).includes(normalizeText(agent.mission))) {
    addViolation('SEO_AGENT_FACT_ENRICHMENT', `${agent.slug}: mission is not rendered`)
  }
}

const linearHtml = htmlByRoute.get('/library/integrations/plugins/linear/') || ''
if (!/\b1 provider\b/i.test(stripTags(linearHtml)) || /\b1 providers\b/i.test(stripTags(linearHtml))) {
  addViolation('SEO_SINGULAR_PLURAL', 'Linear plugin page does not render "1 provider"')
}

const workflowHtml = htmlByRoute.get('/library/workflows/branding-content-production/') || ''
const providerHtml = htmlByRoute.get('/library/integrations/trackbooth/') || ''
if (/vue-flow/i.test(workflowHtml)) addViolation('SEO_SSR_DIAGRAMS', 'Workflow detail output still depends on Vue Flow')
if (/vue-flow/i.test(providerHtml)) addViolation('SEO_SSR_DIAGRAMS', 'Provider detail output still depends on Vue Flow')

const nuxtConfig = await readFile(join(websiteRoot, 'nuxt.config.ts'), 'utf8')
if (!/inlineStyles:\s*false/.test(nuxtConfig)) addViolation('SEO_EXTERNAL_STYLES', 'nuxt.config.ts does not set inlineStyles: false')
if (/['"]@vue-flow\/core\/dist\/(?:style|theme-default)\.css['"]/.test(nuxtConfig)) {
  addViolation('SEO_VUE_FLOW_CSS_SCOPE', 'Vue Flow CSS is still global in nuxt.config.ts')
}

const htaccess = await readFile(join(publicRoot, '.htaccess'), 'utf8')
const requiredHeaderFragments = [
  'Strict-Transport-Security',
  'max-age=31536000',
  'X-Content-Type-Options',
  'nosniff',
  'Referrer-Policy',
  'strict-origin-when-cross-origin',
  'Permissions-Policy',
  'X-Frame-Options',
  'DENY',
]
for (const fragment of requiredHeaderFragments) {
  if (!htaccess.includes(fragment)) addViolation('SEO_SECURITY_HEADERS', `Missing ${fragment}`)
}
if (/Strict-Transport-Security[^\n]*preload/i.test(htaccess)) {
  addViolation('SEO_SECURITY_HEADERS', 'HSTS must not include preload')
}
if (!/DirectorySlash\s+On/i.test(htaccess)) addViolation('SEO_HOST_ROUTE_POLICY', 'DirectorySlash On is missing')
if (!/feed\.xml[\s\S]{0,300}X-Robots-Tag[\s\S]{0,80}noindex/i.test(htaccess)) {
  addViolation('SEO_FEED_NOINDEX', 'feed.xml X-Robots-Tag noindex rule is missing')
}
for (const [source, target] of legacyIntegrationRoutes) {
  const sourceWithoutEdges = source.replace(/^\/|\/$/g, '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const targetEscaped = target.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  if (!new RegExp(`RedirectMatch\\s+301\\s+\\^/${sourceWithoutEdges}/\\?\\$\\s+${targetEscaped}`).test(htaccess)) {
    addViolation('SEO_HOST_ROUTE_POLICY', `${source} -> ${target} exact 301 is missing`)
  }
}

const keyFiles = (await readdir(publicRoot)).filter((name) => /^[a-f0-9]{32}\.txt$/.test(name))
if (keyFiles.length !== 1) {
  addViolation('SEO_INDEXNOW_KEY', `Expected one 32-hex public key file; found ${keyFiles.length}`)
} else {
  const key = keyFiles[0].replace(/\.txt$/, '')
  const keyContent = (await readFile(join(publicRoot, keyFiles[0]), 'utf8')).trim()
  const generatedKeyContent = (await readIfExists(join(outputRoot, keyFiles[0]))).trim()
  if (keyContent !== key || generatedKeyContent !== key) {
    addViolation('SEO_INDEXNOW_KEY', `${keyFiles[0]} filename/content/output mismatch`)
  }
}

const packageJson = JSON.parse(await readFile(join(websiteRoot, 'package.json'), 'utf8'))
const indexNowScriptPath = join(websiteRoot, 'scripts', 'indexnow.mjs')
const indexNowSource = await readIfExists(indexNowScriptPath)
if (!indexNowSource) {
  addViolation('SEO_INDEXNOW_GUARDS', 'scripts/indexnow.mjs is missing')
} else {
  if (!indexNowSource.includes('--submit') || !indexNowSource.includes('INDEXNOW_ALLOW_SUBMIT')) {
    addViolation('SEO_INDEXNOW_GUARDS', 'IndexNow script lacks both explicit submit gates')
  }
  const dryRun = spawnSync(process.execPath, [indexNowScriptPath], {
    cwd: websiteRoot,
    encoding: 'utf8',
    env: { ...process.env, INDEXNOW_ALLOW_SUBMIT: '' },
  })
  if (dryRun.status !== 0 || !/"mode"\s*:\s*"dry-run"/.test(dryRun.stdout)) {
    addViolation('SEO_INDEXNOW_GUARDS', `Dry run failed: ${dryRun.stderr.trim() || dryRun.stdout.trim()}`)
  }
  const deniedSubmit = spawnSync(process.execPath, [indexNowScriptPath, '--submit'], {
    cwd: websiteRoot,
    encoding: 'utf8',
    env: { ...process.env, INDEXNOW_ALLOW_SUBMIT: '' },
  })
  if (deniedSubmit.status === 0) addViolation('SEO_INDEXNOW_GUARDS', '--submit succeeded without INDEXNOW_ALLOW_SUBMIT=1')
}
if (!packageJson.scripts?.['indexnow:dry-run'] || !packageJson.scripts?.['indexnow:submit']) {
  addViolation('SEO_INDEXNOW_LIFECYCLE', 'IndexNow package scripts are missing')
}
for (const name of ['predev', 'prebuild', 'pregenerate', 'build', 'generate', 'test:e2e', 'test:seo:generated']) {
  if (/indexnow/i.test(packageJson.scripts?.[name] || '')) {
    addViolation('SEO_INDEXNOW_LIFECYCLE', `${name} must not invoke IndexNow`)
  }
}

const deploymentDoc = await readFile(join(websiteRoot, 'DEPLOYMENT.md'), 'utf8')
const contentDoc = await readFile(join(websiteRoot, 'CONTENT_OPERATIONS.md'), 'utf8')
for (const term of ['trailing slash', 'IndexNow', 'X-Robots-Tag', 'post-deploy']) {
  if (!deploymentDoc.toLowerCase().includes(term.toLowerCase())) {
    addViolation('SEO_DOCUMENTATION', `DEPLOYMENT.md is missing ${term}`)
  }
}
for (const term of ['lastmod', 'canonical', 'source date']) {
  if (!contentDoc.toLowerCase().includes(term.toLowerCase())) {
    addViolation('SEO_DOCUMENTATION', `CONTENT_OPERATIONS.md is missing ${term}`)
  }
}

const orderedIds = [
  'SEO_CANONICAL_TRAILING_SLASH',
  ...[...violations.keys()].filter((id) => id !== 'SEO_CANONICAL_TRAILING_SLASH').sort(),
]
const violationCount = [...violations.values()].reduce((total, items) => total + items.length, 0)

if (violationCount) {
  console.error(`SEO_GENERATED_CONTRACT_FAILED violations=${violationCount} rules=${violations.size}`)
  for (const id of orderedIds) {
    const items = violations.get(id)
    if (!items?.length) continue
    console.error(`${id} count=${items.length}`)
    for (const detail of items.slice(0, 8)) console.error(`  - ${detail}`)
    if (items.length > 8) console.error(`  - … ${items.length - 8} more`)
  }
  process.exit(1)
}

console.log(JSON.stringify({
  status: 'pass',
  generatedRoutes: htmlByRoute.size,
  sitemapUrls: sitemapEntries.length,
  providers: integrationCatalog.providers.length,
  agents: libraryCatalog.agents.length,
  sitemapSha256: sha256(sitemap),
}, null, 2))
