import type {
  IngressEndpointOut,
  IngressEndpointRoute,
  IngressEndpointStatusOut,
  IngressProviderResult,
  MessageTone,
} from './types'
import { providerLabel } from './formatters'

export interface IngressMessage {
  tone: MessageTone
  text: string
}

const CONFIRMED_STATUSES = new Set(['manual_provider_confirmed'])
const MANUAL_STATUSES = new Set(['manual_provider_update_required'])
const SKIPPED_STATUSES = new Set(['skipped'])
const FAILED_STATUSES = new Set(['failed'])

export function routeNeedsManualProviderUpdate(route: IngressEndpointRoute): boolean {
  return route.action_required === true || MANUAL_STATUSES.has(route.remote_status ?? '')
}

export function endpointHasPublicAddress(endpoint: IngressEndpointOut | null | undefined): boolean {
  return Boolean(endpoint?.public_base_url?.trim()) && endpoint?.status !== 'failed'
}

export function discoveryFailureMessage(
  endpoint: IngressEndpointOut | null | undefined,
): string | null {
  if (endpointHasPublicAddress(endpoint)) return null
  return 'No public address was discovered. Check the local tunnel and try again.'
}

export function summarizeProviderResults(results: IngressProviderResult[]): IngressMessage {
  const counts = {
    manual: countStatuses(results, MANUAL_STATUSES),
    skipped: countStatuses(results, SKIPPED_STATUSES),
    failed: countStatuses(results, FAILED_STATUSES),
    confirmed: countStatuses(results, CONFIRMED_STATUSES),
  }
  const parts: string[] = []

  if (counts.confirmed > 0) {
    parts.push(
      `Confirmed ${counts.confirmed} manual ${plural('provider update', counts.confirmed)}.`,
    )
  }
  if (counts.manual > 0) {
    parts.push(manualProviderSummary(results))
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
    return { tone: 'info', text: 'No provider route updates were needed.' }
  }

  return {
    tone:
      counts.failed > 0 || counts.skipped > 0
        ? 'danger'
        : counts.manual > 0
          ? 'info'
          : 'success',
    text: parts.join(' '),
  }
}

function manualProviderSummary(results: IngressProviderResult[]): string {
  const manualResults = results.filter((result) => MANUAL_STATUSES.has(result.status))
  const labels = manualResults.map(providerResultLabel)
  if (labels.length === 0) return ''

  const prefix =
    labels.length <= 2
      ? labels.join(' and ')
      : `${labels.slice(0, 2).join(', ')} and ${labels.length - 2} more`
  return `${prefix} ${labels.length === 1 ? 'needs' : 'need'} manual webhook update.`
}

function providerResultLabel(result: IngressProviderResult): string {
  const provider = providerLabel(result.provider_key)
  if (result.profile_key) return `${provider} (${result.profile_key})`
  return provider
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
        action_required: result.next_action ? true : route.action_required,
        next_action: result.next_action ?? route.next_action,
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

    const resultUrl = result.request_url
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
