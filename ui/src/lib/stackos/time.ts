/**
 * Operator-grade time formatting.
 *
 * Tables show relative timestamps ("3d ago") so recency is scannable;
 * absolute datetimes go in `title` tooltips. Values are computed at render
 * time (no live ticking — the UI is watcher-free by contract).
 */

const MINUTE = 60_000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR
const WEEK = 7 * DAY

/** "just now" / "4m ago" / "3h ago" / "2d ago" / "5w ago" / "Mar 3, 2026". */
export function formatRelativeDateTime(
  value: string | null | undefined,
  now: Date = new Date(),
): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value

  const delta = now.getTime() - date.getTime()
  if (delta < 0) {
    // Future timestamps (next scheduled run) — mirror the scale forward.
    const ahead = -delta
    if (ahead < MINUTE) return 'in <1m'
    if (ahead < HOUR) return `in ${Math.round(ahead / MINUTE)}m`
    if (ahead < DAY) return `in ${Math.round(ahead / HOUR)}h`
    if (ahead < WEEK * 4) return `in ${Math.round(ahead / DAY)}d`
    return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
  }
  if (delta < MINUTE) return 'just now'
  if (delta < HOUR) return `${Math.floor(delta / MINUTE)}m ago`
  if (delta < DAY) return `${Math.floor(delta / HOUR)}h ago`
  if (delta < WEEK * 4) return `${Math.floor(delta / DAY)}d ago`
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

/** Absolute form for titles/tooltips alongside the relative label. */
export function formatAbsoluteDateTime(value: string | null | undefined): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

/** "45s" / "14m" / "2h 22m" / "1d 4h" from a minute or second count. */
export function formatDurationMinutes(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined || Number.isNaN(minutes)) return '-'
  if (minutes < 1) return '<1m'
  const mins = Math.round(minutes)
  if (mins < 60) return `${mins}m`
  const hours = Math.floor(mins / 60)
  const rem = mins % 60
  if (hours < 24) return rem > 0 ? `${hours}h ${rem}m` : `${hours}h`
  const days = Math.floor(hours / 24)
  const remHours = hours % 24
  return remHours > 0 ? `${days}d ${remHours}h` : `${days}d`
}

/**
 * Newest-first ordering for record lists. The daemon returns rows in
 * ascending id order; operator tables lead with the latest activity.
 * Rows without a parseable timestamp sink to the bottom.
 */
export function newestFirst<T>(
  items: readonly T[],
  pick: (item: T) => string | null | undefined,
): T[] {
  return [...items].sort((a, b) => {
    const ta = Date.parse(pick(a) ?? '')
    const tb = Date.parse(pick(b) ?? '')
    return (Number.isNaN(tb) ? 0 : tb) - (Number.isNaN(ta) ? 0 : ta)
  })
}

/** Duration between two ISO timestamps; open-ended when `end` is missing. */
export function formatDurationBetween(
  start: string | null | undefined,
  end: string | null | undefined,
  now: Date = new Date(),
): string {
  if (!start) return '-'
  const startDate = new Date(start)
  if (Number.isNaN(startDate.getTime())) return '-'
  const endDate = end ? new Date(end) : now
  if (Number.isNaN(endDate.getTime())) return '-'
  const minutes = (endDate.getTime() - startDate.getTime()) / MINUTE
  return formatDurationMinutes(minutes)
}
