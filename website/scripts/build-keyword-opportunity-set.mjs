import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname } from 'node:path'

const [candidatePath, dataForSeoPath, outputBase] = process.argv.slice(2)
if (!candidatePath || !dataForSeoPath || !outputBase) {
  throw new Error('Usage: build-keyword-opportunity-set.mjs <candidates.json> <dataforseo.json> <output-base>')
}

const candidatesDoc = JSON.parse(await readFile(candidatePath, 'utf8'))
const dataForSeoEnvelope = JSON.parse(await readFile(dataForSeoPath, 'utf8'))
const dataForSeoRows = dataForSeoEnvelope.response?.output_json?.tasks?.[0]?.result || []
const metricsByKeyword = new Map(dataForSeoRows.map((row) => [String(row.keyword).trim().toLowerCase(), row]))

const excludedExact = new Set([
  'ai app', 'ai apps', 'ai software', 'ai tools', 'ai websites', 'best ai', 'free ai tools',
  'manus ai', 'sintra ai', 'replicate ai', 'dreamstudio ai', 'lambda labs',
  'ai assistant', 'ai assistants', 'ai assistance', 'best ai apps', 'best ai app',
  'ai app builder', 'generative ai platforms', 'conversational ai platforms',
  'ai text generator', 'best ai tools', 'best ai tool', 'free ai tool', 'ai tool',
  'what is the best ai',
])

function classify(keyword) {
  if (/\b(vs|alternative|alternatives|competitors|pricing|review)\b/.test(keyword)) return 'comparisons'
  if (/\b(codex|claude code|gemini cli|chatgpt|cursor|slack|shopify|wordpress|hubspot|salesforce|notion|google workspace|integration)\b/.test(keyword)) return 'ai-clients-and-integrations'
  if (/\b(content marketing|marketing|sales|customer support|customer success|engineering|software development|finance|accounting|ecommerce|seo|operations|human resources|recruiting|legal|product management|project management)\b/.test(keyword)) return 'department-workflows'
  if (/\b(secure|safe|permission|audit|human in the loop|human approval|governance)\b/.test(keyword)) return 'governance-and-safety'
  if (/\b(local|on device|persistent|stateful|observability|monitoring)\b/.test(keyword)) return 'local-state-and-observability'
  if (/\b(orchestrat|multi agent|multi-agent|mcp|model context protocol|llm)\b/.test(keyword)) return 'orchestration-and-infrastructure'
  if (/\b(platform|software|tool|builder|enterprise|small business|business)\b/.test(keyword)) return 'agent-platforms'
  return 'agentic-workflow-fundamentals'
}

function intent(keyword) {
  if (/\b(vs|alternative|alternatives|competitors|pricing|review)\b/.test(keyword)) return 'comparison'
  if (/^(what is|how |how to)|\b(guide|tutorial|examples|use cases|benefits|best practices|architecture)\b/.test(keyword)) return 'informational'
  if (/\b(best|software|platform|tools|builder|solutions?)\b/.test(keyword)) return 'commercial'
  if (/\b(for|automate|automation|workflow)\b/.test(keyword)) return 'solution'
  return 'exploratory'
}

function funnel(searchIntent) {
  if (searchIntent === 'comparison' || searchIntent === 'commercial') return 'decision'
  if (searchIntent === 'solution') return 'consideration'
  return 'awareness'
}

function audience(keyword, cluster) {
  if (/\b(codex|claude code|gemini cli|mcp|llm|engineering|software development|architecture|observability)\b/.test(keyword)) return 'technical evaluators'
  if (cluster === 'department-workflows') return 'department leaders and operators'
  if (/\b(enterprise|governance|audit|permission|secure)\b/.test(keyword)) return 'operations, security, and IT leaders'
  if (/\b(small business|business)\b/.test(keyword)) return 'business owners and operations teams'
  return 'cross-functional AI evaluators'
}

function destination(keyword, cluster, searchIntent) {
  if (/\b(codex|claude code|gemini cli|existing tools|integration)\b/.test(keyword)) return '/library/articles/use-codex-claude-gemini-with-existing-tools'
  if (/\b(safe|secure|permission|account|login|audit trail|human approval)\b/.test(keyword)) return '/library/articles/how-ai-agents-use-accounts-safely'
  if (/\b(agent vs|workflow vs|orchestrator vs|difference between)\b/.test(keyword)) return '/library/articles/ai-agent-vs-workflow-vs-orchestrator'
  if (/\b(what is agentic|agentic workflow guide|agentic workflow examples|agentic workflow use cases)\b/.test(keyword)) return '/library/articles/what-is-an-agentic-workflow'
  if (cluster === 'department-workflows') return '/library/workflows'
  if (cluster === 'comparisons') return 'new comparison guide'
  if (cluster === 'governance-and-safety') return 'new safety and governance guide'
  if (cluster === 'orchestration-and-infrastructure') return '/library/orchestrators'
  if (searchIntent === 'commercial') return '/library'
  return 'new practical guide'
}

