/**
 * Attention aggregator — the single "what needs me right now?" derivation.
 *
 * StackOS has no cross-cutting attention endpoint (see
 * docs/ui-redesign-direction.md §8C), so this store fans out across the
 * primitives that signal a human is needed and folds them into one ranked,
 * humanized list. Each source is isolated: one failing fetch degrades to an
 * empty contribution and flips `degraded`, it never blanks the whole list.
 *
 * Powers Home's "Needs you" band and the Inbox.
 */

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import type {
  SchemaAuthStatusOut,
  SchemaCostResponse,
  SchemaIntegrationBudgetOut,
  SchemaPageResponseRunOut,
  SchemaRunOut,
} from '@/api'
import type { Tone } from '@/design/status'
import { listAgentRequests } from '@/lib/stackos/agentRequest'
import { apiFetch } from '@/lib/client'
import { callOperation } from '@/lib/operations'
import { newestFirst } from '@/lib/stackos/time'

export type AttentionKind =
  | 'failed-run'
  | 'question'
  | 'blocked'
  | 'connection'
  | 'budget'

export interface AttentionItem {
  /** Stable key, e.g. "question:12". */
  id: string
  kind: AttentionKind
  tone: Tone
  title: string
  detail: string | null
  /** ISO timestamp for recency ordering / display, when meaningful. */
  when: string | null
  /** In-app route to act on or inspect this item. */
  to: string
  /** CTA verb shown on the item. */
  cta: string
  /** User-facing consequence if the item is ignored. */
  impact: string
  /** Clear split between human and agent ownership. */
  ownership: string
  /** Expected state after the human follows the CTA. */
  after: string
}

interface AttentionRefreshOptions {
  authStatus?: Promise<SchemaAuthStatusOut>
}

const TONE_RANK: Record<Tone, number> = {
  danger: 0,
  warning: 1,
  info: 2,
  success: 3,
  neutral: 4,
}

function monthKey(now: Date): string {
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}

