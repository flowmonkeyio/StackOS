<script setup lang="ts">
import { computed, onBeforeUpdate, toRef } from 'vue'
import type { components } from '@/api'
import { UiButton, UiCallout, UiFormField, UiLoadingState, UiSelect } from '@/components/ui'
import { agentIdentity, useProjectAgents } from '../agents/useProjectAgents'
import { useScheduleWorkflow, type ScheduleWorkflowState } from './useScheduleWorkflow'

type Execution = components['schemas']['ScheduleExecution']
type Agent = components['schemas']['TaskDeliveryAgentOverride']
const props = defineProps<{ projectId: number; projectDir: string; templateKey: string; hostKey: string; agents: NonNullable<Execution['agents']>; assignments: NonNullable<Execution['step_agent_assignments']>; disabled?: boolean; workflowState?: ScheduleWorkflowState }>()
const emit = defineEmits<{ 'update:agents': [value: NonNullable<Execution['agents']>]; 'update:assignments': [value: NonNullable<Execution['step_agent_assignments']>] }>()
const { items, loading, error, diagnostics, refresh } = useProjectAgents(toRef(props, 'projectId'), toRef(props, 'projectDir'))
const { state } = useScheduleWorkflow(toRef(props, 'projectId'), toRef(props, 'templateKey'), () => !props.workflowState)
const workflow = computed(() => (props.workflowState ?? state.value).workflow)
const workflowLoading = computed(() => (props.workflowState ?? state.value).loading)
const workflowError = computed(() => (props.workflowState ?? state.value).error)
const roles = computed(() => workflow.value?.spec?.agent_requirements ?? [])
const obsoleteRoles = computed(() => roles.value.length ? Object.keys(props.agents).filter(role => !['delivery_agent', 'review_agent'].includes(role) && !roles.value.some(requirement => requirement.role === role)) : [])
const displayRoles = computed(() => [...roles.value, ...Object.keys(props.agents).filter(role => !obsoleteRoles.value.includes(role) && !roles.value.some(requirement => requirement.role === role)).map(role => ({ role, purpose: 'Saved execution role. Select its project source or clear the override.' }))])
const obsoleteSteps = computed(() => Object.keys(props.assignments).filter(id => !workflow.value?.spec?.steps?.some(step => step.id === id)))
const nativeHost = computed(() => !props.hostKey || props.hostKey === 'codex')
const sourceScope = () => JSON.stringify([props.projectId, props.projectDir])
let loadedSource = sourceScope()
function updateScope() {
  if (loadedSource !== sourceScope()) { loadedSource = sourceScope(); void refresh() }
}
updateScope()
onBeforeUpdate(updateScope)
function value(role: string): string { const agent = props.agents[role]; return agent?.source_ref ? agentIdentity(agent.source_ref) : agent?.instructions ? '__inline__' : '' }
function options(role: string) {
  const result = [{ value: '', label: 'Use project defaults' }, ...items.value.filter(item => item.fields && !item.diagnostics?.length).map(item => ({ value: agentIdentity(item.source_ref), label: `${item.source_ref.native_name} · ${item.source_ref.source_path}` }))]
  const agent = props.agents[role]
  if (agent?.instructions && !agent.source_ref) result.push({ value: '__inline__', label: `${agent.name ?? role} (saved inline ${agent.host_key ?? (props.hostKey || 'agent app')} settings)` })
  if (agent?.source_ref && !result.some(option => option.value === value(role))) result.push({ value: value(role), label: `${agent.source_ref.native_name} · ${agent.source_ref.source_path} (missing or invalid)` })
  return result
}
function choose(role: string, selection: string | number | null) {
  if (selection === '__inline__') return
  const agents = { ...props.agents }
  const row = items.value.find(item => agentIdentity(item.source_ref) === selection)
  if (row) {
    const previous: Agent = { ...agents[role] }
    delete previous.instructions; delete previous.name
    agents[role] = { ...previous, host_key: 'codex', source_ref: row.source_ref }
  } else delete agents[role]
  emit('update:agents', agents)
}
const stepChoices = computed(() => (workflow.value?.spec?.steps ?? []).map(step => ({
  id: step.id, title: step.title,
  frozen: typeof step.extensions_json?.agent_assignment === 'string' ? step.extensions_json.agent_assignment : '',
  roles: roles.value.filter(role => !role.applies_to_steps?.length || role.applies_to_steps.includes(step.id)),
})).filter(step => step.roles.length > 1 || props.assignments[step.id]))
function assign(step: string, role: string | number | null) {
  const assignments = { ...props.assignments }
  if (role) assignments[step] = String(role); else delete assignments[step]
  emit('update:assignments', assignments)
}
function validate(): string | null {
  if (workflowLoading.value || (nativeHost.value && loading.value)) return 'Wait for workflow roles and project agents to load.'
  if (workflowError.value) return workflowError.value
  if (!nativeHost.value) return null
  if (obsoleteRoles.value.length || obsoleteSteps.value.length) return 'Remove saved roles or step assignments that are no longer declared by this workflow.'
  for (const [role, agent] of Object.entries(props.agents)) {
    if (agent.source_ref && !items.value.some(item => item.fields && !item.diagnostics?.length && agentIdentity(item.source_ref) === agentIdentity(agent.source_ref!))) return `${role}: the saved project agent is missing or invalid. Reassign it before saving.`
  }
  return null
}
defineExpose({ validate })
</script>

