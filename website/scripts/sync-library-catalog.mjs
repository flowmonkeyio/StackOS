import { mkdir, readdir, readFile, unlink, writeFile } from 'node:fs/promises'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parse } from 'yaml'

const websiteRoot = join(dirname(fileURLToPath(import.meta.url)), '..')
const repoRoot = join(websiteRoot, '..')
const contentRoot = join(websiteRoot, 'content', 'catalog')
const appDataRoot = join(websiteRoot, 'app', 'data')

const domainCopy = {
  branding: { audience: 'Content and brand teams', color: '#d8ff63' },
  communications: { audience: 'Customer and operations teams', color: '#7ee2ad' },
  core: { audience: 'Every team', color: '#8ea6ff' },
  engineering: { audience: 'Product and engineering teams', color: '#7892ff' },
  gtm: { audience: 'Sales and go-to-market teams', color: '#ff9f73' },
  marketing: { audience: 'Marketing teams', color: '#ff78ac' },
  'media-buying': { audience: 'Growth and paid media teams', color: '#ffbd5c' },
  seo: { audience: 'SEO and content teams', color: '#79dfff' },
  support: { audience: 'Support and success teams', color: '#a8e37f' },
}

const publicDescriptions = {
  'branding.content-production': 'Turn an idea into a researched, reviewed piece of content, then prepare it for every channel you choose.',
  'communications.callback-follow-up': 'Continue a customer conversation after they choose an option in a message.',
  'communications.customer-feedback-intake': 'Bring customer feedback from chat and email into one clear, investigation-ready thread.',
  'communications.inbox-review': 'Review incoming messages, identify what needs action, and turn selected items into organized work.',
  'communications.outbound-notification': 'Send an approved update after a task, decision, or result is ready.',
  'communications.rich-telegram-reply': 'Reply in Telegram with text, images, and useful actions while keeping the conversation connected.',
  'core.project-memory-review': 'Review what the project already knows and recommend the most useful next move.',
  'engineering.tracked-delivery': 'Turn a request into a clear plan, complete it step by step, check the result, and keep the proof.',
  'gtm.account-research': 'Build a useful account brief from your saved context and current research.',
  'gtm.crm-hygiene-pass': 'Find incomplete or inconsistent CRM records and prepare safe, reviewed corrections.',
  'gtm.lead-enrichment-scoring': 'Enrich leads with relevant context and score them against your chosen criteria.',
  'gtm.outbound-sequence-preparation': 'Prepare a reviewed outreach sequence from qualified leads and account context.',
  'gtm.pipeline-risk-review': 'Find deals that need attention and turn the risks into clear follow-up actions.',
  'marketing.campaign-production': 'Move from a campaign idea to messaging, creative, landing pages, and a complete review gallery.',
  'media-buying.budget-reallocation-review': 'Review campaign performance and prepare evidence-backed budget changes for approval.',
  'media-buying.campaign-launch': 'Plan and launch a paid campaign through clear checks, approvals, and connected ad tools.',
  'media-buying.creative-variant-generation': 'Create a focused set of ad variations from your offer, brand, and past performance.',
  'media-buying.landing-page-creative-experiment': 'Design a measurable experiment that connects creative, landing pages, and business goals.',
  'media-buying.performance-diagnosis': 'Explain what changed in paid media performance and recommend what to do next.',
  'seo.content-refresh': 'Find why an existing page is underperforming, improve it, and verify the result.',
  'seo.keyword-research': 'Turn a business goal into a prioritized map of search opportunities and content ideas.',
  'support.delivery-task-handoff': 'Turn a completed support investigation into ready-to-start delivery work.',
  'support.issue-investigation': 'Investigate a reported issue, gather the evidence, and return a clear conclusion in the same conversation.',
}

const featuredWorkflowKeys = new Set([
  'branding.content-production',
  'communications.customer-feedback-intake',
  'engineering.tracked-delivery',
  'marketing.campaign-production',
  'media-buying.campaign-launch',
  'seo.keyword-research',
])

const jargon = [
  [/\bcanonical\b/gi, 'main'],
  [/\boperator\b/gi, 'person'],
  [/\brun[- ]plan\b/gi, 'plan'],
  [/\bprovider\b/gi, 'connected app'],
  [/\bartifact\b/gi, 'saved result'],
  [/\bcredential\b/gi, 'login'],
  [/\bdaemon\b/gi, 'StackOS'],
  [/\bexecution\b/gi, 'work'],
  [/\borchestration\b/gi, 'coordination'],
]

