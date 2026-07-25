import type { DesktopMcpHostStatus } from '@/lib/desktop'

export type AgentHostConnectionState =
  | 'connected'
  | 'available'
  | 'repair_needed'
  | 'review_required'
  | 'update_required'
  | 'restart_required'
  | 'unavailable'
  | 'error'

export interface AgentHostPresentation {
  state: AgentHostConnectionState
  label: string
  detail: string
  tone: 'success' | 'warning' | 'danger' | 'neutral'
}

const STATES = new Set<AgentHostConnectionState>([
  'connected',
  'available',
  'repair_needed',
  'review_required',
  'update_required',
  'restart_required',
  'unavailable',
  'error',
])

const DEFAULT_LABELS: Record<AgentHostConnectionState, string> = {
  connected: 'Connected',
  available: 'Available',
  repair_needed: 'Repair needed',
  review_required: 'Review required',
  update_required: 'Update needed',
  restart_required: 'Restart needed',
  unavailable: 'Not detected',
  error: 'Status unavailable',
}

const TONES: Record<
  AgentHostConnectionState,
  AgentHostPresentation['tone']
> = {
  connected: 'success',
  available: 'neutral',
  repair_needed: 'warning',
  review_required: 'danger',
  update_required: 'warning',
  restart_required: 'warning',
  unavailable: 'neutral',
  error: 'danger',
}

const HOST_LOGOS: Record<string, string> = {
  codex: '/images/openai.webp',
  'claude-code': '/images/claude-code.webp',
  'claude-desktop': '/images/claude.webp',
  'gemini-cli': '/images/gemini.webp',
  hermes: '/images/hermes.webp',
}

export function agentHostLabel(host: DesktopMcpHostStatus): string {
  return host.display_name || host.host_key
}

export function agentHostLogo(host: DesktopMcpHostStatus): string {
  return HOST_LOGOS[host.host_key] || '/images/stackos-icon.png'
}

export function agentHostPresentation(host: DesktopMcpHostStatus): AgentHostPresentation {
  const candidate = host.connection_state
  const state: AgentHostConnectionState =
    candidate && STATES.has(candidate) ? candidate : 'error'
  return {
    state,
    label: host.status_label || DEFAULT_LABELS[state],
    detail: host.message || 'StackOS could not read this connection status.',
    tone: TONES[state],
  }
}

export function agentHostSummary(items: DesktopMcpHostStatus[]): string {
  if (items.length === 0) return 'No connection status yet'
  const connected = items.filter(
    (item) => agentHostPresentation(item).state === 'connected',
  ).length
  const notDetected = items.filter(
    (item) => agentHostPresentation(item).state === 'unavailable',
  ).length
  const needsAttention = items.filter((item) => item.blocking === true).length
  const available = items.length - connected - notDetected - needsAttention
  const parts: string[] = []
  if (connected) parts.push(`${connected} connected`)
  if (available) parts.push(`${available} available`)
  if (needsAttention) parts.push(`${needsAttention} ${needsAttention === 1 ? 'needs' : 'need'} attention`)
  if (notDetected) parts.push(`${notDetected} not detected`)
  return parts.join(' · ')
}
