import { trackerStatus } from '@/design/status'
import type { TrackerStatus } from '@/lib/task-tracker/types'

export const ticketStatuses = Object.keys(trackerStatus) as TrackerStatus[]
export type TicketCounts = Record<TrackerStatus, number>

export interface TicketCountSummary {
  project_id: number | null
  is_active: boolean | null
  as_of: string
  total_count: number
  ticket_counts: TicketCounts
}

export interface RecordedTask {
  id: number
  key: string
  title: string
  status: string
  updated_at: string
}

export interface PortfolioProject {
  id: number
  slug: string
  name: string
  domain: string
  is_active: boolean
  created_at: string
  updated_at: string
  latest_task: RecordedTask | null
  last_activity_at: string | null
  ticket_count: number
  ticket_counts: TicketCounts
}

export interface OverviewPage<T> {
  items: T[]
  next_cursor: number | null
  total_estimate?: number | null
}

export interface ConfiguredWorkflow {
  key: string
  name: string
  pluginSlug: string | null
}

export function ticketWorkUrl(projectId: number, status?: TrackerStatus): string {
  const params = new URLSearchParams({ view: 'tickets' })
  if (status) params.set('status', status)
  return `/projects/${projectId}/tasks?${params.toString()}`
}

export function configuredWorkflows(
  extensions: Array<{ workflow_key: string; enabled: boolean }>,
  catalog: Array<{ key: string; name: string; plugin_slug?: string | null }>,
): ConfiguredWorkflow[] {
  return extensions
    .filter((extension) => extension.enabled)
    .map((extension) => {
      const template = catalog.find((item) => item.key === extension.workflow_key)
      return {
        key: extension.workflow_key,
        name: template?.name ?? extension.workflow_key,
        pluginSlug: template?.plugin_slug ?? null,
      }
    })
}
