import type {
  SchemaFinanceComparisonInput,
  SchemaFinanceQueryInput,
  SchemaFinanceSnapshotOut,
} from '@/api'

export type FinanceQuery = Pick<
  SchemaFinanceQueryInput,
  'start' | 'end' | 'recognition_basis' | 'date_basis' | 'timezone'
> &
  Partial<SchemaFinanceQueryInput>
export type SnapshotRef = Pick<SchemaFinanceSnapshotOut, 'revision' | 'sha256'>
export function object(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}
export function objects(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(object) : []
}
export function text(value: unknown): string {
  return typeof value === 'string' ? value : ''
}
export function refs(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : []
}
export function snapshotRef(value: SnapshotRef): SnapshotRef {
  return { revision: value.revision, sha256: value.sha256 }
}

export type DocumentKind = 'invoice' | 'receipt' | 'unidentified'
export function documentKind(value: unknown): DocumentKind {
  return value === 'invoice' || value === 'receipt' ? value : 'unidentified'
}
export function documentName(value: Record<string, unknown>): string {
  return text(value.filename) || 'Unnamed original'
}
export function documentSummaryText(value: unknown): string {
  const summary = object(value)
  const count = typeof summary.document_count === 'number' ? summary.document_count : 0
  const labels: Record<string, string> = {
    available: 'Available',
    unavailable: 'Unavailable',
    not_verified: 'Not verified',
    not_linked: 'Not linked',
    unidentified: 'Needs identification',
  }
  if (summary.state === 'partial') return `Partial · ${summary.available_count}/${count} available`
  const label = labels[text(summary.state)] || 'Not verified'
  return count ? `${label} · ${count}` : label
}
export function documentCountsText(value: unknown): string {
  const summary = object(value)
  if (typeof summary.document_count !== 'number')
    return 'Document availability has not been verified. Refresh to inspect current originals.'
  if (!summary.document_count)
    return summary.state === 'unidentified'
      ? 'Linked files need identification; inspect the unidentified originals below.'
      : 'No original of this type is linked.'
  return `${summary.document_count} linked · ${summary.available_count} available · ${summary.unavailable_count} unavailable · ${summary.not_verified_count} not verified`
}
export function documentGroups(evidence: unknown, summary: unknown) {
  const documents = objects(evidence)
  const counts = object(summary)
  return (['invoice', 'receipt', 'unidentified'] as const).map((kind) => ({
    kind,
    label:
      kind === 'invoice' ? 'Invoice' : kind === 'receipt' ? 'Receipt' : 'Unidentified documents',
    documents: documents.filter((document) => documentKind(document.document_type) === kind),
    status:
      kind === 'unidentified'
        ? documents.some((document) => documentKind(document.document_type) === kind)
          ? 'Needs identification'
          : 'None listed'
        : documentSummaryText(counts[kind]),
    counts: kind === 'unidentified' ? '' : documentCountsText(counts[kind]),
  }))
}
export function canReadDocument(document: Record<string, unknown>): boolean {
  return (
    Boolean(text(document.attachment_ref)) &&
    document.availability !== 'unavailable' &&
    ![document.retention_state, document.state].some((state) =>
      ['unavailable', 'quarantined', 'disposed', 'missing', 'changed', 'unreadable'].includes(
        text(state),
      ),
    )
  )
}
export function documentAvailabilityText(document: Record<string, unknown>): string {
  if (document.availability === 'available') return 'Available · verified in this snapshot'
  if (document.availability !== 'unavailable') return 'Not verified · verify before opening'
  if (document.reason === 'quarantined') return 'Unavailable · original is quarantined'
  if (document.reason === 'disposed') return 'Unavailable · original was disposed'
  return 'Unavailable · original could not be verified; refresh to inspect current custody'
}

