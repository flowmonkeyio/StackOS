import { readFile, writeFile } from 'node:fs/promises'

const args = process.argv.slice(2)
if (args.length < 2) throw new Error('Usage: prepare-keyword-candidates.mjs <ahrefs files...> <output>')

const outputPath = args.at(-1)
const inputPaths = args.slice(0, -1)
const rawByKeyword = new Map()

for (const path of inputPaths) {
  const envelope = JSON.parse(await readFile(path, 'utf8'))
  const competitor = envelope.response?.metadata_json?.competitor || 'unknown'
  for (const row of envelope.response?.output_json?.keywords || []) {
    const keyword = String(row.keyword || '').trim().toLowerCase()
    if (!keyword) continue
    const existing = rawByKeyword.get(keyword)
    const evidence = {
      source: 'ahrefs',
      competitor,
      volume: row.volume ?? null,
      keywordDifficulty: row.keyword_difficulty ?? null,
      cpcCents: row.cpc ?? null,
      bestPosition: row.best_position ?? null,
    }
    if (!existing || (evidence.volume || 0) > (existing.volume || 0)) rawByKeyword.set(keyword, evidence)
  }
}

const candidates = new Map()

function add(keyword, source, topic = 'general') {
  const normalized = String(keyword).trim().toLowerCase().replace(/\s+/g, ' ')
  if (normalized.split(' ').length < 2 || normalized.length > 100) return
  const existing = candidates.get(normalized)
  const evidence = rawByKeyword.get(normalized)
  if (!existing || evidence) candidates.set(normalized, { keyword: normalized, source, topic, evidence })
}

const relevance = /\b(ai|agent|agentic|workflow|automation|orchestrat|llm|mcp|codex|claude|gemini|n8n|gumloop|lindy|relevance ai|relay app|human.in.the.loop|shopify|wordpress|content|marketing|sales|seo|support|finance|crm|customer success)\b/i
const noise = /\b(login|sign in|download free|crack|hostenger|8n8|xodo|replicate ai|dreamstudio|lambda labs)\b/i

for (const [keyword, evidence] of [...rawByKeyword].sort((a, b) => (b[1].volume || 0) - (a[1].volume || 0))) {
  if (relevance.test(keyword) && !noise.test(keyword)) add(keyword, 'ahrefs', 'competitor-organic')
}

const coreTopics = [
  'agentic workflow', 'agentic workflows', 'ai workflow automation', 'ai agent workflow',
  'ai agent orchestration', 'multi agent workflow', 'multi agent system', 'ai agent platform',
  'ai agents for business', 'business ai automation', 'ai workflow management', 'ai workflow software',
  'ai workflow tool', 'ai workflow builder', 'ai agent tools', 'ai agent integrations',
  'ai automation platform', 'ai operations platform', 'ai work orchestration', 'ai process automation',
  'intelligent workflow automation', 'llm workflow', 'llm orchestration', 'mcp workflow',
  'model context protocol workflow', 'human in the loop ai', 'human approval ai workflow',
  'secure ai agents', 'safe ai agents', 'ai agent permissions', 'ai agent audit trail',
  'ai agent monitoring', 'ai agent observability', 'persistent ai agent', 'stateful ai agent',
  'local ai agent', 'on device ai agent', 'ai agent work management', 'ai task orchestration',
  'autonomous ai workflow', 'enterprise ai agents', 'business process ai', 'ai workflow governance',
]

const topicPatterns = [
  (topic) => `what is ${topic}`,
  (topic) => `${topic} examples`,
  (topic) => `${topic} use cases`,
  (topic) => `${topic} benefits`,
  (topic) => `${topic} best practices`,
  (topic) => `${topic} architecture`,
  (topic) => `${topic} tutorial`,
  (topic) => `${topic} guide`,
  (topic) => `how to build ${topic}`,
  (topic) => `how to implement ${topic}`,
  (topic) => `how to manage ${topic}`,
  (topic) => `how to secure ${topic}`,
  (topic) => `best ${topic} tools`,
  (topic) => `${topic} software`,
  (topic) => `${topic} platform`,
  (topic) => `${topic} for small business`,
  (topic) => `${topic} for enterprise`,
  (topic) => `${topic} with human approval`,
]

