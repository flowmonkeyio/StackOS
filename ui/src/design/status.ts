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

export const statusRegistry = {
  run: runStatus,
  step: stepStatus,
  tracker: trackerStatus,
  project: projectState,
  integration: integrationHealth,
  budget: budgetState,
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
