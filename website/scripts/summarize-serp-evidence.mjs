import { readFile, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'

const outputPath = resolve(
  process.cwd(),
  '../reports/seo/stackos-serp-evidence-2026-07.json',
)

const sourcePaths = process.argv.slice(2).map(path => resolve(path))

if (!sourcePaths.length) {
  throw new Error('Pass one or more DataForSEO SERP response files.')
}

const compactItem = item => ({
  rank: item.rank_group ?? item.rank_absolute ?? null,
  domain: item.domain ?? null,
  title: item.title ?? null,
  url: item.url ?? null,
  description: item.description ?? null,
})

const textValues = value => {
  if (!value) return []
  if (typeof value === 'string') return [value]
  if (Array.isArray(value)) return value.flatMap(textValues)
  if (typeof value !== 'object') return []

  return [
    value.question,
    value.title,
    value.keyword,
    value.text,
    value.query,
    value.items,
  ].flatMap(textValues)
}

const snapshots = []

for (const sourcePath of sourcePaths) {
  const envelope = JSON.parse(await readFile(sourcePath, 'utf8'))
  const result = envelope.response?.output_json?.tasks?.[0]?.result?.[0]

  if (!result) {
    throw new Error(`No SERP result found in ${sourcePath}`)
  }

  const items = result.items ?? []
  const questions = items
    .filter(item => item.type === 'people_also_ask')
    .flatMap(item => textValues(item.items))
  const relatedSearches = items
    .filter(item => item.type === 'related_searches')
    .flatMap(item => textValues(item.items))

  snapshots.push({
    keyword: result.keyword,
    location: result.location_name,
    language: result.language_code,
    fetchedAt: result.datetime ?? envelope.recorded_at,
    itemTypes: result.item_types ?? [],
    hasAiOverview: (result.item_types ?? []).includes('ai_overview'),
    organicResults: items
      .filter(item => item.type === 'organic')
      .slice(0, 10)
      .map(compactItem),
    peopleAlsoAsk: [...new Set(questions)].slice(0, 12),
    relatedSearches: [...new Set(relatedSearches)].slice(0, 12),
    sourcePath,
    providerCostCents: envelope.response?.cost_cents ?? null,
  })
}

await writeFile(
  outputPath,
  `${JSON.stringify({
    generatedAt: new Date().toISOString(),
    provider: 'dataforseo',
    snapshotCount: snapshots.length,
    snapshots,
  }, null, 2)}\n`,
)

console.log(`Wrote ${snapshots.length} SERP snapshots to ${outputPath}`)