function precision(exponent: unknown): exponent is number {
  return (
    typeof exponent === 'number' && Number.isInteger(exponent) && exponent >= 0 && exponent <= 6
  )
}
export function minorToDecimal(minor: string, exponent: number): string {
  if (!precision(exponent) || !/^-?\d+$/.test(minor))
    throw new Error('Confirm currency precision and amount.')
  const negative = minor.startsWith('-')
  const digits = BigInt(minor)
    .toString()
    .replace('-', '')
    .padStart(exponent + 1, '0')
  return `${negative && BigInt(minor) !== 0n ? '-' : ''}${exponent ? `${digits.slice(0, -exponent)}.${digits.slice(-exponent)}` : digits}`
}
export function decimalToMinor(value: string, exponent: number | null): string {
  if (!precision(exponent)) throw new Error('Confirm currency precision before changing an amount.')
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value.trim())
  if (!match || (match[3]?.length ?? 0) > exponent)
    throw new Error('Use an exact decimal amount with the selected decimal places.')
  return BigInt(`${match[1]}${match[2]}${(match[3] ?? '').padEnd(exponent, '0')}`).toString()
}
export function formatMoney(value: unknown): string {
  const money = object(value)
  const minor = text(money.minor) || text(money.amount_minor)
  const currency = text(money.currency)
  if (!minor || !/^-?\d+$/.test(minor) || !currency) return 'Unavailable'
  if (!precision(money.exponent)) return `${currency} ${minor} minor units (precision unavailable)`
  const decimal = minorToDecimal(minor, money.exponent)
  const [whole, fraction] = decimal.replace('-', '').split('.')
  return `${currency} ${decimal.startsWith('-') ? '−' : ''}${whole!.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}${fraction !== undefined ? `.${fraction}` : ''}`
}
export function metricText(value: unknown): string {
  const metric = object(value)
  return metric.state === 'unavailable' || !metric.amount
    ? 'Unavailable'
    : formatMoney(metric.amount)
}
function iso(date: Date): string {
  return date.toISOString().slice(0, 10)
}
function day(value: string): Date {
  const date = new Date(`${value}T12:00:00Z`)
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || !Number.isFinite(date.getTime()) || iso(date) !== value)
    throw new Error('Choose valid dates.')
  return date
}
export function timeframe(preset: string, now = new Date()): { start: string; end: string } {
  if (preset === 'all') return { start: '0001-01-01', end: '9999-12-31' }
  const year = now.getUTCFullYear(),
    month = now.getUTCMonth()
  if (preset === 'year') return { start: `${year}-01-01`, end: iso(now) }
  const start = new Date(Date.UTC(year, month - (preset === 'last-month' ? 1 : 0), 1, 12))
  const end = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + 1, 0, 12))
  return { start: iso(start), end: iso(end) }
}
export function comparisonWindow(
  start: string,
  end: string,
  mode: string,
): SchemaFinanceComparisonInput | null {
  if (mode === 'none') return null
  const first = day(start),
    last = day(end)
  if (first > last) throw new Error('Start must precede end.')
  if (mode === 'period') {
    const span = last.getTime() - first.getTime() + 86400000
    const a = new Date(first.getTime() - span),
      b = new Date(first.getTime() - 86400000)
    if (a.getUTCFullYear() < 1)
      throw new Error('No previous supported period exists for these dates.')
    return { start: iso(a), end: iso(b), clamped: null }
  }
  let clamped = false
  function previous(date: Date): string {
    const month = date.getUTCMonth(),
      year = date.getUTCFullYear() - 1
    if (year < 1) throw new Error('No previous supported year exists for these dates.')
    const prior = new Date(date)
    prior.setUTCFullYear(year)
    if (prior.getUTCMonth() !== month) {
      prior.setUTCDate(0)
      clamped = true
    }
    return iso(prior)
  }
  const a = previous(first),
    b = previous(last)
  return { start: a, end: b, clamped }
}
export function newIntentId(): string {
  return `console-${crypto.randomUUID()}`
}
export function contentBlob(base64: string, mime: string): Blob {
  const decoded = atob(base64)
  return new Blob([Uint8Array.from(decoded, (c) => c.charCodeAt(0))], { type: mime })
}
export function contentText(base64: string): string {
  return new TextDecoder().decode(Uint8Array.from(atob(base64), (c) => c.charCodeAt(0)))
}
