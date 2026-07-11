import { spawnSync } from 'node:child_process'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const websiteRoot = join(dirname(fileURLToPath(import.meta.url)), '..')
const repoRoot = join(websiteRoot, '..')
const python = join(repoRoot, '.venv', 'bin', 'python')
const projectId = process.env.STACKOS_PROJECT_ID
if (!projectId) throw new Error('STACKOS_PROJECT_ID is required to generate the public integration catalog.')
const outputPath = join(websiteRoot, 'app', 'data', 'integration-catalog.generated.json')
const providerPresentationPath = join(repoRoot, 'provider-presentation.json')
const providerLogoAssets = JSON.parse(await readFile(providerPresentationPath, 'utf8'))

const pluginPresentation = {
  branding: { color: '#ff8a68', description: 'Research, create, review, and prepare brand content across the channels your team uses.' },
  communications: { color: '#7ee2ad', description: 'Connect conversations, inboxes, files, and notifications across chat and email tools.' },
  core: { color: '#8ea6ff', description: 'Use the built-in StackOS services that keep local work, context, and browser tasks connected.' },
  engineering: { color: '#7892ff', description: 'Connect delivery work to code, browsers, tests, and the tools that help teams ship reliably.' },
  gtm: { color: '#ff9f73', description: 'Research accounts, enrich leads, update CRM records, and prepare sales follow-up across your revenue tools.' },
  marketing: { color: '#ff78ac', description: 'Plan and produce campaigns across creative, landing pages, messaging, and distribution tools.' },
  'media-buying': { color: '#ffbd5c', description: 'Plan, launch, measure, and improve paid campaigns across advertising platforms.' },
  publishing: { color: '#b99cff', description: 'Move reviewed content into websites, newsletters, and social channels.' },
  seo: { color: '#79dfff', description: 'Research demand and measure search performance with specialist SEO data sources.' },
  shopify: { color: '#95bf47', description: 'Work with Shopify products, customers, inventory, orders, and reporting.' },
  support: { color: '#a8e37f', description: 'Investigate customer issues, preserve the conversation, and hand verified work into delivery.' },
  trackbooth: { color: '#d9ff63', description: 'Operate Trackbooth accounts, partners, offers, traffic, reporting, and finance through its synced action catalog.' },
  utils: { color: '#9aa7bd', description: 'Reusable research, media generation, data, and productivity tools for any workflow.' },
}

const reviewedProviderLinks = {
  trackbooth: [
    { key: 'homepage_url', label: 'Visit tool', url: 'https://trackbooth.com/', confidence: 'verified' },
    { key: 'platform_url', label: 'See platform', url: 'https://trackbooth.com/platform', confidence: 'verified' },
  ],
}

function slugify(value = '') {
  return String(value)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/(^-|-$)/g, '')
}

