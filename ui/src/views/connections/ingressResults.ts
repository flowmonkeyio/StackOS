import type {
  IngressEndpointOut,
  IngressEndpointRoute,
  IngressEndpointStatusOut,
  IngressProviderResult,
  MessageTone,
} from './types'

export interface IngressMessage {
  tone: MessageTone
  text: string
}

const UPDATED_STATUSES = new Set(['remote_webhook_updated'])
const DRY_RUN_STATUSES = new Set(['remote_webhook_dry_run'])
const MANUAL_STATUSES = new Set(['manual_provider_update_required'])
const SKIPPED_STATUSES = new Set(['skipped'])
const FAILED_STATUSES = new Set(['failed'])

export function endpointHasPublicAddress(endpoint: IngressEndpointOut | null | undefined): boolean {
  return Boolean(endpoint?.public_base_url?.trim()) && endpoint?.status !== 'failed'
}

export function discoveryFailureMessage(endpoint: IngressEndpointOut | null | undefined): string | null {
  if (endpointHasPublicAddress(endpoint)) return null
  return 'No public address was discovered. Check the local tunnel and try again.'
}

export function summarizeProviderResults(results: IngressProviderResult[]): IngressMessage {
  const counts = {
    updated: countStatuses(results, UPDATED_STATUSES),
    dryRun: countStatuses(results, DRY_RUN_STATUSES),
    manual: countStatuses(results, MANUAL_STATUSES),
    skipped: countStatuses(results, SKIPPED_STATUSES),
    failed: countStatuses(results, FAILED_STATUSES),
  }
  const parts: string[] = []

  if (counts.updated > 0) {
    parts.push(`Synced ${counts.updated} ${plural('provider webhook', counts.updated)}.`)
  }
  if (counts.manual > 0) {
    parts.push(`${counts.manual} ${plural('provider', counts.manual)} needs manual update.`)
  }
  if (counts.dryRun > 0) {
    parts.push(`${counts.dryRun} ${plural('provider webhook', counts.dryRun)} checked in dry-run mode.`)
  }
  if (counts.skipped > 0) {
    parts.push(`${counts.skipped} ${plural('provider', counts.skipped)} skipped.`)
  }
  if (counts.failed > 0) {
    const firstError = results.find((result) => FAILED_STATUSES.has(result.status))?.error
    parts.push(
      `${counts.failed} ${plural('provider sync', counts.failed)} failed.${firstError ? ` ${firstError}` : ''}`,
    )
  }

  if (parts.length === 0) {
    return { tone: 'info', text: 'No provider webhook changes were needed.' }
  }

  return {
    tone: counts.failed > 0 || counts.skipped > 0 ? 'danger' : counts.manual > 0 || counts.dryRun > 0 ? 'info' : 'success',
    text: parts.join(' '),
  }
}

export function applyProviderResultsToIngressStatus(
  status: IngressEndpointStatusOut | null | undefined,
  results: IngressProviderResult[],
): IngressEndpointStatusOut | null {
  if (!status) return null
  if (!status.routes?.length || results.length === 0) return status

  return {
    ...status,
    routes: status.routes.map((route) => {
      const result = matchingProviderResult(route, results)
      if (!result) return route
      return {
        ...route,
        remote_status: result.status,
        notes: result.notes ?? route.notes,
      }
    }),
  }
}

function matchingProviderResult(
  route: IngressEndpointRoute,
  results: IngressProviderResult[],
): IngressProviderResult | undefined {
  return results.find((result) => {
    if (result.provider_key !== route.provider_key) return false
    if (result.profile_key && result.profile_key !== route.profile_key) return false

    const resultUrl = result.webhook_url ?? result.request_url
    if (resultUrl && resultUrl !== route.ingress_url) return false

    return true
  })
}

function countStatuses(results: IngressProviderResult[], statuses: Set<string>): number {
  return results.filter((result) => statuses.has(result.status)).length
}

function plural(label: string, count: number): string {
  return count === 1 ? label : `${label}s`
}