function cleanText(value = '') {
  let text = String(value).replace(/\s+/g, ' ').trim()
  for (const [pattern, replacement] of jargon) text = text.replace(pattern, replacement)
  return text
}

function shortText(value, max = 170) {
  const text = cleanText(value)
  if (text.length <= max) return text
  const shortened = text.slice(0, max - 1).replace(/\s+\S*$/, '')
  return `${shortened}…`
}

function slugify(value) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/(^-|-$)/g, '')
}

function humanize(value = '') {
  return value
    .split(/[._-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

async function yamlFiles(dir) {
  const entries = await readdir(dir, { withFileTypes: true })
  const files = []
  for (const entry of entries) {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) files.push(...(await yamlFiles(path)))
    if (entry.isFile() && /\.ya?ml$/.test(entry.name)) files.push(path)
  }
  return files
}

async function readYaml(path) {
  return parse(await readFile(path, 'utf8'))
}

async function writeRecords(kind, records) {
  const dir = join(contentRoot, kind)
  await mkdir(dir, { recursive: true })
  const expectedFiles = new Set(records.map((record) => `${record.slug}.json`))
  await Promise.all(
    records.map((record) =>
      writeFile(join(dir, `${record.slug}.json`), `${JSON.stringify(record, null, 2)}\n`, 'utf8'),
    ),
  )
  const existingFiles = await readdir(dir)
  await Promise.all(
    existingFiles
      .filter((file) => file.endsWith('.json') && !expectedFiles.has(file))
      .map((file) => unlink(join(dir, file))),
  )
}

const workflowFiles = await yamlFiles(join(repoRoot, 'plugins'))
const workflowDocs = []
const agentDocs = []
const skillDocs = []

for (const path of workflowFiles) {
  const pathFromRoot = relative(repoRoot, path)
  if (!/(workflows|agent-presets|skill-presets)/.test(pathFromRoot)) continue
  const document = await readYaml(path)
  if (pathFromRoot.includes('/workflows/') && document?.key) workflowDocs.push(document)
  if (pathFromRoot.includes('/agent-presets/')) agentDocs.push(...(document?.presets || []))
  if (pathFromRoot.includes('/skill-presets/')) skillDocs.push(...(document?.presets || []))
}

const agentNameByKey = new Map(agentDocs.map((agent) => [agent.key, cleanText(agent.name)]))

const workflows = workflowDocs
  .map((workflow) => {
    const domain = workflow.domain || workflow.key.split('.')[0]
    const domainMeta = domainCopy[domain] || { audience: 'Operations teams', color: '#8ea6ff' }
    const description = publicDescriptions[workflow.key] || shortText(workflow.description, 190)
    const stages = (workflow.steps || []).map((step) => ({
      id: step.id,
      title: cleanText(step.title || humanize(step.id)),
      summary: shortText(step.purpose || `Complete the ${humanize(step.id).toLowerCase()} stage.`, 155),
    }))
    const agentNames = (workflow.agent_requirements || [])
      .map((item) => agentNameByKey.get(item.agent_preset_ref) || humanize(item.role))
      .filter(Boolean)
    const agentRefs = (workflow.agent_requirements || [])
      .filter((item) => item.agent_preset_ref)
      .map((item) => ({
        key: item.agent_preset_ref,
        slug: slugify(item.agent_preset_ref),
        name: agentNameByKey.get(item.agent_preset_ref) || humanize(item.role),
      }))
    const integrations = [...new Set((workflow.action_contracts || []).map((item) => humanize(item.provider || item.action)).filter(Boolean))]
    return {
      key: workflow.key,
      slug: slugify(workflow.key),
      name: cleanText(workflow.name),
      description,
      domain,
      audience: domainMeta.audience,
      color: domainMeta.color,
      featured: featuredWorkflowKeys.has(workflow.key),
      whenToUse: [
        `Use this when you want to ${description.charAt(0).toLowerCase()}${description.slice(1).replace(/\.$/, '')}.`,
        `Best for ${domainMeta.audience.toLowerCase()} that want repeatable work without changing the tools they already use.`,
      ],
      stages,
      agentNames,
      agentRefs,
      integrations,
    }
  })
  .sort((a, b) => a.name.localeCompare(b.name))

const agents = agentDocs
  .filter((agent) => agent?.key && agent?.name)
  .map((agent) => {
    const domain = agent.domain || agent.key.split('.')[0]
    const domainMeta = domainCopy[domain] || { audience: 'Operations teams', color: '#8ea6ff' }
    return {
      key: agent.key,
      slug: slugify(agent.key),
      name: cleanText(agent.name),
      description: shortText(agent.description, 185),
      domain,
      audience: domainMeta.audience,
      color: domainMeta.color,
      featured: Boolean(agent.applies_to_workflows?.some((key) => featuredWorkflowKeys.has(key))),
      role: humanize(agent.role || agent.key.split('.').at(-1)),
      workflowKeys: agent.applies_to_workflows || [],
    }
  })
  .sort((a, b) => a.name.localeCompare(b.name))

const orchestrators = skillDocs
  .filter((skill) => skill?.key && skill?.name && skill.skill_type === 'main-agent-orchestration')
  .map((skill) => {
    const domain = skill.domain || skill.key.split('.')[0]
    const domainMeta = domainCopy[domain] || { audience: 'Operations teams', color: '#8ea6ff' }
    const workflowKeys = skill.applies_to_workflows || []
    const relatedAgents = agentDocs
      .filter((agent) => agent.applies_to_workflows?.some((key) => workflowKeys.includes(key)))
      .map((agent) => cleanText(agent.name))
      .slice(0, 8)
    const agentRefs = agentDocs
      .filter((agent) => agent.applies_to_workflows?.some((key) => workflowKeys.includes(key)))
      .map((agent) => ({ key: agent.key, slug: slugify(agent.key), name: cleanText(agent.name) }))
      .slice(0, 8)
    return {
      key: skill.key,
      slug: slugify(skill.key),
      name: cleanText(skill.name).replace('Branding Brand', 'Brand'),
      description: shortText(skill.description, 190),
      domain,
      audience: domainMeta.audience,
      color: domainMeta.color,
      featured: true,
      workflowKeys,
      coordinates: relatedAgents,
      agentRefs,
    }
  })
  .sort((a, b) => a.name.localeCompare(b.name))

await writeRecords('workflows', workflows)
await writeRecords('agents', agents)
await writeRecords('orchestrators', orchestrators)
await mkdir(appDataRoot, { recursive: true })
await writeFile(
  join(appDataRoot, 'library-catalog.generated.json'),
  `${JSON.stringify({ workflows, agents, orchestrators }, null, 2)}\n`,
  'utf8',
)

const articleDir = join(websiteRoot, 'content', 'articles')
const articleFiles = (await readdir(articleDir)).filter((name) => name.endsWith('.md'))
const articleSlugs = new Set(articleFiles.map((name) => name.replace(/\.md$/, '')))
const workflowSlugs = new Set(workflows.map((item) => item.slug))
const agentSlugs = new Set(agents.map((item) => item.slug))
const relationErrors = []

for (const file of articleFiles) {
  const source = await readFile(join(articleDir, file), 'utf8')
  const frontmatterMatch = source.match(/^---\n([\s\S]*?)\n---/)
  if (!frontmatterMatch) {
    relationErrors.push(`${file}: missing YAML frontmatter`)
    continue
  }
  const frontmatter = parse(frontmatterMatch[1])
  const checks = [
    ['relatedWorkflows', frontmatter.relatedWorkflows || [], workflowSlugs],
    ['relatedAgents', frontmatter.relatedAgents || [], agentSlugs],
    ['relatedArticles', frontmatter.relatedArticles || [], articleSlugs],
  ]
  for (const [field, refs, allowed] of checks) {
    for (const ref of refs) {
      if (!allowed.has(ref)) relationErrors.push(`${file}: ${field} references missing item "${ref}"`)
    }
  }
  for (const match of source.matchAll(/article-workflow-visual\{[^}]*workflow="([^"]+)"/g)) {
    if (!workflowSlugs.has(match[1])) relationErrors.push(`${file}: visual references missing workflow "${match[1]}"`)
  }
}

if (relationErrors.length) {
  throw new Error(`Library content validation failed:\n- ${relationErrors.join('\n- ')}`)
}

console.log(`Library catalog: ${workflows.length} workflows, ${agents.length} agents, ${orchestrators.length} orchestrators, ${articleFiles.length} validated articles.`)
