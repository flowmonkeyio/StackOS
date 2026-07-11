<script setup lang="ts">
import { computed } from 'vue'

import type {
  SchemaLoadedWorkflowTemplate,
  SchemaWorkflowTemplateSpec,
  SchemaWorkflowTemplateExtensionOut,
} from '@/api'
import {
  UiAdvancedJsonPanel,
  UiBadge,
  UiCallout,
  UiCard,
  UiMetadataStrip,
} from '@/components/ui'
import type { UiMetadataStripItem } from '@/components/ui/UiMetadataStrip.vue'
import { sanitizeForDisplay } from '@/lib/stackos/json'
import WorkflowTemplateJourney from './WorkflowTemplateJourney.vue'

const props = defineProps<{
  template: SchemaLoadedWorkflowTemplate
}>()

const spec = computed<SchemaWorkflowTemplateSpec>(() => props.template.spec)
const projectExtension = computed<SchemaWorkflowTemplateExtensionOut | null>(
  () => props.template.project_extension ?? null,
)
const inputs = computed(() => spec.value.inputs ?? [])
const outputs = computed(() => spec.value.outputs ?? [])
const approvals = computed(() => spec.value.approval_gates ?? [])
const authRequirements = computed(() => spec.value.auth_requirements ?? [])
const capabilityRequirements = computed(() => spec.value.capability_requirements ?? [])
const agentRequirements = computed(() => spec.value.agent_requirements ?? [])
const requiredInputs = computed(() => inputs.value.filter((input) => input.required))
const requiredConnections = computed(() => authRequirements.value.filter((item) => !item.optional))
const projectRequiredInputKeys = computed(() => projectExtension.value?.required_input_keys_json ?? [])

const projectSetupAreas = computed(() => {
  if (!projectExtension.value) return []
  return [
    { label: 'Saved starting values', value: projectExtension.value.input_defaults_json },
    { label: 'Chosen project context', value: projectExtension.value.selected_context_json },
    { label: 'Safety guardrails', value: projectExtension.value.guardrails_json },
    { label: 'Stage adjustments', value: projectExtension.value.step_overrides_json },
    { label: 'Workflow changes', value: projectExtension.value.template_overrides_json },
  ].filter((item) => item.value && Object.keys(item.value).length > 0)
})

const templateFacts = computed<UiMetadataStripItem[]>(() => [
  { label: 'Area', value: spec.value.domain ? humanize(spec.value.domain) : 'General' },
  { label: 'Stages', value: spec.value.steps.length },
  { label: 'Required inputs', value: requiredInputs.value.length + projectRequiredInputKeys.value.length },
  { label: 'Human checkpoints', value: approvals.value.length },
  { label: 'Outcomes', value: outputs.value.length },
  { label: 'Version', value: spec.value.version },
])

const technicalContract = computed(() => sanitizeForDisplay({
  identity: {
    key: spec.value.key,
    schema_version: spec.value.schema_version,
    source: props.template.summary.source,
    plugin_slug: props.template.summary.plugin_slug,
    origin_path: props.template.summary.origin_path,
  },
  inputs: inputs.value,
  outputs: outputs.value,
  context_requirements: spec.value.context_requirements ?? [],
  action_contracts: spec.value.action_contracts ?? [],
  resource_contracts: spec.value.resource_contracts ?? [],
  policies: spec.value.policies ?? [],
  learning_hooks: spec.value.learning_hooks ?? [],
  agent_requirements: agentRequirements.value,
  skill_requirements: spec.value.skill_requirements ?? [],
  skill_preset_requirements: spec.value.skill_preset_requirements ?? [],
  steps: spec.value.steps,
  project_extension: projectExtension.value,
}))