<template>
  <section
    aria-label="Workflow agents"
    class="space-y-4"
  >
    <h3 class="text-sm font-semibold text-fg-strong">
      Agents
    </h3>
    <UiLoadingState
      v-if="workflowLoading || (nativeHost && loading)"
      label="Loading workflow roles and project agents…"
    />
    <UiCallout
      v-if="workflowError || (nativeHost && error)"
      tone="warning"
      title="Agent selection unavailable"
    >
      {{ workflowError || error }}
    </UiCallout>
    <UiCallout
      v-if="!nativeHost"
      tone="info"
      title="Saved agent app settings"
    >
      {{ hostKey }} execution settings are preserved. Project file selection supports Codex; this app's native definitions cannot be edited here.
    </UiCallout>
    <UiCallout
      v-for="(diagnostic, i) in nativeHost ? diagnostics : []"
      :key="i"
      tone="warning"
      title="Project source needs attention"
    >
      {{ diagnostic.code }} {{ diagnostic.repair }}
    </UiCallout>
    <template v-if="nativeHost">
      <div
        v-for="role in obsoleteRoles"
        :key="role"
        class="text-sm text-warning-fg"
      >
        Saved role {{ role }} is no longer declared by this workflow.
        <UiButton
          :disabled="disabled || workflowLoading"
          @click="choose(role, '')"
        >
          Remove saved role
        </UiButton>
      </div>
      <div
        v-for="step in obsoleteSteps"
        :key="step"
        class="text-sm text-warning-fg"
      >
        Saved step {{ step }} is no longer declared by this workflow.
        <UiButton
          :disabled="disabled || workflowLoading"
          @click="assign(step, '')"
        >
          Remove saved step assignment
        </UiButton>
      </div>
      <UiFormField
        v-for="role in displayRoles"
        :key="role.role"
        :label="role.role"
        :help="role.purpose || 'Select a project agent or use the saved project assignment.'"
      >
        <UiSelect
          :model-value="value(role.role)"
          :options="options(role.role)"
          :disabled="disabled || loading || !projectDir"
          searchable
          @update:model-value="choose(role.role, $event)"
        />
        <p
          v-if="agents[role.role]?.source_ref && !items.some(item => item.fields && !item.diagnostics?.length && agentIdentity(item.source_ref) === value(role.role)) && !loading"
          class="text-xs text-danger-fg"
        >
          Saved source missing or invalid. Reassign before saving.
        </p>
      </UiFormField>
      <UiFormField
        v-for="step in stepChoices"
        :key="step.id"
        :label="`Agent role for ${step.title}`"
        help="Choose the declared role for this workflow step when more than one applies."
      >
        <UiSelect
          :model-value="assignments[step.id] ?? ''"
          :options="[{ value: '', label: step.frozen ? `Workflow assignment (${step.frozen})` : 'Use unambiguous workflow assignment' }, ...step.roles.map(role => ({ value: role.role, label: role.role }))]"
          :disabled="disabled"
          @update:model-value="assign(step.id, $event)"
        />
      </UiFormField>
      <p
        v-if="!projectDir"
        class="text-xs text-fg-muted"
      >
        Set the project folder in project setup to select its agents.
      </p>
      <p
        v-else-if="!loading && !items.length && !error"
        class="text-xs text-fg-muted"
      >
        No project agent definitions found in this folder.
      </p>
      <p class="text-xs text-fg-muted">
        References use the project files at the next admission. Already queued and running work keeps its saved snapshot.
      </p>
    </template>
  </section>
</template>
