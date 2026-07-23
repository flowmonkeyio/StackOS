/**
 * Canonical status / severity mappings for the generic StackOS UI.
 *
 * Components like `StatusBadge` read from these maps, so backend status
 * strings resolve consistently without per-workflow UI logic.
 */

export type Tone = 'neutral' | 'info' | 'success' | 'warning' | 'danger';

export interface StatusDef {
  label: string;
  tone: Tone;
  description?: string;
  /** Lucide icon name. Resolved by consumer. */
  icon?: string;
  /** Show a leading dot for live/in-flight states. */
  dot?: boolean;
  /** True if this is a transitional state; pulses the dot. */
  inFlight?: boolean;
}

function defineStatuses<K extends string>(map: Record<K, StatusDef>): Record<K, StatusDef> {
  return map;
}

export const runStatus = defineStatuses({
  draft: { label: 'Draft', tone: 'neutral', icon: 'file' },
  pending: { label: 'Pending', tone: 'neutral', icon: 'clock' },
  queued: { label: 'Queued', tone: 'neutral', icon: 'clock' },
  started: { label: 'Started', tone: 'info', icon: 'loader', dot: true, inFlight: true },
  'not-started': { label: 'Not Started', tone: 'neutral', icon: 'clock' },
  'in-progress': { label: 'In Progress', tone: 'info', icon: 'loader', dot: true, inFlight: true },
  running: { label: 'Running', tone: 'info', icon: 'loader', dot: true, inFlight: true },
  paused: { label: 'Paused', tone: 'warning', icon: 'pause' },
  success: { label: 'Success', tone: 'success', icon: 'check-circle' },
  succeeded: { label: 'Succeeded', tone: 'success', icon: 'check-circle' },
  completed: { label: 'Completed', tone: 'success', icon: 'check-circle' },
  complete: { label: 'Complete', tone: 'success', icon: 'check-circle' },
  deferred: { label: 'Deferred', tone: 'warning', icon: 'pause' },
  failed: { label: 'Failed', tone: 'danger', icon: 'x-circle' },
  aborted: { label: 'Aborted', tone: 'danger', icon: 'circle-slash' },
  canceled: { label: 'Canceled', tone: 'neutral', icon: 'circle-slash' },
  skipped: { label: 'Skipped', tone: 'neutral', icon: 'skip-forward' },
  timedOut: { label: 'Timed Out', tone: 'danger', icon: 'timer-off' },
  partial: { label: 'Partial', tone: 'warning', icon: 'circle-dashed' },
});

export const stepStatus = defineStatuses({
  pending: { label: 'Pending', tone: 'neutral', icon: 'clock' },
  queued: { label: 'Queued', tone: 'neutral', icon: 'clock' },
  running: { label: 'Running', tone: 'info', icon: 'loader', dot: true, inFlight: true },
  success: { label: 'Success', tone: 'success', icon: 'check-circle' },
  succeeded: { label: 'Succeeded', tone: 'success', icon: 'check-circle' },
  completed: { label: 'Completed', tone: 'success', icon: 'check-circle' },
  failed: { label: 'Failed', tone: 'danger', icon: 'x-circle' },
  aborted: { label: 'Aborted', tone: 'danger', icon: 'circle-slash' },
  skipped: { label: 'Skipped', tone: 'neutral', icon: 'skip-forward' },
  deferred: { label: 'Deferred', tone: 'warning', icon: 'pause' },
  'dry-run': { label: 'Dry Run', tone: 'warning', icon: 'flask-conical' },
  enabled: { label: 'Enabled', tone: 'success', icon: 'circle-check' },
  disabled: { label: 'Disabled', tone: 'neutral', icon: 'circle' },
  paused: { label: 'Paused', tone: 'warning', icon: 'pause' },
  draft: { label: 'Draft', tone: 'neutral', icon: 'file' },
  deprecated: { label: 'Deprecated', tone: 'warning', icon: 'archive-x' },
  blocked: { label: 'Blocked', tone: 'danger', icon: 'octagon-alert' },
});

export const trackerStatus = defineStatuses({
  'not-started': { label: 'Not Started', tone: 'neutral', icon: 'clock' },
  'in-progress': {
    label: 'In Progress',
    tone: 'info',
    icon: 'loader',
    dot: true,
    inFlight: true,
  },
  complete: { label: 'Complete', tone: 'success', icon: 'check-circle' },
  deferred: { label: 'Deferred', tone: 'warning', icon: 'pause' },
  aborted: { label: 'Aborted', tone: 'danger', icon: 'circle-slash' },
  failed: { label: 'Failed', tone: 'danger', icon: 'x-circle' },
  skipped: { label: 'Skipped', tone: 'neutral', icon: 'skip-forward' },
});

export const projectState = defineStatuses({
  archived: { label: 'Archived', tone: 'neutral', icon: 'archive' },
  setup: { label: 'Setup', tone: 'info', icon: 'settings' },
  blocked: { label: 'Blocked', tone: 'danger', icon: 'octagon-alert' },
});

export const integrationHealth = defineStatuses({
  healthy: { label: 'Healthy', tone: 'success', icon: 'check-circle' },
  degraded: { label: 'Degraded', tone: 'warning', icon: 'alert-triangle' },
  failing: { label: 'Failing', tone: 'danger', icon: 'x-circle' },
  notConnected: { label: 'Not connected', tone: 'neutral', icon: 'plug' },
  expiring: { label: 'Expiring', tone: 'warning', icon: 'key-round' },
  expired: { label: 'Expired', tone: 'danger', icon: 'key-round' },
});

