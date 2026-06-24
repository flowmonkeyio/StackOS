<script setup lang="ts">
import { computed } from 'vue'

import type {
  SchemaLoadedWorkflowTemplate,
  SchemaWorkflowAgentRequirementSpec,
  SchemaWorkflowSkillPresetRequirementSpec,
  SchemaWorkflowSkillRequirementSpec,
  SchemaWorkflowTemplateSpec,
  SchemaWorkflowTemplateExtensionOut,
} from '@/api'
import type { BadgeTone } from '@/components/ui/UiBadge.vue'
import {
  UiAdvancedJsonPanel,
  UiBadge,
  UiCallout,
  UiCard,
  UiCountBadge,
  UiDescriptionList,
  UiIcon,
  UiJsonBlock,
  UiMetadataStrip,
} from '@/components/ui'
import type { UiMetadataStripItem } from '@/components/ui/UiMetadataStrip.vue'
import { sanitizeForDisplay } from '@/lib/stackos/json'

type TemplateStep = SchemaWorkflowTemplateSpec['steps'][number]

const props = defineProps<{
  template: SchemaLoadedWorkflowTemplate
}>()

const spec = computed<SchemaWorkflowTemplateSpec>(() => props.template.spec)
const projectExtension = computed<SchemaWorkflowTemplateExtensionOut | null>(
  () => props.template.project_extension ?? null,
)
const contextRequirements = computed(() => spec.value.context_requirements ?? [])
const agentRequirements = computed(() => spec.value.agent_requirements ?? [])
const skillRequirements = computed(() => spec.value.skill_requirements ?? [])
const skillPresetRequirements = computed(() => spec.value.skill_preset_requirements ?? [])
const inputs = computed(() => spec.value.inputs ?? [])
const outputs = computed(() => spec.value.outputs ?? [])
const approvals = computed(() => spec.value.approval_gates ?? [])
const policies = computed(() => spec.value.policies ?? [])
const hooks = computed(() => spec.value.learning_hooks ?? [])
const rawTemplate = computed(() => sanitizeForDisplay(spec.value))
const extensionJson = computed(() =>
  projectExtension.value ? sanitizeForDisplay(projectExtension.value) : null,
)
const extensionRequiredInputs = computed(() => projectExtension.value?.required_input_keys_json ?? [])

const extensionKeyGroups = computed<{ title: string; keys: string[]; tone: BadgeTone }[]>(() => [
  { title: 'Defaults', keys: objectKeys(projectExtension.value?.input_defaults_json), tone: 'neutral' },
  { title: 'Context', keys: objectKeys(projectExtension.value?.selected_context_json), tone: 'neutral' },
  { title: 'Step overrides', keys: objectKeys(projectExtension.value?.step_overrides_json), tone: 'neutral' },
  { title: 'Guardrails', keys: objectKeys(projectExtension.value?.guardrails_json), tone: 'neutral' },
  { title: 'Workflow changes', keys: objectKeys(projectExtension.value?.template_overrides_json), tone: 'accent' },
])

function objectKeys(value: Record<string, unknown> | null | undefined): string[] {
  if (!value || typeof value !== 'object') return []
  return Object.keys(value)
}

function requirementTone(
  requirement:
    | SchemaWorkflowAgentRequirementSpec['requirement']
    | SchemaWorkflowSkillPresetRequirementSpec['requirement']
    | SchemaWorkflowSkillRequirementSpec['requirement'],
): BadgeTone {
  if (requirement === 'required') return 'warning'
  if (requirement === 'recommended') return 'info'
  return 'neutral'
}

const templateFacts = computed<UiMetadataStripItem[]>(() => [
  { label: 'Key', value: spec.value.key, mono: true },
  { label: 'Domain', value: spec.value.domain ?? null },
  {
    label: 'Origin',
    value: props.template.summary.origin_path ?? null,
    mono: true,
    title: props.template.summary.origin_path ?? undefined,
  },
  { label: 'Source', value: props.template.summary.source },
  { label: 'Plugin', value: props.template.summary.plugin_slug ?? null },
  { label: 'Version', value: spec.value.version },
])

