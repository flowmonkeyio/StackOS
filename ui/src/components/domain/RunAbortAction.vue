<script setup lang="ts">
import { computed } from 'vue'
import { UiDropdownMenu, UiIcon, UiIconButton } from '@/components/ui'
import type { UiDropdownMenuProps } from '@/components/ui/UiDropdownMenu.vue'
import { useRunAbort, type RunAbortTarget } from '@/composables/useRunAbort'

const props = withDefaults(defineProps<{
  target: RunAbortTarget | null
  /** Footer consumers open inward; other consumers keep their existing placement. */
  placement?: UiDropdownMenuProps['placement']
}>(), { placement: 'bottom-end' })
const emit = defineEmits<{ refresh: [] }>()
const { pending, show } = useRunAbort()
const available = computed(() => props.target && !['aborted', 'failed', 'complete', 'completed', 'skipped'].includes(props.target.status))
function select() {
  if (!props.target || pending.value) return
  show(props.target, () => emit('refresh'))
}
</script>

<template>
  <UiDropdownMenu
    v-if="available"
    :items="[{ key: 'abort', label: pending ? 'Aborting…' : 'Abort run', tone: 'danger', disabled: pending, onSelect: select }]"
    :placement="placement"
    aria-label="Run actions"
  >
    <template #trigger="{ toggle }">
      <UiIconButton
        data-dropdown-trigger
        size="sm"
        :aria-label="`Run actions for ${target?.title}`"
        :disabled="pending"
        @click.stop="toggle"
      >
        <UiIcon
          name="more"
          class="h-4 w-4"
        />
      </UiIconButton>
    </template>
  </UiDropdownMenu>
</template>
