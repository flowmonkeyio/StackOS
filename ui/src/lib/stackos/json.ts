const SECRET_KEY_PATTERN =
  /(^|[_-])(access[_-]?token|api[_-]?key|apikey|authorization|client[_-]?secret|password|private[_-]?key|refresh[_-]?token|secret|token)([_-]|$)/i

const SAFE_REF_KEYS = new Set(['credential_ref', 'auth_ref'])

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isSecretKey(key: string): boolean {
  const normalized = key.toLowerCase().replaceAll('-', '_')
  if (SAFE_REF_KEYS.has(normalized) || normalized.endsWith('_credential_ref')) return false
  return SECRET_KEY_PATTERN.test(normalized)
}

function redactText(value: string): string {
  return value
    .replace(/Bearer\s+[A-Za-z0-9._~+/=-]+/gi, 'Bearer [redacted]')
    .replace(/(api[_-]?key|token|secret|password)=([^&\s]+)/gi, '$1=[redacted]')
}

export function sanitizeForDisplay(value: unknown): unknown {
  if (Array.isArray(value)) return value.map((item) => sanitizeForDisplay(item))
  if (isRecord(value)) {
    const out: Record<string, unknown> = {}
    for (const [key, child] of Object.entries(value)) {
      out[key] = isSecretKey(key) ? '[redacted]' : sanitizeForDisplay(child)
    }
    return out
  }
  if (typeof value === 'string') return redactText(value)
  return value
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

export function shortValue(value: unknown): string {
  if (value === null || value === undefined) return '-'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  try {
    return JSON.stringify(sanitizeForDisplay(value))
  } catch {
    return String(value)
  }
}
