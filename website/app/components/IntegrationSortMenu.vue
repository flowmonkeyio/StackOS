<script setup lang="ts">
const props = defineProps<{ modelValue: 'name' | 'actions' }>()
const emit = defineEmits<{ 'update:modelValue': [value: 'name' | 'actions'] }>()

const options = [
  { value: 'name' as const, label: 'A–Z' },
  { value: 'actions' as const, label: 'Most actions' },
]
const root = ref<HTMLElement | null>(null)
const open = ref(false)
const ready = ref(false)
const activeLabel = computed(() => options.find((option) => option.value === props.modelValue)!.label)

function choose(value: 'name' | 'actions') {
  emit('update:modelValue', value)
  open.value = false
}

function closeFromOutside(event: PointerEvent) {
  if (!root.value?.contains(event.target as Node)) open.value = false
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    open.value = false
    root.value?.querySelector<HTMLButtonElement>('.integration-sort__trigger')?.focus()
  }
}

onMounted(() => {
  ready.value = true
  document.addEventListener('pointerdown', closeFromOutside)
})
onBeforeUnmount(() => document.removeEventListener('pointerdown', closeFromOutside))
</script>

<template>
  <div ref="root" class="integration-sort" @keydown="handleKeydown">
    <span>Sort</span>
    <button
      class="integration-sort__trigger"
      type="button"
      :disabled="!ready"
      aria-haspopup="listbox"
      :aria-expanded="open"
      @click="open = !open"
    >
      {{ activeLabel }}
      <i aria-hidden="true" />
    </button>
    <div v-if="open" class="integration-sort__menu" role="listbox" aria-label="Sort integrations">
      <button
        v-for="option in options"
        :key="option.value"
        type="button"
        role="option"
        :aria-selected="modelValue === option.value"
        :class="{ 'is-selected': modelValue === option.value }"
        @click="choose(option.value)"
      >
        <span>{{ option.label }}</span>
        <b aria-hidden="true">✓</b>
      </button>
    </div>
  </div>
</template>

<style scoped>
.integration-sort { position: relative; z-index: 8; display: flex; flex: 0 0 auto; align-items: center; gap: 8px; color: var(--ink-muted); font-size: 12px; }
.integration-sort__trigger { display: flex; min-width: 132px; min-height: 40px; align-items: center; justify-content: space-between; gap: 16px; padding: 0 12px; color: var(--paper); font: inherit; font-size: 13px; font-weight: 700; background: #151a24; border: 1px solid rgb(255 255 255 / 12%); border-radius: 9px; cursor: pointer; }
.integration-sort__trigger:hover,
.integration-sort__trigger:focus-visible { border-color: rgb(255 255 255 / 26%); outline: none; }
.integration-sort__trigger:disabled { cursor: wait; opacity: 0.72; }
.integration-sort__trigger i { width: 7px; height: 7px; border-right: 1.5px solid currentColor; border-bottom: 1.5px solid currentColor; transform: translateY(-2px) rotate(45deg); transition: transform 160ms ease; }
.integration-sort__trigger[aria-expanded='true'] i { transform: translateY(2px) rotate(225deg); }
.integration-sort__menu { position: absolute; top: calc(100% + 7px); right: 0; width: 180px; padding: 6px; background: #171c27; border: 1px solid rgb(255 255 255 / 13%); border-radius: 11px; box-shadow: 0 18px 50px rgb(0 0 0 / 38%); }
.integration-sort__menu button { display: flex; width: 100%; min-height: 40px; align-items: center; justify-content: space-between; padding: 0 10px; color: var(--ink-soft); font: inherit; font-size: 13px; text-align: left; background: transparent; border: 0; border-radius: 7px; cursor: pointer; }
.integration-sort__menu button:hover,
.integration-sort__menu button:focus-visible { color: var(--paper); background: rgb(255 255 255 / 7%); outline: none; }
.integration-sort__menu button.is-selected { color: var(--ink); background: var(--signal); }
.integration-sort__menu b { visibility: hidden; }
.integration-sort__menu button.is-selected b { visibility: visible; }
</style>
