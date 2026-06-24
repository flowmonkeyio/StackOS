<script setup lang="ts">
// KvList — semantic <dl>/<dt>/<dd> key/value display.
//
// Used by read-only summaries (e.g. run detail) to render key/value facts.
// Items are rendered in declaration order; the consumer may format values
// up-front (the component just renders strings/slots).

interface KvItem {
  key: string
  label: string
  value: unknown
}

interface Props {
  items: KvItem[]
  /** When true, lays the rows out two columns wide on md+. */
  twoColumn?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  twoColumn: false,
})

function display(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'string') return value
  if (typeof value === 'number') return value.toLocaleString()
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (value instanceof Date) return value.toISOString()
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}
</script>

<template>
  <dl
    class="grid gap-x-6 gap-y-3 text-sm"
    :class="props.twoColumn ? 'sm:grid-cols-[max-content_1fr] md:grid-cols-[max-content_1fr_max-content_1fr]' : 'sm:grid-cols-[max-content_1fr]'"
  >
    <template
      v-for="item in items"
      :key="item.key"
    >
      <dt class="text-xs font-medium text-fg-muted">
        {{ item.label }}
      </dt>
      <dd class="min-w-0 break-words text-sm text-fg-default">
        <slot
          :name="`item:${item.key}`"
          :value="item.value"
          :item="item"
        >
          {{ display(item.value) }}
        </slot>
      </dd>
    </template>
  </dl>
</template>
