<script setup lang="ts">
import { computed, inject, onBeforeUnmount, ref, toRef } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, routerKey } from 'vue-router'
import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import { resolveAgentHostLogo } from '@/lib/stackos/agentIdentity'
import { UiButton, UiCallout, UiConfirmDialog, UiFormField, UiIcon, UiIconButton, UiInput, UiLoadingState, UiSelect, UiTextarea } from '@/components/ui'
import { useProjectFolder } from '@/composables/useProjectFolder'
import { agentError, agentIdentity, useProjectAgents, type AgentSummary, type AgentFields } from './useProjectAgents'

const props = defineProps<{ projectId: number; standalone?: boolean }>()
const codexLogo = resolveAgentHostLogo('codex')
let reloadAgents = () => {}
const { projectDir, folderIssue, folderLoading, refreshFolder } = useProjectFolder(toRef(props, 'projectId'), () => reloadAgents())
const search = ref('')
const features = ref('null')
const skills = ref('null')
const customModel = ref(false)
function resetEditorOptions(fields: AgentFields | null) {
  features.value = JSON.stringify(fields?.features ?? null, null, 2)
  skills.value = JSON.stringify(fields?.skills ?? null, null, 2)
  customModel.value = false
}
const { items, diagnostics, loading, reading, saving, error, message, selected, draft, dirty, refresh, edit, add, close, save } = useProjectAgents(toRef(props, 'projectId'), projectDir, resetEditorOptions)
reloadAgents = () => { close(); void refresh() }
const visible = computed(() => items.value.filter(item => `${item.source_ref.native_name} ${item.fields?.description ?? ''} ${item.source_ref.source_path}`.toLowerCase().includes(search.value.toLowerCase())))
const title = (name: string) => name.replace(/[_-]/g, ' ').replace(/^./, value => value.toUpperCase()).replace(/\bseo\b/gi, 'SEO')
const pending = ref<(() => void) | null>(null)
let cancelNavigation: (() => void) | null = null
function cancelDiscard() { pending.value = null; cancelNavigation?.(); cancelNavigation = null }
function protect(action: () => void) { if (saving.value) return; if (dirty.value) pending.value = action; else action() }
function discard() { const action = pending.value; pending.value = null; action?.() }
const editor = ref<HTMLElement | null>(null)
const optionsError = ref('')
const modelOptions = computed(() => [{ value: '', label: 'Default (inherited)' }, ...(draft.value?.model ? [{ value: draft.value.model, label: draft.value.model }] : []), { value: '__custom__', label: 'Custom model…' }])
function selectModel(value: string | number | null) {
  customModel.value = value === '__custom__'
  if (draft.value && !customModel.value) draft.value.model = String(value ?? '') || null
}
async function submit() {
  if (!draft.value) return
  optionsError.value = ''
  try {
    for (const key of ['features', 'skills'] as const) {
      const value: unknown = JSON.parse(key === 'features' ? features.value : skills.value)
      if (value !== null && (typeof value !== 'object' || Array.isArray(value))) throw new Error(`${key} must be a JSON object or null.`)
      draft.value[key] = value as AgentFields[typeof key]
    }
    await save()
  } catch (cause) { optionsError.value = agentError(cause) }
}
const optionsDirty = computed(() => draft.value !== null && (features.value !== JSON.stringify(draft.value.features ?? null, null, 2) || skills.value !== JSON.stringify(draft.value.skills ?? null, null, 2)))
const hasChanges = computed(() => dirty.value || optionsDirty.value)
if (inject(routerKey, null)) {
  const protectNavigation = () => {
    if (saving.value) return false
    if (!hasChanges.value) return true
    return new Promise<boolean>(resolve => { pending.value = () => { cancelNavigation = null; resolve(true) }; cancelNavigation = () => resolve(false) })
  }
  onBeforeRouteLeave(protectNavigation)
  onBeforeRouteUpdate(protectNavigation)
}
function guarded(action: () => void) { if (optionsDirty.value && !dirty.value) pending.value = action; else protect(action) }
const settings: { key: 'model_reasoning_effort' | 'model_reasoning_summary' | 'model_verbosity'; label: string; values: string[] }[] = [
  { key: 'model_reasoning_effort', label: 'Reasoning effort', values: ['none', 'minimal', 'low', 'medium', 'high', 'xhigh'] },
  { key: 'model_reasoning_summary', label: 'Reasoning summary', values: ['auto', 'concise', 'detailed', 'none'] },
  { key: 'model_verbosity', label: 'Verbosity', values: ['low', 'medium', 'high'] },
]
function beforeUnload(event: BeforeUnloadEvent) { if (hasChanges.value) { event.preventDefault(); event.returnValue = '' } }
window.addEventListener('beforeunload', beforeUnload)
onBeforeUnmount(() => { cancelNavigation?.(); window.removeEventListener('beforeunload', beforeUnload) })
function select(row: AgentSummary) { guarded(() => { void edit(row).then(() => editor.value?.querySelector<HTMLInputElement>('input')?.focus()) }) }
</script>

