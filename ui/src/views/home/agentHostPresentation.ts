import type { DesktopMcpHostStatus } from '@/lib/desktop'

export const SUPPORTED_AGENT_HOSTS = [
  { host_key: 'codex', label: 'Codex' },
  { host_key: 'claude-code', label: 'Claude Code' },
  { host_key: 'claude-desktop', label: 'Claude Desktop' },
  { host_key: 'gemini-cli', label: 'Gemini CLI' },
] as const

export type AgentHostConnectionState =
  | 'connected'
  | 'restart-needed'
  | 'not-connected'
  | 'update-needed'
  | 'repair-needed'
  | 'not-detected'

export interface AgentHostPresentation {
  state: AgentHostConnectionState
  label: string
  detail: string
  tone: 'success' | 'warning' | 'danger' | 'neutral'
}

export function agentHostLabel(hostKey: string): string {
  return SUPPORTED_AGENT_HOSTS.find((host) => host.host_key === hostKey)?.label ?? hostKey
}

export function agentHostPresentation(host: DesktopMcpHostStatus): AgentHostPresentation {
  if (!host.available || host.status === 'absent') {
    return {
      state: 'not-detected',
      label: 'Not detected',
      detail: 'This tool was not found on this Mac.',
      tone: 'neutral',
    }
  }
  if (host.needs_restart || host.status === 'restart_required') {
    return {
      state: 'restart-needed',
      label: 'Restart needed',
      detail: 'Restart this tool to finish its StackOS connection.',
      tone: 'warning',
    }
  }
  if (
    (host.status === 'registered_current' || host.status === 'registered') &&
    host.ok !== false
  ) {
    return {
      state: 'connected',
      label: 'Connected',
      detail: 'StackOS is ready in this tool.',
      tone: 'success',
    }
  }
  if (host.status === 'available_unregistered' || host.status === 'removed') {
    return {
      state: 'not-connected',
      label: 'Not connected',
      detail: 'This tool is available, but it cannot use StackOS yet.',
      tone: host.advisory ? 'neutral' : 'warning',
    }
  }
  if (host.status === 'unsupported_host_version') {
    return {
      state: 'update-needed',
      label: 'Update needed',
      detail: 'This version cannot report its StackOS connection.',
      tone: 'warning',
    }
  }
  return {
    state: 'repair-needed',
    label: 'Repair needed',
    detail: 'The saved StackOS connection needs repair.',
    tone: host.status === 'registered_unsafe' ? 'danger' : 'warning',
  }
}

export function agentHostSummary(items: DesktopMcpHostStatus[]): string {
  if (items.length === 0) return 'No connection status yet'
  const states = items.map((item) => agentHostPresentation(item).state)
  const connected = states.filter((state) => state === 'connected').length
  const notDetected = states.filter((state) => state === 'not-detected').length
  const needsAttention = states.length - connected - notDetected
  const parts: string[] = []
  if (connected) parts.push(`${connected} connected`)
  if (needsAttention) parts.push(`${needsAttention} ${needsAttention === 1 ? 'needs' : 'need'} attention`)
  if (notDetected) parts.push(`${notDetected} not detected`)
  return parts.join(' · ')
}
