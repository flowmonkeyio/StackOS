<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

defineProps<{ email: string }>()
const text = ref<HTMLElement | null>(null)
const overflow = ref(false)
let observer: ResizeObserver | undefined
function measure() {
  if (text.value) overflow.value = text.value.scrollWidth > text.value.clientWidth
}
onMounted(() => {
  measure()
  if (typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(measure)
    if (text.value) observer.observe(text.value)
  }
})
onBeforeUnmount(() => observer?.disconnect())
</script>

<template>
  <span class="relative block min-w-0 overflow-hidden whitespace-nowrap" aria-hidden="true">
    <span ref="text" class="block overflow-hidden">{{ email }}</span>
    <span v-if="overflow" class="absolute right-0 top-0 bg-bg-surface-alt">...</span>
  </span>
</template>