export const useAttentionStore = defineStore('attention', () => {
  const items = ref<AttentionItem[]>([])
  const loading = ref(false)
  const degraded = ref(false)
  const projectId = ref<number | null>(null)

  const countsByKind = computed<Record<AttentionKind, number>>(() => {
    const base: Record<AttentionKind, number> = {
      'failed-run': 0,
      question: 0,
      blocked: 0,
      connection: 0,
      budget: 0,
    }
    for (const item of items.value) base[item.kind] += 1
    return base
  })

  const total = computed(() => items.value.length)

  async function source<T>(fn: () => Promise<T[]>): Promise<T[]> {
    try {
      return await fn()
    } catch {
      degraded.value = true
      return []
    }
  }

  async function failedRuns(id: number, base: string): Promise<AttentionItem[]> {
    const page = await apiFetch<SchemaPageResponseRunOut>(
      `/api/v1/projects/${id}/runs?status=failed&limit=20`,
    )
    const rows = newestFirst<SchemaRunOut>(page.items, (r) => r.ended_at ?? r.started_at).slice(0, 6)
    return rows.map((run) => ({
      id: `failed-run:${run.id}`,
      kind: 'failed-run' as const,
      tone: 'danger' as Tone,
      title: run.last_step ? `A job failed at "${run.last_step}"` : 'A job failed',
      detail: run.error ?? null,
      when: run.ended_at ?? run.started_at ?? null,
      to: `${base}/runs/${run.id}`,
      cta: 'Inspect failed run',
      impact: 'The run stopped before it reached its intended outcome.',
      ownership: 'A person inspects the failure and decides whether setup needs repair or the originating agent should retry. StackOS does not retry on its own.',
      after: 'The run remains auditable. A connected agent can resume or create follow-up work after the cause is understood.',
    }))
  }

  async function questions(id: number, base: string): Promise<AttentionItem[]> {
    const page = await listAgentRequests({
      project_id: id,
      attention_status: 'unread',
      limit: 25,
    })
    const rows = newestFirst(page.items ?? [], (r) => r.created_at).slice(0, 12)
    return rows.map((req) => ({
      id: `question:${req.id}`,
      kind: 'question' as const,
      tone: 'info' as Tone,
      title: req.title?.trim() || 'A request is waiting for you',
      detail: req.body_preview?.trim() || null,
      when: req.created_at ?? null,
      to: `${base}/agent-requests?request=${req.id}&attention_status=unread`,
      cta: 'Open request',
      impact: 'Related agent work may be waiting for a human answer or approval.',
      ownership: 'You provide the decision in the request context. The originating agent owns the next execution step.',
      after: 'The answered request stays in the audit trail and the agent can continue through MCP.',
    }))
  }

  async function blocked(id: number, base: string): Promise<AttentionItem[]> {
    const status = await callOperation<{ blocked_ticket_count?: number | null }>('tracker.status', {
      project_id: id,
    })
    const count = status.blocked_ticket_count ?? 0
    if (count <= 0) return []
    return [
      {
        id: 'blocked:summary',
        kind: 'blocked',
        tone: 'warning',
        title: count === 1 ? '1 task is blocked' : `${count} tasks are blocked`,
        detail: 'Work is waiting on an unmet dependency.',
        when: null,
        to: `${base}/tasks?focus=blocked`,
        cta: 'Review blocked work',
        impact: `${count} ${count === 1 ? 'work item cannot' : 'work items cannot'} progress until dependencies or recorded blockers change.`,
        ownership: 'Agents own execution and dependency updates. A person reviews impact, supplies missing decisions, or coordinates external repair.',
        after: 'When the owning agent records the dependency as resolved, the item leaves Attention automatically.',
      },
    ]
  }

  async function connections(
    id: number,
    base: string,
    authStatus?: Promise<SchemaAuthStatusOut>,
  ): Promise<AttentionItem[]> {
    const status = await (authStatus ??
      apiFetch<SchemaAuthStatusOut>(`/api/v1/projects/${id}/auth/status`))
    const attention = (status.connections ?? []).filter(
      (c) => c.revoked_at == null && c.status !== 'connected' && c.status !== 'used',
    )
    return attention.slice(0, 6).map((c) => ({
      id: `connection:${c.credential_ref}`,
      kind: 'connection' as const,
      tone: (c.status === 'expired' || c.status === 'failed' ? 'danger' : 'warning') as Tone,
      title: `${c.label || c.provider_key} needs attention`,
      detail: `Connection ${c.status}.`,
      when: c.last_tested_at ?? null,
      to: `${base}/connections?section=services&provider_key=${encodeURIComponent(c.provider_key)}`,
      cta: 'Repair connection',
      impact: `Agent actions that require ${c.label || c.provider_key} may fail or remain unavailable.`,
      ownership: 'Connection setup is a human local-admin action. Credentials stay inside the StackOS daemon.',
      after: 'Test the repaired connection. Connected agents will receive the same safe credential reference when it is healthy.',
    }))
  }

  async function budgets(id: number, base: string): Promise<AttentionItem[]> {
    const now = new Date()
    const [budgetRows, cost] = await Promise.all([
      apiFetch<SchemaIntegrationBudgetOut[]>(`/api/v1/projects/${id}/budgets`),
      apiFetch<SchemaCostResponse>(`/api/v1/projects/${id}/cost?month=${monthKey(now)}`).catch(
        () => null,
      ),
    ])
    const spendByKind = (cost?.by_integration ?? {}) as Record<string, number>
    const out: AttentionItem[] = []
    for (const b of budgetRows) {
      const cap = b.monthly_budget_usd ?? 0
      if (cap <= 0) continue
      const spend = b.current_month_spend ?? spendByKind[b.kind] ?? 0
      const pct = (spend / cap) * 100
      const threshold = b.alert_threshold_pct ?? 80
      if (pct < threshold) continue
      const over = pct >= 100
      out.push({
        id: `budget:${b.kind}`,
        kind: 'budget',
        tone: over ? 'danger' : 'warning',
        title: over ? `${b.kind} is over budget` : `${b.kind} is approaching its budget`,
        detail: `$${spend.toFixed(2)} of $${cap.toFixed(2)} this month (${Math.round(pct)}%).`,
        when: null,
        to: `${base}/cost-budget?from=attention&integration=${encodeURIComponent(b.kind)}`,
        cta: 'Review spend policy',
        impact: over
          ? 'New provider activity may be blocked by the project budget.'
          : 'Current usage is close to the human-defined monthly limit.',
        ownership: 'A person owns budget and risk policy. Agents operate only inside the configured limit.',
        after: 'The alert clears after spend falls below the threshold or a person deliberately changes the budget.',
      })
    }
    return out
  }

  async function refresh(id: number, options: AttentionRefreshOptions = {}): Promise<void> {
    projectId.value = id
    loading.value = true
    degraded.value = false
    const base = `/projects/${id}`
    try {
      const [a, b, c, d, e] = await Promise.all([
        source(() => failedRuns(id, base)),
        source(() => questions(id, base)),
        source(() => blocked(id, base)),
        source(() => connections(id, base, options.authStatus)),
        source(() => budgets(id, base)),
      ])
      const merged = [...a, ...b, ...c, ...d, ...e]
      merged.sort((x, y) => {
        const tone = TONE_RANK[x.tone] - TONE_RANK[y.tone]
        if (tone !== 0) return tone
        const tx = x.when ? Date.parse(x.when) : 0
        const ty = y.when ? Date.parse(y.when) : 0
        return (Number.isNaN(ty) ? 0 : ty) - (Number.isNaN(tx) ? 0 : tx)
      })
      items.value = merged
    } finally {
      loading.value = false
    }
  }

  function reset(): void {
    items.value = []
    degraded.value = false
    projectId.value = null
  }

  return { items, loading, degraded, projectId, countsByKind, total, refresh, reset }
})
