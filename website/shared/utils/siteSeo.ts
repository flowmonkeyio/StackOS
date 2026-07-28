export const SITE_NAME = 'StackOS'

const brandSuffix = /\s*(?:\||—)\s*StackOS(?:\s+Library)?\s*$/i
const filePath = /\.[a-z0-9]{1,10}$/i

export function normalizeSubjectTitle(value: string) {
  let title = value.replace(/\s+/g, ' ').trim()
  while (brandSuffix.test(title)) title = title.replace(brandSuffix, '').trim()
  return title
}

export function brandTitle(value: string) {
  const subject = normalizeSubjectTitle(value)
  return subject === SITE_NAME ? SITE_NAME : `${subject} | ${SITE_NAME}`
}

export function canonicalPath(value: string) {
  const url = new URL(value, 'https://canonical.invalid')
  const normalized = url.pathname.replace(/\/{2,}/g, '/')
  if (normalized === '/') return '/'

  const path = normalized.replace(/\/+$/, '')
  const lastSegment = path.split('/').at(-1) || ''
  return filePath.test(lastSegment) ? path : `${path}/`
}

export function absoluteSiteUrl(origin: string, value: string) {
  return new URL(canonicalPath(value), `${origin.replace(/\/+$/, '')}/`).toString()
}

export function countLabel(count: number, singular: string, plural = `${singular}s`) {
  return `${count} ${count === 1 ? singular : plural}`
}
