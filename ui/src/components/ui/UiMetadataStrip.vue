<!--
  UiMetadataStrip — compact inline metadata for drawers and dense detail headers.
  Use this when a long description list would waste vertical space.
-->
<script setup lang="ts">
export interface UiMetadataStripItem {
  key?: string | number
  label: string
  value?: string | number | boolean | null
  mono?: boolean
  title?: string
}

const props = withDefaults(
  defineProps<{
    items: UiMetadataStripItem[]
    ariaLabel?: string
  }>(),
  {
    ariaLabel: undefined,
  },
)

function displayValue(value: UiMetadataStripItem['value']): string | number {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  return value
}
</script>

<template>
  <dl
    class="ui-metadata-strip flex flex-wrap gap-x-4 gap-y-2 rounded-md border border-subtle bg-bg-surface-alt px-3 py-2"
    :aria-label="props.ariaLabel"
  >
    <div
      v-for="(item, index) in props.items"
      :key="item.key ?? `${item.label}:${index}`"
      class="inline-flex min-w-0 max-w-full items-baseline gap-1.5"
      :title="item.title"
    >
      <dt class="shrink-0 text-2xs font-semibold uppercase text-fg-subtle">
        {{ item.label }}
      </dt>
      <dd
        :class="[
          'min-w-0 truncate text-sm font-medium text-fg-default',
          item.mono && 'font-mono text-xs',
        ]"
      >
        {{ displayValue(item.value) }}
      </dd>
    </div>
  </dl>
</template>
