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