export const budgetState = defineStatuses({
  underBudget: { label: 'Under budget', tone: 'success', icon: 'circle-check' },
  onTrack: { label: 'On track', tone: 'info', icon: 'gauge' },
  approaching: { label: 'Approaching', tone: 'warning', icon: 'trending-up' },
  overBudget: { label: 'Over budget', tone: 'danger', icon: 'alert-octagon' },
  capped: { label: 'Capped', tone: 'neutral', icon: 'lock' },
});

// Provider/credential connection status (auth/status connections[].status).
export const connectionStatus = defineStatuses({
  connected: { label: 'Connected', tone: 'success', icon: 'check-circle', dot: true },
  used: { label: 'Connected', tone: 'success', icon: 'check-circle', dot: true },
  pending: { label: 'Pending', tone: 'warning', icon: 'clock', dot: true, inFlight: true },
  expired: { label: 'Expired', tone: 'danger', icon: 'key-round' },
  failed: { label: 'Failed', tone: 'danger', icon: 'x-circle' },
  revoked: { label: 'Revoked', tone: 'neutral', icon: 'circle-slash' },
  'setup-required': { label: 'Setup needed', tone: 'warning', icon: 'wrench' },
});

// Provider capability readiness derived from explicit provider-manifest contracts.
export const readinessStatus = defineStatuses({
  ready: { label: 'Ready', tone: 'success', icon: 'check-circle' },
  'not-enabled': { label: 'Not enabled', tone: 'neutral', icon: 'circle' },
  'missing-scopes': { label: 'Missing scopes', tone: 'warning', icon: 'key-round' },
  'operator-checklist': { label: 'Operator checklist', tone: 'warning', icon: 'list-checks' },
  'connection-repair': { label: 'Connection repair', tone: 'danger', icon: 'wrench' },
  pending: { label: 'Pending', tone: 'info', icon: 'clock', dot: true, inFlight: true },
});

// Agent-request lifecycle (the agent/human inbox).
export const agentRequestStatus = defineStatuses({
  new: { label: 'New', tone: 'info', icon: 'inbox' },
  claimed: { label: 'Claimed', tone: 'info', icon: 'loader', dot: true, inFlight: true },
  'run-created': { label: 'Run created', tone: 'info', icon: 'loader', dot: true, inFlight: true },
  'run-started': { label: 'Running', tone: 'info', icon: 'loader', dot: true, inFlight: true },
  responded: { label: 'Responded', tone: 'info', icon: 'check' },
  resolved: { label: 'Resolved', tone: 'success', icon: 'check-circle' },
  ignored: { label: 'Ignored', tone: 'neutral', icon: 'circle-slash' },
  failed: { label: 'Failed', tone: 'danger', icon: 'x-circle' },
});

// Read/unread/archived attention flag.
export const attentionState = defineStatuses({
  unread: { label: 'Unread', tone: 'info', icon: 'inbox', dot: true },
  read: { label: 'Read', tone: 'neutral', icon: 'check' },
  archived: { label: 'Archived', tone: 'neutral', icon: 'archive' },
});

// Local service / daemon health.
export const systemHealth = defineStatuses({
  ok: { label: 'Running', tone: 'success', icon: 'check-circle', dot: true },
  healthy: { label: 'Running', tone: 'success', icon: 'check-circle', dot: true },
  degraded: { label: 'Degraded', tone: 'warning', icon: 'alert-triangle' },
  unreachable: { label: 'Unreachable', tone: 'danger', icon: 'x-circle' },
  checking: { label: 'Checking…', tone: 'neutral', icon: 'loader', dot: true, inFlight: true },
});

// Agent-memory entities surfaced in Project Data.
export const memoryState = defineStatuses({
  proposed: { label: 'Proposed', tone: 'warning', icon: 'clock' },
  accepted: { label: 'Accepted', tone: 'success', icon: 'check-circle' },
  active: { label: 'Active', tone: 'success', icon: 'check-circle' },
  rejected: { label: 'Rejected', tone: 'danger', icon: 'x-circle' },
  superseded: { label: 'Superseded', tone: 'neutral', icon: 'archive' },
  recorded: { label: 'Recorded', tone: 'info', icon: 'check' },
  planned: { label: 'Planned', tone: 'neutral', icon: 'clock' },
  running: { label: 'Running', tone: 'info', icon: 'loader', dot: true, inFlight: true },
  complete: { label: 'Complete', tone: 'success', icon: 'check-circle' },
  completed: { label: 'Complete', tone: 'success', icon: 'check-circle' },
  archived: { label: 'Archived', tone: 'neutral', icon: 'archive' },
});

export const statusRegistry = {
  run: runStatus,
  step: stepStatus,
  tracker: trackerStatus,
  project: projectState,
  integration: integrationHealth,
  budget: budgetState,
  connection: connectionStatus,
  readiness: readinessStatus,
  agentRequest: agentRequestStatus,
  attention: attentionState,
  system: systemHealth,
  memory: memoryState,
} as const;

export type StatusDomain = keyof typeof statusRegistry;
type StatusMap = { [Status in string]: StatusDef };

function titleCaseStatus(key: string): string {
  return key
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/[_-]/g, ' ')
    .split(' ')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase())
    .join(' ');
}

export function resolveStatus(domain: StatusDomain, key: string): StatusDef {
  const map = statusRegistry[domain] as StatusMap;
  return map[key] ?? {
    label: titleCaseStatus(key),
    tone: 'neutral',
    description: `Unknown ${domain} status: ${key}`,
  };
}
