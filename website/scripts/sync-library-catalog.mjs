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

function cleanText(value = '') {
  return String(value).replace(/\s+/g, ' ').trim()
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
const catalogErrors = []

for (const workflow of workflowDocs) {
  if (!workflow.experience) catalogErrors.push(`${workflow.key}: missing experience contract`)
  if (!workflow.public) catalogErrors.push(`${workflow.key}: missing public catalog contract`)
  for (const field of ['problem', 'outcome']) {
    if (!cleanText(workflow.experience?.[field])) catalogErrors.push(`${workflow.key}: missing experience.${field}`)
  }
  for (const field of ['operator_path', 'agent_path']) {
    if (!workflow.experience?.[field]?.length) catalogErrors.push(`${workflow.key}: missing experience.${field}`)
  }
  if (!cleanText(workflow.public?.audience)) catalogErrors.push(`${workflow.key}: missing public.audience`)
  if (!workflow.public?.setup) catalogErrors.push(`${workflow.key}: missing public.setup`)
  if (!workflow.public?.prerequisites?.length) catalogErrors.push(`${workflow.key}: missing public.prerequisites`)
  if (!workflow.public?.proof?.length) catalogErrors.push(`${workflow.key}: missing public.proof`)
}

for (const agent of agentDocs) {
  if (!['reasoning', 'mechanical', 'review'].includes(agent.role_class)) {
    catalogErrors.push(`${agent.key}: missing or invalid role_class`)
  }
}

if (catalogErrors.length) {
  throw new Error(`Library source contracts are incomplete:\n- ${catalogErrors.join('\n- ')}`)
}

const workflows = workflowDocs
  .map((workflow) => {
    const domain = workflow.domain || workflow.key.split('.')[0]
    const domainMeta = domainCopy[domain] || { audience: 'Operations teams', color: '#8ea6ff' }
    const experience = workflow.experience
    const publicMeta = workflow.public
    const description = shortText(experience.outcome, 190)
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
      audience: cleanText(publicMeta.audience),
      color: domainMeta.color,
      featured: Boolean(publicMeta.featured),
      problem: cleanText(experience.problem),
      outcome: cleanText(experience.outcome),
      whyAi: cleanText(experience.why_ai),
      operatorPath: (experience.operator_path || []).map(cleanText),
      agentPath: (experience.agent_path || []).map(cleanText),
      progressSignals: (experience.progress_signals || []).map(cleanText),
      recovery: (experience.recovery || []).map(cleanText),
      safeStoppingPoints: (experience.safe_stopping_points || []).map(cleanText),
      handoffs: (experience.handoffs || []).map((item) => ({
        workflowKey: item.workflow_key,
        relationship: item.relationship,
        when: cleanText(item.when),
      })),
      setup: publicMeta.setup,
      prerequisites: (publicMeta.prerequisites || []).map(cleanText),
      proof: (publicMeta.proof || []).map(cleanText),
      whenToUse: (workflow.when_to_use || []).map(cleanText),
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
      featured: Boolean(agent.applies_to_workflows?.some((key) => workflows.find((workflow) => workflow.key === key)?.featured)),
      role: humanize(agent.role || agent.key.split('.').at(-1)),
      roleClass: agent.role_class,
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
      featured: Boolean(workflowKeys.some((key) => workflows.find((workflow) => workflow.key === key)?.featured)),
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