function stepContracts(step: TemplateStep): { label: string; value: string }[] {
  return [
    { label: 'Context', refs: step.context_refs ?? [] },
    { label: 'Actions', refs: step.action_refs ?? [] },
    { label: 'Approvals', refs: step.approval_refs ?? [] },
    { label: 'Outputs', refs: step.output_refs ?? [] },
  ]
    .filter((group) => group.refs.length > 0)
    .map((group) => ({ label: group.label, value: group.refs.join(', ') }))
}
</script>

<template>
  <div class="space-y-5">
    <!-- Key facts only — the drawer header already shows the template name and description. -->
    <UiMetadataStrip
      :items="templateFacts"
      aria-label="Template facts"
    />

    <UiCard section>
      <template #header>
        <h3 class="t-h3 text-fg-strong">
          Project Setup
        </h3>
        <div class="flex shrink-0 items-center gap-1.5">
          <UiBadge
            v-if="projectExtension?.enabled"
            tone="success"
          >
            Active
          </UiBadge>
          <UiBadge
            v-else-if="projectExtension"
            tone="warning"
          >
            Disabled
          </UiBadge>
          <UiBadge v-else>
            Shared template
          </UiBadge>
        </div>
      </template>

      <UiCallout
        v-if="!projectExtension"
        tone="info"
        density="compact"
      >
        This project is using the shared workflow template as-is.
      </UiCallout>

      <div
        v-else
        class="space-y-4"
      >
        <p class="text-xs text-fg-muted">
          Project-specific defaults, context, guardrails, and workflow changes for this template.
        </p>

        <UiDescriptionList
          layout="grid"
          :columns="2"
          :items="[
            { label: 'Workflow key', value: projectExtension.workflow_key, mono: true },
            { label: 'Extension ID', value: projectExtension.id, mono: true },
          ]"
        />

        <div
          v-if="extensionRequiredInputs.length"
          class="space-y-1.5"
        >
          <h4 class="text-xs font-medium text-fg-muted">
            Required inputs
          </h4>
          <div class="flex flex-wrap gap-1.5">
            <UiBadge
              v-for="key in extensionRequiredInputs"
              :key="key"
              tone="warning"
            >
              {{ key }}
            </UiBadge>
          </div>
        </div>

        <div class="grid gap-2 md:grid-cols-2">
          <section
            v-for="group in extensionKeyGroups"
            :key="group.title"
            class="rounded-md border border-subtle bg-bg-surface-alt p-2.5"
          >
            <h4 class="text-xs font-medium text-fg-muted">
              {{ group.title }}
            </h4>
            <div class="mt-1.5 flex flex-wrap gap-1.5">
              <UiBadge
                v-for="key in group.keys"
                :key="key"
                :tone="group.tone"
              >
                {{ key }}
              </UiBadge>
              <span
                v-if="group.keys.length === 0"
                class="text-xs text-fg-subtle"
              >
                —
              </span>
            </div>
          </section>
        </div>

        <UiJsonBlock
          :data="extensionJson"
          max-height="18rem"
          density="compact"
          wrap
          aria-label="Project workflow extension JSON"
        />
      </div>
    </UiCard>

    <UiCard section>
      <template #header>
        <h3 class="t-h3 text-fg-strong">
          Agents & skills
        </h3>
        <div class="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
          <UiBadge>{{ agentRequirements.length }} agents</UiBadge>
          <UiBadge>{{ skillRequirements.length }} skills</UiBadge>
          <UiBadge>{{ skillPresetRequirements.length }} presets</UiBadge>
        </div>
      </template>

      <div class="space-y-4">
        <p class="text-xs text-fg-muted">
          Generic presets and host skills the caller should adapt before running this workflow.
        </p>

        <div class="grid gap-4 lg:grid-cols-3">
          <section class="min-w-0 space-y-2">
            <h4 class="text-xs font-medium text-fg-muted">
              Agents
            </h4>
            <p
              v-if="agentRequirements.length === 0"
              class="text-sm text-fg-muted"
            >
              No agent requirements.
            </p>
            <ul
              v-else
              class="space-y-2"
            >
              <li
                v-for="agent in agentRequirements"
                :key="agent.role"
                class="rounded-md border border-subtle bg-bg-surface-alt p-2.5"
              >
                <div class="flex flex-wrap items-center gap-2">
                  <span class="text-sm font-medium text-fg-default">{{ agent.role }}</span>
                  <UiBadge :tone="requirementTone(agent.requirement)">
                    {{ agent.requirement }}
                  </UiBadge>
                </div>
                <p
                  class="mt-0.5 truncate font-mono text-2xs text-fg-subtle"
                  :title="agent.agent_preset_ref"
                >
                  {{ agent.agent_preset_ref }}
                </p>
                <p
                  v-if="agent.purpose"
                  class="mt-1.5 line-clamp-3 text-xs text-fg-muted"
                >
                  {{ agent.purpose }}
                </p>
                <div
                  v-if="agent.applies_to_steps?.length"
                  class="mt-1.5 flex flex-wrap gap-1"
                >
                  <UiBadge
                    v-for="step in agent.applies_to_steps"
                    :key="step"
                    size="sm"
                    tone="accent"
                  >
                    {{ step }}
                  </UiBadge>
                </div>
                <ul
                  v-if="agent.handoff_notes?.length"
                  class="mt-1.5 space-y-1 text-xs text-fg-muted"
                >
                  <li
                    v-for="note in agent.handoff_notes"
                    :key="note"
                  >
                    {{ note }}
                  </li>
                </ul>
              </li>
            </ul>
          </section>

          <section class="min-w-0 space-y-2">
            <h4 class="text-xs font-medium text-fg-muted">
              Skill presets
            </h4>
            <p
              v-if="skillPresetRequirements.length === 0"
              class="text-sm text-fg-muted"
            >
              No skill presets.
            </p>
            <ul
              v-else
              class="space-y-2"
            >
              <li
                v-for="preset in skillPresetRequirements"
                :key="preset.skill_preset_ref"
                class="rounded-md border border-subtle bg-bg-surface-alt p-2.5"
              >
                <div class="flex flex-wrap items-center gap-2">
                  <span
                    class="min-w-0 truncate font-mono text-xs font-medium text-fg-default"
                    :title="preset.skill_preset_ref"
                  >
                    {{ preset.skill_preset_ref }}
                  </span>
                  <UiBadge :tone="requirementTone(preset.requirement)">
                    {{ preset.requirement }}
                  </UiBadge>
                </div>
                <p
                  v-if="preset.purpose"
                  class="mt-1.5 line-clamp-3 text-xs text-fg-muted"
                >
                  {{ preset.purpose }}
                </p>
                <div
                  v-if="preset.applies_to_steps?.length"
                  class="mt-1.5 flex flex-wrap gap-1"
                >
                  <UiBadge
                    v-for="step in preset.applies_to_steps"
                    :key="step"
                    size="sm"
                    tone="accent"
                  >
                    {{ step }}
                  </UiBadge>
                </div>
                <ul
                  v-if="preset.setup_notes?.length"
                  class="mt-1.5 space-y-1 text-xs text-fg-muted"
                >
                  <li
                    v-for="note in preset.setup_notes"
                    :key="note"
                  >
                    {{ note }}
                  </li>
                </ul>
              </li>
            </ul>
          </section>

          <section class="min-w-0 space-y-2">
            <h4 class="text-xs font-medium text-fg-muted">
              Skills
            </h4>
            <p
              v-if="skillRequirements.length === 0"
              class="text-sm text-fg-muted"
            >
              No skill requirements.
            </p>
            <ul
              v-else
              class="space-y-2"
            >
              <li
                v-for="skill in skillRequirements"
                :key="skill.skill_ref"
                class="rounded-md border border-subtle bg-bg-surface-alt p-2.5"
              >
                <div class="flex flex-wrap items-center gap-2">
                  <span
                    class="min-w-0 truncate font-mono text-xs font-medium text-fg-default"
                    :title="skill.skill_ref"
                  >
                    {{ skill.skill_ref }}
                  </span>
                  <UiBadge :tone="requirementTone(skill.requirement)">
                    {{ skill.requirement }}
                  </UiBadge>
                </div>
                <p
                  v-if="skill.purpose"
                  class="mt-1.5 line-clamp-3 text-xs text-fg-muted"
                >
                  {{ skill.purpose }}
                </p>
                <div
                  v-if="skill.applies_to_steps?.length"
                  class="mt-1.5 flex flex-wrap gap-1"
                >
                  <UiBadge
                    v-for="step in skill.applies_to_steps"
                    :key="step"
                    size="sm"
                    tone="accent"
                  >
                    {{ step }}
                  </UiBadge>
                </div>
                <ul
                  v-if="skill.setup_notes?.length"
                  class="mt-1.5 space-y-1 text-xs text-fg-muted"
                >
                  <li
                    v-for="note in skill.setup_notes"
                    :key="note"
                  >
                    {{ note }}
                  </li>
                </ul>
              </li>
            </ul>
          </section>
        </div>
      </div>
    </UiCard>

    <UiCard section>
      <template #header>
        <h3 class="t-h3 text-fg-strong">
          Workflow steps
        </h3>
        <UiBadge>{{ spec.steps.length }} steps</UiBadge>
      </template>

      <ol class="space-y-2">
        <li
          v-for="(step, index) in spec.steps"
          :key="step.id"
          class="rounded-md border border-subtle bg-bg-surface-alt p-2.5"
        >
          <div class="flex flex-wrap items-center gap-2">
            <span
              class="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-bg-sunken text-2xs font-medium text-fg-muted"
              aria-hidden="true"
            >
              {{ index + 1 }}
            </span>
            <span class="text-sm font-medium text-fg-default">{{ step.title }}</span>
            <span class="font-mono text-2xs text-fg-subtle">{{ step.id }}</span>
          </div>
          <p
            v-if="step.purpose"
            class="mt-1 text-xs text-fg-muted"
          >
            {{ step.purpose }}
          </p>
          <details
            v-if="stepContracts(step).length > 0"
            class="group mt-2 rounded-md border border-subtle bg-bg-surface"
          >
            <summary class="focus-ring flex cursor-pointer list-none items-center gap-1.5 rounded-md px-2.5 py-1.5 text-2xs font-medium text-fg-muted transition-colors duration-fast hover:text-fg-default [&::-webkit-details-marker]:hidden">
              <UiIcon
                name="chevron-right"
                class="h-3 w-3 shrink-0 text-fg-subtle transition-transform duration-fast group-open:rotate-90"
                aria-hidden="true"
              />
              Contracts
            </summary>
            <UiDescriptionList
              class="border-t border-subtle px-2.5 py-2"
              layout="grid"
              :columns="2"
              density="compact"
              :items="stepContracts(step).map((group) => ({ ...group, mono: true }))"
              :aria-label="`${step.title} contracts`"
            />
          </details>
        </li>
      </ol>
    </UiCard>

    <UiCard section>
      <template #header>
        <h3 class="t-h3 text-fg-strong">
          Contracts
        </h3>
        <div class="flex shrink-0 items-center gap-1.5">
          <UiBadge>{{ inputs.length }} inputs</UiBadge>
          <UiBadge>{{ outputs.length }} outputs</UiBadge>
        </div>
      </template>

      <div class="grid gap-4 lg:grid-cols-2">
        <section class="min-w-0 space-y-2">
          <h4 class="text-xs font-medium text-fg-muted">
            Inputs
          </h4>
          <p
            v-if="inputs.length === 0"
            class="text-sm text-fg-muted"
          >
            No declared inputs.
          </p>
          <ul
            v-else
            class="space-y-2"
          >
            <li
              v-for="input in inputs"
              :key="input.key"
              class="flex items-center justify-between gap-3 rounded-md border border-subtle bg-bg-surface-alt px-2.5 py-2 text-sm"
            >
              <span class="min-w-0 truncate font-medium text-fg-default">
                {{ input.name ?? input.key }}
              </span>
              <UiBadge :tone="input.required ? 'warning' : 'neutral'">
                {{ input.type }}
              </UiBadge>
            </li>
          </ul>
        </section>
        <section class="min-w-0 space-y-2">
          <h4 class="text-xs font-medium text-fg-muted">
            Outputs
          </h4>
          <p
            v-if="outputs.length === 0"
            class="text-sm text-fg-muted"
          >
            No declared outputs.
          </p>
          <ul
            v-else
            class="space-y-2"
          >
            <li
              v-for="output in outputs"
              :key="output.key"
              class="flex items-center justify-between gap-3 rounded-md border border-subtle bg-bg-surface-alt px-2.5 py-2 text-sm"
            >
              <span class="min-w-0 truncate font-medium text-fg-default">
                {{ output.name ?? output.key }}
              </span>
              <UiBadge>{{ output.type }}</UiBadge>
            </li>
          </ul>
        </section>
      </div>
    </UiCard>

    <UiCard section>
      <template #header>
        <div class="flex min-w-0 items-center gap-2">
          <h3 class="t-h3 text-fg-strong">
            Context
          </h3>
          <UiCountBadge :value="contextRequirements.length" />
        </div>
      </template>

      <p
        v-if="contextRequirements.length === 0"
        class="text-sm text-fg-muted"
      >
        No context requirements.
      </p>
      <ul
        v-else
        class="space-y-2"
      >
        <li
          v-for="req in contextRequirements"
          :key="req.id"
          class="rounded-md border border-subtle bg-bg-surface-alt p-2.5"
        >
          <div class="flex flex-wrap items-center gap-2">
            <span class="text-sm font-medium text-fg-default">{{ req.id }}</span>
            <UiBadge tone="info">
              {{ req.source }}
            </UiBadge>
          </div>
          <p
            v-if="req.purpose"
            class="mt-1 text-xs text-fg-muted"
          >
            {{ req.purpose }}
          </p>
        </li>
      </ul>
    </UiCard>

    <UiCard section>
      <template #header>
        <h3 class="t-h3 text-fg-strong">
          Rules
        </h3>
        <div class="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
          <UiBadge>{{ approvals.length }} approvals</UiBadge>
          <UiBadge>{{ policies.length }} policies</UiBadge>
          <UiBadge>{{ hooks.length }} hooks</UiBadge>
        </div>
      </template>

      <div class="space-y-2">
        <UiAdvancedJsonPanel
          title="Approvals"
          :summary="`${approvals.length} entries`"
          :data="sanitizeForDisplay(approvals)"
          max-height="12rem"
        />
        <UiAdvancedJsonPanel
          title="Policies"
          :summary="`${policies.length} entries`"
          :data="sanitizeForDisplay(policies)"
          max-height="12rem"
        />
        <UiAdvancedJsonPanel
          title="Learning hooks"
          :summary="`${hooks.length} entries`"
          :data="sanitizeForDisplay(hooks)"
          max-height="12rem"
        />
      </div>
    </UiCard>

    <UiAdvancedJsonPanel
      title="Template JSON"
      summary="Raw template spec"
      :data="rawTemplate"
      max-height="24rem"
    />
  </div>
</template>
