<script setup lang="ts">
import { ref } from 'vue'

import type { SchemaActionCallAuditOut, SchemaRunPlanOut } from '@/api'
import StatusBadge from '@/components/StatusBadge.vue'
import { UiBadge, UiCallout, UiJsonBlock, UiPanel, UiSectionHeader } from '@/components/ui'
import { formatDateTime, sanitizeForDisplay } from '@/lib/stackos/json'

const props = withDefaults(defineProps<{
  plan: SchemaRunPlanOut
  actionCalls?: SchemaActionCallAuditOut[]
}>(), {
  actionCalls: () => [],
})

const expandedStep = ref<string | null>(null)

function callsForStep(stepPk: number): SchemaActionCallAuditOut[] {
  return props.actionCalls.filter((call) => call.run_plan_step_id === stepPk)
}

function toggleStep(stepId: string): void {
  expandedStep.value = expandedStep.value === stepId ? null : stepId
}
</script>

<template>
  <div class="space-y-4">
    <UiPanel class="p-4">
      <UiSectionHeader
        :title="plan.title"
        :description="plan.goal"
      >
        <template #actions>
          <StatusBadge
            :status="plan.status"
            kind="run"
          />
          <UiBadge v-if="plan.template_key">{{ plan.template_key }}</UiBadge>
        </template>
      </UiSectionHeader>

      <dl class="grid gap-3 text-sm md:grid-cols-3 xl:grid-cols-6">
        <div>
          <dt class="text-xs text-fg-muted">Run plan</dt>
          <dd class="font-mono">#{{ plan.id }}</dd>
        </div>
        <div>
          <dt class="text-xs text-fg-muted">Run</dt>
          <dd>{{ plan.run_id ? `#${plan.run_id}` : '-' }}</dd>
        </div>
        <div>
          <dt class="text-xs text-fg-muted">Template</dt>
          <dd class="truncate">{{ plan.template_version ?? '-' }}</dd>
        </div>
        <div>
          <dt class="text-xs text-fg-muted">Source</dt>
          <dd>{{ plan.template_source ?? '-' }}</dd>
        </div>
        <div>
          <dt class="text-xs text-fg-muted">Started</dt>
          <dd>{{ formatDateTime(plan.started_at) }}</dd>
        </div>
        <div>
          <dt class="text-xs text-fg-muted">Completed</dt>
          <dd>{{ formatDateTime(plan.completed_at) }}</dd>
        </div>
      </dl>
    </UiPanel>

    <UiPanel class="p-4">
      <UiSectionHeader
        title="Steps"
        as="h3"
      />
      <ol class="space-y-2">
        <li
          v-for="step in plan.steps"
          :key="step.id"
          class="rounded-md border border-subtle bg-bg-surface"
        >
          <button
            type="button"
            class="focus-ring flex w-full items-center justify-between gap-3 p-3 text-left text-sm hover:bg-bg-surface-alt"
            :aria-expanded="expandedStep === step.step_id"
            :aria-controls="`run-plan-step-${step.id}`"
            @click="toggleStep(step.step_id)"
          >
            <span class="flex min-w-0 flex-wrap items-center gap-2">
              <span class="font-mono text-xs text-fg-muted">#{{ step.position + 1 }}</span>
              <span class="min-w-0 truncate font-medium text-fg-default">{{ step.title }}</span>
              <StatusBadge
                :status="step.status"
                kind="job"
                :small="true"
              />
              <UiBadge
                v-if="(step.allowed_tools ?? []).length > 0"
                tone="info"
              >
                {{ (step.allowed_tools ?? []).length }} tools
              </UiBadge>
            </span>
            <span class="shrink-0 text-xs text-fg-muted">
              {{ formatDateTime(step.started_at) }}
            </span>
          </button>

          <div
            v-if="expandedStep === step.step_id"
            :id="`run-plan-step-${step.id}`"
            class="space-y-3 border-t border-subtle p-3 text-sm"
          >
            <UiCallout
              v-if="step.error"
              tone="danger"
              density="compact"
            >
              {{ step.error }}
            </UiCallout>
            <p
              v-if="step.purpose"
              class="text-fg-muted"
            >
              {{ step.purpose }}
            </p>
            <div class="grid gap-3 lg:grid-cols-2">
              <div>
                <h4 class="mb-1 text-xs font-semibold uppercase tracking-wide text-fg-muted">
                  Contracts
                </h4>
                <UiJsonBlock
                  :data="sanitizeForDisplay({
                    inputs: step.input_refs_json,
                    context: step.context_refs_json,
                    actions: step.action_refs_json,
                    resources: step.resource_refs_json,
                    approvals: step.approval_refs_json,
                    outputs: step.output_refs_json,
                    tools: step.allowed_tools ?? [],
                  })"
                  density="compact"
                  max-height="14rem"
                  wrap
                />
              </div>
              <div>
                <h4 class="mb-1 text-xs font-semibold uppercase tracking-wide text-fg-muted">
                  Result
                </h4>
                <UiJsonBlock
                  :data="sanitizeForDisplay(step.result_json ?? step.expected_outputs_json ?? {})"
                  density="compact"
                  max-height="14rem"
                  wrap
                />
              </div>
            </div>

            <div v-if="callsForStep(step.id).length > 0">
              <h4 class="mb-1 text-xs font-semibold uppercase tracking-wide text-fg-muted">
                Action Calls
              </h4>
              <ul class="space-y-2">
                <li
                  v-for="call in callsForStep(step.id)"
                  :key="call.id"
                  class="rounded-md border border-subtle bg-bg-surface-alt p-2"
                >
                  <div class="mb-2 flex flex-wrap items-center gap-2">
                    <UiBadge tone="accent">{{ call.plugin_slug }}</UiBadge>
                    <UiBadge>{{ call.action_key }}</UiBadge>
                    <StatusBadge
                      :status="call.status"
                      kind="job"
                      :small="true"
                    />
                    <span class="text-xs text-fg-muted">{{ formatDateTime(call.created_at) }}</span>
                  </div>
                  <UiJsonBlock
                    :data="sanitizeForDisplay({
                      request: call.request_json,
                      response: call.response_json,
                      metadata: call.metadata_json,
                    })"
                    density="compact"
                    max-height="12rem"
                    wrap
                  />
                </li>
              </ul>
            </div>
          </div>
        </li>
      </ol>
    </UiPanel>

    <div class="grid gap-4 lg:grid-cols-2">
      <UiPanel class="p-4">
        <UiSectionHeader title="Approvals" as="h3" />
        <UiJsonBlock
          :data="sanitizeForDisplay(plan.approval_requests)"
          density="compact"
          max-height="16rem"
          wrap
        />
      </UiPanel>
      <UiPanel class="p-4">
        <UiSectionHeader title="Run Context" as="h3" />
        <UiJsonBlock
          :data="sanitizeForDisplay({
            inputs: plan.inputs_json,
            selected_context: plan.selected_context_json,
            context_filters: plan.context_filters_json,
            grants: plan.grant_snapshot_json,
            budget: plan.budget_snapshot_json,
            policy: plan.policy_snapshot_json,
            outputs: plan.output_contract_json,
            metadata: plan.metadata_json,
          })"
          density="compact"
          max-height="16rem"
          wrap
        />
      </UiPanel>
    </div>
  </div>
</template>