for (const topic of coreTopics) {
  add(topic, 'curated', 'core')
  for (const pattern of topicPatterns) add(pattern(topic), 'curated', 'core')
}

const departments = [
  'content marketing', 'marketing', 'sales', 'customer support', 'customer success', 'engineering',
  'software development', 'finance', 'accounting', 'ecommerce', 'shopify', 'seo', 'operations',
  'human resources', 'recruiting', 'legal', 'product management', 'project management',
]

for (const department of departments) {
  for (const keyword of [
    `ai workflow for ${department}`,
    `ai agents for ${department}`,
    `${department} workflow automation ai`,
    `${department} agentic workflow`,
    `automate ${department} with ai agents`,
    `best ai tools for ${department} workflows`,
    `ai workflow examples for ${department}`,
    `human in the loop ai for ${department}`,
  ]) add(keyword, 'curated', department)
}

const clients = ['codex', 'claude code', 'gemini cli', 'chatgpt', 'cursor']
const apps = ['slack', 'shopify', 'wordpress', 'hubspot', 'salesforce', 'notion', 'google workspace']

for (const client of clients) {
  for (const keyword of [
    `${client} workflows`, `${client} workflow automation`, `${client} agent workflow`,
    `${client} business automation`, `use ${client} with existing tools`,
    `${client} human approval workflow`, `${client} mcp workflow`, `${client} persistent tasks`,
  ]) add(keyword, 'curated', 'ai-client')
}

for (const app of apps) {
  for (const keyword of [
    `ai agent integration with ${app}`, `${app} ai workflow automation`, `${app} agentic workflow`,
    `ai agents for ${app}`, `human approval workflow ${app}`, `secure ai automation ${app}`,
  ]) add(keyword, 'curated', 'integration')
}

const competitors = ['n8n', 'gumloop', 'lindy ai', 'relevance ai', 'relay app']
for (const competitor of competitors) {
  for (const keyword of [
    `${competitor} alternative`, `${competitor} alternatives`, `${competitor} competitors`,
    `${competitor} review`, `${competitor} pricing`, `${competitor} ai agents`,
    `${competitor} workflow automation`, `${competitor} for business`,
    `${competitor} vs ai agent platform`, `${competitor} vs agentic workflow`,
  ]) add(keyword, 'curated', 'comparison')
}

for (let index = 0; index < competitors.length; index += 1) {
  for (let other = index + 1; other < competitors.length; other += 1) {
    add(`${competitors[index]} vs ${competitors[other]}`, 'curated', 'comparison')
  }
}

const sorted = [...candidates.values()].sort((a, b) => {
  const aEvidence = a.evidence ? 1 : 0
  const bEvidence = b.evidence ? 1 : 0
  if (aEvidence !== bEvidence) return bEvidence - aEvidence
  const volumeDiff = (b.evidence?.volume || 0) - (a.evidence?.volume || 0)
  return volumeDiff || a.keyword.localeCompare(b.keyword)
})

if (sorted.length < 1000) throw new Error(`Only generated ${sorted.length} unique candidates`)

const selected = sorted.slice(0, 1000)
await writeFile(`${outputPath}.keywords.json`, `${JSON.stringify(selected.map((item) => item.keyword))}\n`, 'utf8')
await writeFile(outputPath, `${JSON.stringify({
  generatedAt: new Date().toISOString(),
  requestedCount: 1000,
  inputFiles: inputPaths,
  rawAhrefsUniqueCount: rawByKeyword.size,
  candidatePoolCount: sorted.length,
  keywords: selected.map((item) => item.keyword),
  candidates: selected,
}, null, 2)}\n`, 'utf8')

console.log(`Prepared ${selected.length} candidates from ${rawByKeyword.size} unique Ahrefs rows and ${sorted.length} total candidates.`)