function answerFormat(searchIntent) {
  if (searchIntent === 'comparison') return 'direct verdict plus criteria table'
  if (searchIntent === 'commercial') return 'shortlist with fit, tradeoffs, and proof'
  if (searchIntent === 'solution') return 'use-case answer plus visible workflow'
  return 'direct definition followed by steps, examples, and sources'
}

function score(item, metrics) {
  const keyword = item.keyword
  const volume = Number(metrics?.search_volume || 0)
  const cpc = Number(metrics?.cpc || 0)
  const words = keyword.split(' ').length
  let value = Math.log10(volume + 1) * 12 + Math.min(cpc, 25) * 0.8
  if (item.evidence) value += 8 + Math.log10(Number(item.evidence.volume || 0) + 1) * 5
  if (/\b(agentic workflow|ai workflow automation|ai agent workflow|ai agent orchestration|ai agents for business|human in the loop ai)\b/.test(keyword)) value += 62
  if (/\b(examples|use cases|best practices|how to|what is|best|platform|software|alternative|vs)\b/.test(keyword)) value += 10
  if (words >= 3 && words <= 7) value += 8
  if (words > 9) value -= 10
  if (/^what is .+s$/.test(keyword) || /^best what is/.test(keyword)) value -= 16
  if (item.source === 'curated' && volume === 0) value += 3
  return Math.round(value * 100) / 100
}

const enriched = candidatesDoc.candidates
  .filter((item) => !excludedExact.has(item.keyword))
  .filter((item) => item.source === 'curated' || /\b(agentic|ai agent|ai workflow|workflow automation|orchestrat|multi agent|mcp|codex|claude|gemini|human in the loop|human approval|secure ai|safe ai|persistent ai|stateful ai|local ai|n8n|gumloop|lindy ai|relevance ai|relay app)\b/.test(item.keyword))
  .filter((item) => !/^what is .+s$/.test(item.keyword) && !/^best what is/.test(item.keyword))
  .map((item) => {
    const metrics = metricsByKeyword.get(item.keyword) || {}
    const cluster = classify(item.keyword)
    const searchIntent = intent(item.keyword)
    return {
      keyword: item.keyword,
      cluster,
      audience: audience(item.keyword, cluster),
      intent: searchIntent,
      funnelStage: funnel(searchIntent),
      searchVolume: metrics.search_volume ?? 0,
      cpc: metrics.cpc ?? null,
      competition: metrics.competition ?? null,
      competitionIndex: metrics.competition_index ?? null,
      ahrefsVolume: item.evidence?.volume ?? null,
      ahrefsDifficulty: item.evidence?.keywordDifficulty ?? null,
      ahrefsCpcCents: item.evidence?.cpcCents ?? null,
      ahrefsBestPosition: item.evidence?.bestPosition ?? null,
      ahrefsCompetitor: item.evidence?.competitor ?? null,
      source: item.evidence ? 'ahrefs + dataforseo' : 'curated + dataforseo',
      recommendedDestination: destination(item.keyword, cluster, searchIntent),
      answerFormat: answerFormat(searchIntent),
      opportunityScore: score(item, metrics),
    }
  })
  .sort((a, b) => b.opportunityScore - a.opportunityScore || b.searchVolume - a.searchVolume || a.keyword.localeCompare(b.keyword))

const caps = {
  'agentic-workflow-fundamentals': 105,
  'orchestration-and-infrastructure': 75,
  'governance-and-safety': 65,
  'department-workflows': 100,
  'ai-clients-and-integrations': 65,
  comparisons: 45,
  'agent-platforms': 65,
  'local-state-and-observability': 45,
}

const selected = []
const selectedKeywords = new Set()
const counts = new Map()
for (const item of enriched) {
  if (selected.length >= 500) break
  const count = counts.get(item.cluster) || 0
  if (count >= (caps[item.cluster] || 60)) continue
  selected.push(item)
  selectedKeywords.add(item.keyword)
  counts.set(item.cluster, count + 1)
}
for (const item of enriched) {
  if (selected.length >= 500) break
  if (selectedKeywords.has(item.keyword)) continue
  selected.push(item)
  selectedKeywords.add(item.keyword)
  counts.set(item.cluster, (counts.get(item.cluster) || 0) + 1)
}