<template>
  <section
    class="space-y-4"
    aria-label="Project agents"
  >
    <ProjectPageHeader
      v-if="standalone"
      class="!pb-0"
      :project-id="projectId"
      title="Agents"
      description="Agents defined in this project."
    >
      <template #actions>
        <UiButton
          icon-left="refresh"
          :disabled="loading || reading || saving || !projectDir"
          @click="guarded(() => { close(); void refresh() })"
        >
          Refresh
        </UiButton>
        <UiButton
          icon-left="plus"
          variant="primary"
          :disabled="saving || !projectDir || loading"
          @click="guarded(add)"
        >
          Add agent
        </UiButton>
      </template>
    </ProjectPageHeader>
    <div
      class="flex flex-wrap items-start justify-between gap-3"
      :class="{ '!mt-2': standalone }"
    >
      <div class="min-w-0 flex-1 space-y-2">
        <p
          v-if="!standalone"
          class="text-sm text-fg-muted"
        >
          Agents defined in this project.
        </p>
        <p
          v-if="projectDir"
          class="flex items-start gap-2 text-sm text-fg-default break-all"
        >
          <UiIcon
            name="folder"
            class="mt-0.5 h-4 w-4 shrink-0"
          />Project folder: {{ projectDir }}
        </p>
      </div>
      <div
        v-if="!standalone"
        class="flex gap-2"
      >
        <UiButton
          icon-left="refresh"
          :disabled="loading || reading || saving || !projectDir"
          @click="guarded(() => { close(); void refresh() })"
        >
          Refresh
        </UiButton>
        <UiButton
          variant="primary"
          icon-left="plus"
          :disabled="saving || !projectDir || loading"
          @click="guarded(add)"
        >
          Add agent
        </UiButton>
      </div>
    </div>
    <UiLoadingState
      v-if="folderLoading"
      label="Loading project folder…"
    />
    <UiCallout
      v-if="!folderLoading && folderIssue"
      tone="warning"
      title="Project folder needs setup"
    >
      {{ folderIssue }}
      <UiButton
        @click="guarded(() => { void refreshFolder() })"
      >
        Retry project folder
      </UiButton>
    </UiCallout>
    <UiCallout
      v-if="error"
      tone="danger"
      title="Agent source requires attention"
    >
      {{ error }} Your draft is retained. Refresh discards it only after confirmation.
    </UiCallout>
    <UiCallout
      v-for="(diagnostic, index) in diagnostics"
      :key="index"
      tone="warning"
      title="Project source unavailable"
    >
      {{ diagnostic.code }} {{ diagnostic.repair }}
    </UiCallout>
    <div
      class="agents-layout"
      :class="{ 'agents-layout--editing': draft }"
    >
      <div class="min-w-0">
        <UiInput
          v-model="search"
          aria-label="Find an agent"
          placeholder="Find an agent"
          class="mb-3"
        />
        <UiLoadingState
          v-if="loading"
          label="Loading project agents…"
        />
        <div
          v-else-if="visible.length"
          class="agent-list rounded-lg border border-default overflow-hidden"
        >
          <div class="agent-columns bg-bg-surface-alt px-4 py-2 text-xs font-semibold uppercase text-fg-muted">
            <span>Agent</span><span>App</span>
          </div>
          <button
            v-for="row in visible"
            :key="agentIdentity(row.source_ref)"
            type="button"
            class="agent-columns agent-row focus-ring w-full border-t border-subtle px-4 py-3 text-left"
            :class="{ 'agent-row--selected': selected && agentIdentity(selected.source_ref) === agentIdentity(row.source_ref) }"
            :aria-pressed="Boolean(selected && agentIdentity(selected.source_ref) === agentIdentity(row.source_ref))"
            :disabled="reading || saving"
            @click="select(row)"
          >
            <span class="min-w-0"><span
              class="block truncate font-semibold text-fg-strong"
              :title="row.source_ref.native_name"
            >{{ title(row.source_ref.native_name) }}</span><span
              class="block truncate text-xs text-fg-muted"
              :title="row.fields?.description ?? row.source_ref.source_path"
            >{{ row.fields?.description ?? row.source_ref.source_path }}</span><span
              v-for="(diagnostic, i) in row.diagnostics"
              :key="i"
              class="block text-xs text-warning-fg"
            >{{ diagnostic.code }} {{ diagnostic.repair }}</span></span>
            <span class="flex items-center gap-2 text-sm text-fg-default"><img
              v-if="codexLogo"
              :src="codexLogo"
              alt=""
              class="h-5 w-5 shrink-0"
            >Codex</span>
          </button>
        </div>
        <p
          v-else-if="projectDir && !error"
          class="rounded-lg border border-default p-6 text-sm text-fg-muted"
        >
          {{ search ? 'No matching agents.' : 'No project agents yet. Add an agent to create its project file.' }}
        </p>
        <p class="mt-3 text-xs text-fg-muted">
          Project file editing supports Codex. Other agent apps keep their existing execution settings.
        </p>
      </div>
      <UiLoadingState
        v-if="reading"
        label="Reading full project instructions…"
      />
      <aside
        v-if="draft"
        ref="editor"
        class="agent-editor"
        aria-label="Agent editor"
      >
        <div class="mb-4 border-b border-subtle pb-4">
          <div class="flex items-start justify-between gap-3">
            <h2 class="min-w-0 break-words text-xl font-semibold text-fg-strong">
              {{ selected ? title(selected.source_ref.native_name) : 'Add agent' }}
            </h2><UiIconButton
              aria-label="Close agent editor"
              :disabled="saving"
              @click="guarded(close)"
            >
              <UiIcon
                name="close"
                class="h-4 w-4"
              />
            </UiIconButton>
          </div>
          <p class="mt-1 break-all text-xs text-fg-muted">
            {{ selected?.source_ref.source_path ?? (draft.name ? `.codex/agents/${draft.name}.toml` : '.codex/agents/') }}
          </p>
        </div>
        <form
          class="space-y-4"
          @submit.prevent="submit"
        >
          <UiFormField
            label="Name"
            required
            :help="selected && draft.name !== selected.fields?.name ? 'Changing the native name keeps this file in place. Reassign saved assignments and automations before renaming a referenced agent.' : undefined"
          >
            <UiInput
              v-model="draft.name"
              :disabled="saving || selected?.capabilities?.edit === false"
              required
              pattern="[a-zA-Z0-9_][a-zA-Z0-9_\-]*"
            />
          </UiFormField>
          <UiFormField
            label="Description"
            required
          >
            <UiTextarea
              v-model="draft.description"
              :rows="2"
              required
              :disabled="saving || selected?.capabilities?.edit === false"
            />
          </UiFormField>
          <div class="grid grid-cols-2 gap-4 agent-settings">
            <UiFormField label="Agent app">
              <UiInput
                model-value="Codex"
                disabled
              />
            </UiFormField>
            <UiFormField
              label="Model"
            >
              <UiSelect
                :model-value="customModel ? '__custom__' : draft.model ?? ''"
                :options="modelOptions"
                :disabled="saving || selected?.capabilities?.edit === false"
                @update:model-value="selectModel"
              />
            </UiFormField>
          </div>
          <UiFormField
            v-if="customModel"
            label="Custom model"
            help="Exact native model name. Blank inherits execution settings."
          >
            <UiInput
              :model-value="draft.model ?? ''"
              :disabled="saving || selected?.capabilities?.edit === false"
              @update:model-value="draft.model = String($event ?? '') || null"
            />
          </UiFormField>
          <UiFormField
            label="Instructions"
            required
            help="Full instructions from the project file."
          >
            <UiTextarea
              v-model="draft.developer_instructions"
              :rows="9"
              required
              :disabled="saving || selected?.capabilities?.edit === false"
            />
          </UiFormField>
          <details class="border-y border-subtle py-3">
            <summary class="focus-ring cursor-pointer rounded-sm text-sm font-semibold text-fg-default">
              More options
            </summary>
            <div class="mt-4 space-y-4">
              <UiFormField
                v-for="setting in settings"
                :key="setting.key"
                :label="setting.label"
              >
                <UiSelect
                  :model-value="draft[setting.key] ?? ''"
                  :options="[{ value: '', label: 'Default (inherited)' }, ...setting.values.map(value => ({ value, label: value }))]"
                  :disabled="saving || selected?.capabilities?.edit === false"
                  @update:model-value="draft[setting.key] = String($event ?? '') || null"
                />
              </UiFormField>
              <UiFormField
                label="Feature restrictions"
                help="Native JSON object or null to inherit. Only supported restrictions can be saved."
              >
                <UiTextarea
                  v-model="features"
                  :rows="3"
                  :disabled="saving || selected?.capabilities?.edit === false"
                />
              </UiFormField>
              <UiFormField
                label="Skill restrictions"
                help="Native JSON object or null to inherit. File settings cannot grant permissions or credentials."
              >
                <UiTextarea
                  v-model="skills"
                  :rows="3"
                  :disabled="saving || selected?.capabilities?.edit === false"
                />
              </UiFormField>
              <p
                v-if="selected?.unsupported_fields?.length"
                class="break-words text-xs text-warning-fg"
              >
                Preserved native fields with unsupported execution settings: {{ selected.unsupported_fields.join(', ') }}. Their execution support is not implied.
              </p>
              <p class="text-xs text-fg-muted">
                Blank model and Default options inherit task, project, then agent app settings. Untouched native options and comments are preserved. Permissions, providers and MCP configuration remain execution context.
              </p>
            </div>
          </details>
          <UiCallout
            v-if="optionsError"
            tone="danger"
            title="Check native options"
          >
            {{ optionsError }}
          </UiCallout>
          <div class="flex flex-wrap items-center gap-3 border-t border-subtle pt-4">
            <UiButton
              type="submit"
              variant="primary"
              :loading="saving"
              :disabled="!hasChanges || reading || selected?.capabilities?.edit === false"
            >
              {{ selected ? 'Save changes' : 'Add agent' }}
            </UiButton><span class="text-xs text-fg-muted">{{ hasChanges ? 'Unsaved changes' : 'No changes' }}</span><span class="text-xs text-fg-muted sm:ml-auto">Changes apply to future runs.</span>
          </div>
          <p
            class="text-xs text-fg-muted"
            role="status"
          >
            {{ message || 'Saved in the project file. Saving starts nothing.' }}
          </p>
        </form>
      </aside>
    </div>
    <UiConfirmDialog
      :model-value="Boolean(pending)"
      title="Discard unsaved changes?"
      description="Your draft has not been saved to the project file."
      confirm-label="Discard changes"
      @update:model-value="!$event && cancelDiscard()"
      @confirm="discard"
      @cancel="cancelDiscard"
    />
  </section>
</template>

<style scoped>
.agents-layout { display: grid; gap: 16px; min-width: 0; }
.agents-layout--editing { grid-template-columns: minmax(0, 0.85fr) minmax(0, 1.15fr); align-items: start; }
.agent-columns { display: grid; grid-template-columns: minmax(0, 1fr) 80px; align-items: center; gap: 16px; }
.agent-row { position: relative; background: var(--color-bg-surface); }
.agent-row:hover { background: var(--color-bg-surface-alt); }
.agent-row--selected { background: var(--color-accent-primary-subtle); box-shadow: inset 4px 0 var(--color-accent-primary); }
.agent-editor { margin-top: -12px; min-height: calc(100dvh - 136px); padding: 16px; border: 1px solid var(--color-border-default); border-radius: 8px; background: var(--color-bg-surface); min-width: 0; overflow-wrap: anywhere; }
@media (max-width: 1100px) { .agents-layout--editing { grid-template-columns: minmax(0, 1fr); } .agent-editor { margin-top: 0; min-height: 0; } }
@media (max-width: 440px) { .agent-settings { grid-template-columns: minmax(0, 1fr); } }
</style>
