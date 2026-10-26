<script setup>
import { computed, useAttrs } from 'vue'
import { canonicalPath } from '~~/shared/utils/siteSeo'

defineOptions({ inheritAttrs: false })

const props = defineProps({
  href: { type: String, default: '' },
  target: { type: String, default: undefined, required: false },
})
const attrs = useAttrs()
const isLocalFile = computed(() => props.href.startsWith('/')
  && !props.href.startsWith('//')
  && !canonicalPath(props.href).endsWith('/'))
const fileRel = () => Object.hasOwn(attrs, 'rel')
  ? attrs.rel
  : props.target && props.target !== '_self' ? 'noopener noreferrer' : undefined
</script>

<template>
  <a v-if="isLocalFile" v-bind="$attrs" :href="props.href" :target="props.target" :rel="fileRel()">
    <slot />
  </a>
  <NuxtLink v-else v-bind="$attrs" :href="props.href" :target="props.target">
    <slot />
  </NuxtLink>
</template>
