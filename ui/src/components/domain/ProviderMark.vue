<script setup lang="ts">
import { computed } from 'vue'

import { providerLogo } from '@/lib/stackos/providerPresentation'

const props = withDefaults(
  defineProps<{
    name: string
    providerKey: string
    pluginSlug?: string | null
    size?: 'xs' | 'sm' | 'md'
  }>(),
  {
    pluginSlug: null,
    size: 'md',
  },
)

const logo = computed(() => providerLogo(props.providerKey, props.pluginSlug))
const initials = computed(() => {
  const words = props.name
    .replace(/\b(API|Bot|Admin)\b/gi, '')
    .trim()
    .split(/\s+/)
    .filter(Boolean)
  if (words.length > 1) return `${words[0]![0]}${words.at(-1)![0]}`.toUpperCase()
  return (words[0] ?? props.providerKey).slice(0, 2).toUpperCase()
})
</script>

<template>
  <span
    class="provider-mark"
    :class="[
      `provider-mark--${size}`,
      {
        'provider-mark--logo': logo,
        'provider-mark--wordmark': logo?.kind.includes('wordmark'),
        'provider-mark--dark': logo?.kind === 'wordmark-dark',
      },
    ]"
    aria-hidden="true"
  >
    <img v-if="logo" :src="logo.src" alt="" />
    <template v-else>
      <i />
      <b>{{ initials }}</b>
    </template>
  </span>
</template>

<style scoped>
.provider-mark {
  position: relative;
  display: grid;
  width: 44px;
  height: 44px;
  flex: 0 0 auto;
  place-items: center;
  overflow: hidden;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-md);
  background: var(--color-accent-subtle);
  color: var(--color-accent-fg);
  box-shadow: var(--shadow-xs);
}

.provider-mark--sm {
  width: 36px;
  height: 36px;
}

.provider-mark--xs {
  width: 24px;
  height: 24px;
  border-radius: var(--radius-sm);
  box-shadow: none;
}

.provider-mark--xs b {
  font-size: var(--fs-2xs);
}

.provider-mark--xs i {
  top: 4px;
  right: 4px;
  width: 4px;
  height: 4px;
}

.provider-mark i {
  position: absolute;
  top: 7px;
  right: 7px;
  width: 5px;
  height: 5px;
  border-radius: var(--radius-full);
  background: var(--color-accent-primary);
}

.provider-mark b {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  letter-spacing: -0.03em;
}

.provider-mark--logo {
  background: var(--color-bg-surface-alt);
}

.provider-mark--dark {
  background: var(--color-bg-sunken);
}

.provider-mark img {
  display: block;
  width: 72%;
  height: 72%;
  object-fit: contain;
}

.provider-mark--wordmark img {
  width: 82%;
  height: 62%;
}
</style>