if (selected.length !== 500) throw new Error(`Expected 500 selected keywords, got ${selected.length}`)

selected.forEach((item, index) => {
  item.rank = index + 1
  item.priority = index < 100 ? 'high' : index < 300 ? 'medium' : 'exploratory'
})

const clusterSummary = Object.fromEntries([...counts].sort(([a], [b]) => a.localeCompare(b)))
const volumeSummary = {
  withMeasuredDemand: selected.filter((item) => item.searchVolume > 0).length,
  zeroVolumeLongTail: selected.filter((item) => item.searchVolume === 0).length,
  totalSearchVolume: selected.reduce((sum, item) => sum + item.searchVolume, 0),
}
const generatedAt = new Date().toISOString()
const data = {
  schemaVersion: 'stackos.keyword-opportunity-set.v1',
  title: 'StackOS 500-keyword opportunity library',
  status: 'researched',
  generatedAt,
  market: { location: 'United States', language: 'English' },
  keywordCount: selected.length,
  clusterSummary,
  volumeSummary,
  methodology: {
    providers: ['Ahrefs', 'DataForSEO'],
    candidateCount: candidatesDoc.keywords.length,
    ahrefsCompetitors: ['n8n.io', 'gumloop.com', 'lindy.ai', 'relevanceai.com', 'relay.app'],
    selection: 'Balanced opportunity score using demand, CPC, Ahrefs evidence, specificity, product relevance, audience intent, and cluster caps.',
    seo: 'One primary intent and destination per keyword; avoid cannibalization by assigning clusters before drafting.',
    aeo: 'Lead with a direct answer matching answerFormat, then support it with steps, examples, and proof.',
    geo: 'Use explicit entities, definitions, workflow stages, source-backed claims, and internally linked evidence that answer engines can cite.',
  },
  evidenceRefs: {
    ahrefs: candidatesDoc.inputFiles,
    dataforseo: dataForSeoPath,
  },
  keywords: selected,
}

function csvCell(value) {
  if (value === null || value === undefined) return ''
  const text = String(value)
  return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text
}

const csvFields = [
  'rank', 'keyword', 'priority', 'cluster', 'audience', 'intent', 'funnelStage',
  'searchVolume', 'cpc', 'competition', 'competitionIndex', 'ahrefsVolume',
  'ahrefsDifficulty', 'ahrefsBestPosition', 'ahrefsCompetitor', 'source',
  'recommendedDestination', 'answerFormat', 'opportunityScore',
]
const csv = [csvFields.join(','), ...selected.map((row) => csvFields.map((field) => csvCell(row[field])).join(','))].join('\n')
const topRows = selected.slice(0, 30).map((item) => `| ${item.rank} | ${item.keyword} | ${item.searchVolume} | ${item.cluster} | ${item.recommendedDestination} |`).join('\n')
const markdown = `# StackOS 500-keyword opportunity library\n\nGenerated ${generatedAt.slice(0, 10)} for United States English search intent.\n\n## Coverage\n\n- 500 selected keywords from 1,000 measured candidates\n- ${volumeSummary.withMeasuredDemand} terms with measured Google Ads demand\n- ${volumeSummary.zeroVolumeLongTail} evidence-led long-tail or answer-engine topics\n- Providers: Ahrefs and DataForSEO\n\n## Cluster mix\n\n${Object.entries(clusterSummary).map(([cluster, count]) => `- ${cluster}: ${count}`).join('\n')}\n\n## Top opportunities\n\n| Rank | Keyword | Volume | Cluster | Recommended destination |\n| ---: | --- | ---: | --- | --- |\n${topRows}\n\nThe complete set, metrics, audience, intent, answer format, and destination mapping are in the adjacent CSV and JSON files.\n`

await mkdir(dirname(outputBase), { recursive: true })
await Promise.all([
  writeFile(`${outputBase}.json`, `${JSON.stringify(data, null, 2)}\n`, 'utf8'),
  writeFile(`${outputBase}.csv`, `${csv}\n`, 'utf8'),
  writeFile(`${outputBase}.md`, markdown, 'utf8'),
])

console.log(JSON.stringify({ keywordCount: selected.length, clusterSummary, volumeSummary, top: selected.slice(0, 10) }, null, 2))
