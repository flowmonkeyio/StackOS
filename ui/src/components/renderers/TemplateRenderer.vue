<script setup lang="ts">
import { computed } from 'vue'
import type { SchemaLoadedWorkflowTemplate } from '@/api'
import { UiAdvancedJsonPanel, UiBadge, UiCallout } from '@/components/ui'
import { sanitizeForDisplay } from '@/lib/stackos/json'

const props = defineProps<{ template: SchemaLoadedWorkflowTemplate }>()
// The loader has already applied the project extension. Never overlay it again here.
const spec = computed(() => props.template.spec)
const extension = computed(() => props.template.project_extension)
const inputs = computed(() => {
  const required = new Set(extension.value?.enabled ? extension.value.required_input_keys_json : [])
  const declared = (spec.value.inputs ?? []).map(input => ({ ...input, required: input.required || required.has(input.key) }))
  return [...declared, ...[...required].filter(key => !declared.some(input => input.key === key))
    .map(key => ({ key, name: key, description: 'Required by this project.', required: true }))]
})
const steps = computed(() => spec.value.steps ?? [])
const stepName = (id: string) => steps.value.find(step => step.id === id)?.title || id
const details = computed(() => sanitizeForDisplay(props.template))
</script>

<template>
  <div class="workflow-detail">
    <section aria-labelledby="workflow-stages">
      <h2
        id="workflow-stages"
        class="t-h2 mb-4 text-fg-strong"
      >
        Stages
      </h2>
      <ol
        v-if="steps.length"
        class="workflow-stages"
      >
        <li
          v-for="(step, index) in steps"
          :key="step.id"
        >
          <span
            class="workflow-stage-number"
            aria-hidden="true"
          >{{ index + 1 }}</span>
          <div class="min-w-0">
            <p
              class="font-medium text-fg-strong"
              :title="step.purpose"
            >
              {{ step.title || step.id }}
            </p>
            <p
              v-if="step.purpose"
              class="sr-only"
            >
              {{ step.purpose }}
            </p>
            <p
              v-if="step.depends_on?.length"
              class="sr-only"
            >
              After: {{ step.depends_on.map(stepName).join(', ') }}
            </p>
          </div>
        </li>
      </ol>
      <p
        v-else
        class="text-sm text-fg-muted"
      >
        No stages declared.
      </p>
    </section>
    <div class="min-w-0 space-y-6">
      <UiCallout
        v-if="extension && !extension.enabled"
        tone="warning"
      >
        Project extension is disabled. Showing the shared workflow contract.
      </UiCallout>
      <section aria-labelledby="workflow-inputs">
        <h2
          id="workflow-inputs"
          class="t-h2 mb-4 text-fg-strong"
        >
          Required information
        </h2>
        <div
          v-if="inputs.length"
          class="workflow-table-wrap"
        >
          <table class="workflow-table">
            <thead>
              <tr>
                <th scope="col">
                  Name
                </th><th scope="col">
                  Requirement
                </th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="input in inputs"
                :key="input.key"
              >
                <td><span :title="input.description">{{ input.name || input.key }}</span></td>
                <td><UiBadge>{{ input.required ? 'Required' : 'Optional' }}</UiBadge></td>
              </tr>
            </tbody>
          </table>
        </div>
        <p
          v-else
          class="text-sm text-fg-muted"
        >
          No inputs declared.
        </p>
      </section>
      <section
        v-if="spec.outputs?.length"
        aria-labelledby="workflow-outputs"
      >
        <h2
          id="workflow-outputs"
          class="t-h2 mb-4 text-fg-strong"
        >
          Outputs
        </h2>
        <div class="workflow-table-wrap">
          <table class="workflow-table">
            <thead>
              <tr>
                <th scope="col">
                  Name
                </th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="output in spec.outputs"
                :key="output.key"
              >
                <td :title="output.description">
                  {{ output.name || output.key }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
      <section
        v-if="spec.approval_gates?.length"
        aria-labelledby="workflow-checkpoints"
      >
        <h2
          id="workflow-checkpoints"
          class="t-h2 mb-4 text-fg-strong"
        >
          Checkpoints
        </h2>
        <ul class="workflow-checkpoints">
          <li
            v-for="gate in spec.approval_gates"
            :key="gate.key"
          >
            <p class="font-medium">
              {{ gate.key }}
            </p>
            <p
              v-if="gate.description"
              class="mt-1 text-xs text-fg-muted"
            >
              {{ gate.description }}
            </p>
            <p
              v-if="steps.some(step => step.approval_refs?.includes(gate.key))"
              class="mt-1 text-xs text-fg-muted"
            >
              Stages: {{ steps.filter(step => step.approval_refs?.includes(gate.key)).map(step => step.title || step.id).join(', ') }}
            </p>
          </li>
        </ul>
      </section>
      <UiAdvancedJsonPanel
        title="Details"
        summary=""
        :data="details"
        max-height="30rem"
      />
    </div>
  </div>
</template>

<style scoped>
.workflow-detail { display: grid; grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr); gap: 32px; overflow-wrap: anywhere; }
.workflow-stages, .workflow-table-wrap, .workflow-checkpoints { border: 1px solid var(--color-border-default); border-radius: var(--radius-lg); background: var(--color-bg-surface); overflow: hidden; }
.workflow-stages li { display: flex; align-items: flex-start; gap: 20px; padding: 12px 20px; }
.workflow-stages li + li, .workflow-checkpoints li + li { border-top: 1px solid var(--color-border-subtle); }
.workflow-stage-number { flex: 0 0 28px; min-height: 28px; display: grid; place-items: center; border-radius: var(--radius-sm); background: var(--color-bg-surface-alt); font-weight: 600; }
.workflow-table { width: 100%; table-layout: fixed; text-align: left; font-size: var(--fs-sm); }
.workflow-table th { background: var(--color-bg-surface-alt); text-transform: uppercase; font-size: var(--fs-xs); color: var(--color-fg-muted); }
.workflow-table th, .workflow-table td { padding: 10px 16px; border-bottom: 1px solid var(--color-border-subtle); }
.workflow-table tr:last-child td { border-bottom: 0; }
.workflow-checkpoints li { padding: 12px 16px; }
@media (max-width: 900px) { .workflow-detail { grid-template-columns: minmax(0, 1fr); gap: 24px; } }
</style>