function humanize(value = '') {
  return String(value)
    .split(/[._-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function cleanText(value = '') {
  return String(value).replace(/\s+/g, ' ').trim()
}

function requiredText(value, context) {
  const text = cleanText(value)
  if (!text) throw new Error(`Missing required public catalog field: ${context}.`)
  return text
}

function requiredArray(value, context) {
  if (!Array.isArray(value)) throw new Error(`Missing required public catalog collection: ${context}.`)
  return value
}

function runOperation(name, input = {}) {
  const command = spawnSync(
    python,
    ['-m', 'stackos', 'ops', 'call', name, '--project', projectId, '--response-mode', 'raw', '--input', '-'],
    {
      cwd: repoRoot,
      encoding: 'utf8',
      input: JSON.stringify(input),
      maxBuffer: 32 * 1024 * 1024,
    },
  )

  if (command.status !== 0) {
    throw new Error(`StackOS ${name} failed:\n${command.stderr || command.stdout}`)
  }

  try {
    const parsed = JSON.parse(command.stdout)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error('expected a top-level object')
    }
    return parsed
  } catch (error) {
    throw new Error(`StackOS ${name} returned invalid JSON: ${error.message}`)
  }
}

function safeLinks(providerKey, setup = {}) {
  const catalogLinks = [
    { key: 'homepage_url', label: 'Visit tool', url: setup.homepage_url },
    { key: 'docs_url', label: 'Documentation', url: setup.docs_url },
    { key: 'signup_url', label: 'Create account', url: setup.signup_url },
    { key: 'console_url', label: 'Open console', url: setup.console_url },
    { key: 'support_url', label: 'Support', url: setup.support_url },
  ].map((link) => ({ ...link, confidence: setup.url_confidence?.[link.key] || null }))
  const reviewedLinks = Object.hasOwn(reviewedProviderLinks, providerKey) ? reviewedProviderLinks[providerKey] : []
  const candidates = [...reviewedLinks, ...catalogLinks]
  const seenUrls = new Set()
  const seenKinds = new Set()
  return candidates.filter((link) => {
    if (!link.url || !/^https:\/\//i.test(link.url) || /localhost|127\.0\.0\.1/i.test(link.url)) return false
    if (seenUrls.has(link.url) || seenKinds.has(link.key)) return false
    seenUrls.add(link.url)
    seenKinds.add(link.key)
    return true
  })
}

function actionName(name, providerName) {
  return cleanText(name).replace(new RegExp(`^${providerName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}:\\s*`, 'i'), '')
}

function actionDescription(value) {
  const text = cleanText(value)
  if (!text.includes('|')) return text
  return cleanText(text.split('|').at(-1))
}

const catalog = runOperation('catalog.list')
const catalogPlugins = requiredArray(catalog.plugins, 'catalog.plugins')
const providerKeyCounts = new Map()

for (const entry of catalogPlugins) {
  for (const provider of requiredArray(entry.providers, `${entry.plugin?.slug}.providers`)) {
    providerKeyCounts.set(provider.key, (providerKeyCounts.get(provider.key) || 0) + 1)
  }
}

const providers = []
const plugins = []

for (const entry of catalogPlugins) {
  const plugin = entry.plugin
  const pluginSlug = plugin.slug
  const presentation = pluginPresentation[pluginSlug]
  if (!presentation) throw new Error(`Missing required public presentation metadata for plugin "${pluginSlug}".`)
  const { color } = presentation
  const capabilities = requiredArray(entry.capabilities, `${pluginSlug}.capabilities`)
  const capabilityNames = new Map(capabilities.map((capability) => [
    requiredText(capability.key, `${pluginSlug}.capability.key`),
    requiredText(capability.name, `${pluginSlug}.${capability.key}.capability.name`),
  ]))
  const pluginProviders = []

  for (const provider of requiredArray(entry.providers, `${pluginSlug}.providers`)) {
    const providerCatalogKey = `${pluginSlug}.${provider.key}`
    const logo = providerLogoAssets[providerCatalogKey] ?? null
    if (logo) {
      const relativeLogoPath = logo.src.replace(/^\//, '')
      const websiteLogoPath = join(websiteRoot, 'public', relativeLogoPath)
      const uiLogoPath = join(repoRoot, 'ui', 'public', relativeLogoPath)
      let websiteLogo
      let uiLogo
      try {
        ;[websiteLogo, uiLogo] = await Promise.all([
          readFile(websiteLogoPath),
          readFile(uiLogoPath),
        ])
      } catch {
        throw new Error(
          `Mapped integration logo must exist in website/public and ui/public: ${providerCatalogKey} -> ${logo.src}`,
        )
      }
      if (!websiteLogo.equals(uiLogo)) {
        throw new Error(
          `Mapped integration logo differs between website/public and ui/public: ${providerCatalogKey} -> ${logo.src}`,
        )
      }
    }
    const duplicateKey = providerKeyCounts.get(provider.key) > 1
    const slug = slugify(duplicateKey ? `${pluginSlug}-${provider.key}` : provider.key)
    const actions = requiredArray(entry.actions, `${pluginSlug}.actions`)
      .filter((action) => action.provider_key === provider.key && !action.config_json?.trackbooth_removed_action)
      .map((action) => {
        const capabilityName = capabilityNames.get(action.capability_key)
        if (!capabilityName) throw new Error(`Action "${action.action_ref}" references unknown capability "${action.capability_key}".`)
        return {
          key: requiredText(action.action_ref, `${pluginSlug}.${provider.key}.action.key`),
          name: requiredText(actionName(action.name, provider.name), `${action.action_ref}.name`),
          description: requiredText(actionDescription(action.description), `${action.action_ref}.description`),
          keywords: requiredText(action.description, `${action.action_ref}.keywords`),
          capability: requiredText(action.capability_key, `${action.action_ref}.capability`),
          capabilityName,
          risk: requiredText(action.risk_level, `${action.action_ref}.risk`),
        }
      })
      .sort((a, b) => a.name.localeCompare(b.name))
    const setup = provider.config_json?.setup || {}
    const links = safeLinks(provider.key, setup)
    const capabilities = [...new Set(actions.map((action) => action.capabilityName))].sort()
    const providerItem = {
      key: providerCatalogKey,
      slug,
      providerKey: provider.key,
      name: requiredText(provider.name, `${pluginSlug}.${provider.key}.name`),
      description: requiredText(provider.description, `${pluginSlug}.${provider.key}.description`),
      authType: provider.auth_type,
      pluginSlug,
      pluginName: requiredText(plugin.name, `${pluginSlug}.name`),
      pluginDescription: presentation.description,
      color,
      logo,
      actionCount: actions.length,
      capabilities,
      actions,
      links,
      primaryUrl: links[0]?.url || null,
      docsUrl: links.find((link) => link.key === 'docs_url')?.url || null,
      setupNote: cleanText(setup.setup_note),
    }
    providers.push(providerItem)
    pluginProviders.push(providerItem)
  }

  if (pluginProviders.length) {
    plugins.push({
      slug: pluginSlug,
      name: requiredText(plugin.name, `${pluginSlug}.name`),
      description: presentation.description,
      color,
      providerCount: pluginProviders.length,
      actionCount: pluginProviders.reduce((total, provider) => total + provider.actionCount, 0),
      capabilityCount: capabilities.length,
      providerSlugs: pluginProviders.map((provider) => provider.slug),
      providerNames: pluginProviders.map((provider) => provider.name),
    })
  }
}

providers.sort((a, b) => a.name.localeCompare(b.name))
plugins.sort((a, b) => a.name.localeCompare(b.name))

const trackboothProvider = providers.find((provider) => provider.providerKey === 'trackbooth')
if (!trackboothProvider) throw new Error('The synced Trackbooth provider is required in the public integration catalog.')

const generated = {
  generatedAt: new Date().toISOString(),
  source: 'StackOS catalog.list after Trackbooth catalog sync',
  counts: {
    providers: providers.length,
    plugins: plugins.length,
    actions: providers.reduce((total, provider) => total + provider.actionCount, 0),
    trackboothActions: trackboothProvider.actionCount,
  },
  plugins,
  providers,
}

await mkdir(dirname(outputPath), { recursive: true })
await writeFile(outputPath, `${JSON.stringify(generated, null, 2)}\n`, 'utf8')

console.log(
  `Integration catalog: ${generated.counts.providers} providers across ${generated.counts.plugins} plugins, ${generated.counts.actions} actions (${generated.counts.trackboothActions} Trackbooth).`,
)
