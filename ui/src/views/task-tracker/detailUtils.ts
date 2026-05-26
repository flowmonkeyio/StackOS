export function hasJsonObject(value: Record<string, unknown> | null): boolean {
  return Boolean(value && Object.keys(value).length > 0)
}

export function formatJsonBlock(value: Record<string, unknown> | null): string {
  return value ? JSON.stringify(value, null, 2) : ''
}