function humanize(value: string): string {
  return value.replace(/[._:-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}
</script>

<template>
  <div class="space-y-5">
    <section class="workflow-overview overflow-hidden rounded-xl border border-default bg-bg-surface">
      <div class="workflow-overview__hero px-5 py-5 sm:px-6">
        <div class="max-w-3xl">
          <div class="flex flex-wrap items-center gap-2">
            <UiBadge tone="accent">
              At a glance
            </UiBadge>
            <UiBadge
              :tone="projectExtension?.enabled ? 'success' : projectExtension ? 'warning' : 'neutral'"
            >
              {{ projectExtension?.enabled ? 'Ready for this project' : projectExtension ? 'Turned off for this project' : 'Shared starting point' }}
            </UiBadge>
          </div>
          <h3 class="mt-4 text-xl font-semibold tracking-tight text-fg-strong">
            What this workflow helps accomplish
          </h3>
          <p class="mt-2 text-sm leading-6 text-fg-muted">
            {{ spec.description || 'A reusable path that guides a connected agent from project context to a reviewed outcome.' }}
          </p>
        </div>

        <div
          v-if="spec.when_to_use?.length || spec.when_not_to_use?.length"
          class="mt-5 grid gap-3 md:grid-cols-2"
        >
          <div
            v-if="spec.when_to_use?.length"
            class="workflow-overview__fit workflow-overview__fit--good"
          >
            <h4>Good fit when</h4>
            <ul>
              <li
                v-for="item in spec.when_to_use"
                :key="item"
              >
                {{ item }}
              </li>
            </ul>
          </div>
          <div
            v-if="spec.when_not_to_use?.length"
            class="workflow-overview__fit"
          >
            <h4>Choose another route when</h4>
            <ul>
              <li
                v-for="item in spec.when_not_to_use"
                :key="item"
              >
                {{ item }}
              </li>
            </ul>
          </div>
        </div>
      </div>
      <UiMetadataStrip
        :items="templateFacts"
        aria-label="Workflow at a glance"
      />
    </section>

    <WorkflowTemplateJourney :spec="spec" />

    <UiCard section>
      <template #header>
        <div>
          <p class="t-overline text-accent-primary">
            Before the agent starts
          </p>
          <h3 class="t-h2 mt-1 text-fg-strong">
            Project readiness
          </h3>
        </div>
        <UiBadge :tone="requiredInputs.length || requiredConnections.length ? 'warning' : 'success'">
          {{ requiredInputs.length + requiredConnections.length }} required items
        </UiBadge>
      </template>

      <UiCallout
        tone="info"
        density="compact"
      >
        People set the goal, connect approved tools, and confirm sensitive checkpoints. The agent then uses MCP to ask StackOS for the scoped plan and permitted actions.
      </UiCallout>

      <div class="mt-4 grid gap-3 lg:grid-cols-3">
        <section class="workflow-readiness-card">
          <h4>Information to provide</h4>
          <p v-if="!requiredInputs.length && !projectRequiredInputKeys.length">
            No required information is declared.
          </p>
          <ul v-else>
            <li
              v-for="input in requiredInputs"
              :key="input.key"
            >
              <strong>{{ input.name || humanize(input.key) }}</strong>
              <span>{{ input.description || 'Needed before this workflow can begin.' }}</span>
            </li>
            <li
              v-for="key in projectRequiredInputKeys"
              :key="key"
            >
              <strong>{{ humanize(key) }}</strong><span>Required by this project.</span>
            </li>
          </ul>
        </section>

        <section class="workflow-readiness-card">
          <h4>Connections and tools</h4>
          <p v-if="!authRequirements.length && !capabilityRequirements.length">
            No extra connection is declared.
          </p>
          <ul v-else>
            <li
              v-for="connection in authRequirements"
              :key="connection.key"
            >
              <strong>{{ humanize(connection.provider) }}</strong>
              <span>{{ connection.description || (connection.optional ? 'Optional connection.' : 'Must be connected before use.') }}</span>
            </li>
            <li
              v-for="capability in capabilityRequirements"
              :key="capability.key"
            >
              <strong>{{ humanize(capability.key) }}</strong><span>{{ capability.description }}</span>
            </li>
          </ul>
        </section>

        <section class="workflow-readiness-card">
          <h4>Who needs to be involved</h4>
          <p v-if="!agentRequirements.length && !approvals.length">
            Only the connected agent is declared.
          </p>
          <ul v-else>
            <li
              v-for="agent in agentRequirements"
              :key="agent.role"
            >
              <strong>{{ humanize(agent.role) }}</strong><span>{{ agent.purpose || 'Handles assigned stages.' }}</span>
            </li>
            <li
              v-for="approval in approvals"
              :key="approval.key"
            >
              <strong>{{ approval.approver ? humanize(approval.approver) : 'A human approver' }}</strong>
              <span>{{ approval.description || 'Reviews a protected checkpoint.' }}</span>
            </li>
          </ul>
        </section>
      </div>
    </UiCard>

    <div class="grid gap-5 lg:grid-cols-2">
      <UiCard section>
        <template #header>
          <h3 class="t-h2 text-fg-strong">
            What the user gets
          </h3><UiBadge>{{ outputs.length }}</UiBadge>
        </template>
        <p
          v-if="!outputs.length"
          class="text-sm text-fg-muted"
        >
          The template does not declare a named final outcome.
        </p>
        <ul
          v-else
          class="workflow-output-list"
        >
          <li
            v-for="output in outputs"
            :key="output.key"
          >
            <span aria-hidden="true">✓</span>
            <div><strong>{{ output.name || humanize(output.key) }}</strong><p>{{ output.description || 'A recorded outcome available to the next person or agent.' }}</p></div>
          </li>
        </ul>
      </UiCard>

      <UiCard section>
        <template #header>
          <h3 class="t-h2 text-fg-strong">
            Project setup
          </h3><UiBadge>{{ projectSetupAreas.length }}</UiBadge>
        </template>
        <UiCallout
          v-if="!projectExtension"
          tone="neutral"
          density="compact"
        >
          This project uses the shared workflow without project-specific changes.
        </UiCallout>
        <UiCallout
          v-else-if="!projectExtension.enabled"
          tone="warning"
          density="compact"
        >
          This workflow is currently disabled for this project.
        </UiCallout>
        <div
          v-else-if="projectSetupAreas.length"
          class="flex flex-wrap gap-2"
        >
          <UiBadge
            v-for="area in projectSetupAreas"
            :key="area.label"
            tone="success"
          >
            {{ area.label }}
          </UiBadge>
        </div>
        <p
          v-else
          class="text-sm text-fg-muted"
        >
          The workflow is enabled with its standard settings.
        </p>
      </UiCard>
    </div>

    <UiAdvancedJsonPanel
      title="Technical contract"
      summary="IDs, exact references, policies, and project overrides"
      :data="technicalContract"
      max-height="30rem"
    />
  </div>
</template>

<style scoped>
.workflow-overview__hero { background: radial-gradient(circle at 85% 10%, color-mix(in srgb, var(--color-accent-primary) 11%, transparent), transparent 34%), var(--color-bg-surface); }
.workflow-overview__fit { border: 1px solid var(--color-border-subtle); border-radius: var(--radius-md); background: var(--color-bg-surface-alt); padding: 12px; }
.workflow-overview__fit--good { border-color: color-mix(in srgb, var(--color-success-default) 30%, var(--color-border-subtle)); background: color-mix(in srgb, var(--color-success-default) 5%, var(--color-bg-surface)); }
.workflow-overview__fit h4, .workflow-readiness-card h4 { color: var(--color-fg-strong); font-size: var(--fs-xs); font-weight: var(--fw-semibold); }
.workflow-overview__fit ul { margin-top: 6px; list-style: disc; padding-left: 17px; color: var(--color-fg-muted); font-size: var(--fs-xs); line-height: 1.55; }
.workflow-readiness-card { min-width: 0; border: 1px solid var(--color-border-subtle); border-radius: var(--radius-lg); background: var(--color-bg-surface-alt); padding: 14px; }
.workflow-readiness-card > p { margin-top: 8px; color: var(--color-fg-muted); font-size: var(--fs-xs); }
.workflow-readiness-card ul { margin-top: 8px; display: grid; gap: 10px; }
.workflow-readiness-card li { display: grid; gap: 2px; }
.workflow-readiness-card strong { color: var(--color-fg-default); font-size: var(--fs-xs); font-weight: var(--fw-semibold); }
.workflow-readiness-card span { color: var(--color-fg-muted); font-size: var(--fs-xs); line-height: 1.45; }
.workflow-output-list { display: grid; gap: 10px; }
.workflow-output-list li { display: grid; grid-template-columns: 24px minmax(0, 1fr); gap: 10px; border-radius: var(--radius-md); background: var(--color-bg-surface-alt); padding: 12px; }
.workflow-output-list li > span { display: grid; width: 22px; height: 22px; place-items: center; border-radius: 999px; background: var(--color-success-subtle); color: var(--color-success-fg); font-size: 11px; font-weight: var(--fw-bold); }
.workflow-output-list strong { color: var(--color-fg-strong); font-size: var(--fs-sm); }.workflow-output-list p { margin-top: 3px; color: var(--color-fg-muted); font-size: var(--fs-xs); line-height: 1.5; }
</style>
