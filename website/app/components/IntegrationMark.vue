<script setup lang="ts">
import type { IntegrationLogo } from '~/composables/useIntegrationCatalog'

const props = withDefaults(defineProps<{ name: string; color: string; logo?: IntegrationLogo | null; size?: 'small' | 'large' }>(), {
  logo: null,
  size: 'small',
})

const initials = computed(() => {
  const words = props.name.replace(/\b(API|Bot|Admin)\b/gi, '').trim().split(/\s+/).filter(Boolean)
  if (words.length > 1) return `${words[0]![0]}${words.at(-1)![0]}`.toUpperCase()
  return words[0]!.slice(0, 2).toUpperCase()
})
</script>

<template>
  <span class="integration-mark" :class="[`is-${size}`, { 'has-logo': logo, 'is-wordmark': logo?.kind === 'wordmark' || logo?.kind === 'wordmark-dark', 'is-dark-logo': logo?.kind === 'wordmark-dark' }]" :style="{ '--mark-color': color }" aria-hidden="true">
    <template v-if="logo"><img :src="logo.src" alt=""></template>
    <template v-else><i /><b>{{ initials }}</b></template>
  </span>
</template>

<style scoped>
.integration-mark {
  position: relative;
  display: grid;
  width: 48px;
  height: 48px;
  flex: 0 0 auto;
  place-items: center;
  overflow: hidden;
  color: #f7f6f1;
  background: #151a25;
  border: 1px solid color-mix(in srgb, var(--mark-color) 48%, rgb(255 255 255 / 10%));
  border-radius: 14px;
  box-shadow: inset 0 1px rgb(255 255 255 / 7%);
}

.integration-mark::after {
  position: absolute;
  right: -15px;
  bottom: -17px;
  width: 48px;
  height: 48px;
  content: '';
  background: var(--mark-color);
  border-radius: 50%;
  filter: blur(18px);
  opacity: .38;
}

.integration-mark i {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 6px;
  height: 6px;
  background: var(--mark-color);
  border-radius: 50%;
  box-shadow: 0 0 10px color-mix(in srgb, var(--mark-color) 72%, transparent);
}

.integration-mark b {
  position: relative;
  z-index: 1;
  font-family: var(--font-mono);
  font-size: 13px;
  letter-spacing: -.04em;
}

.integration-mark.has-logo {
  background: #f8f7f2;
  border-color: rgb(255 255 255 / 18%);
  box-shadow: inset 0 0 0 1px rgb(7 10 15 / 5%), 0 8px 22px rgb(0 0 0 / 14%);
}

.integration-mark.has-logo::after { display: none; }
.integration-mark img { position: relative; z-index: 1; display: block; width: 74%; height: 74%; object-fit: contain; }
.integration-mark.is-wordmark { width: 112px; }
.integration-mark.is-wordmark img { width: 84%; height: 64%; }
.integration-mark.is-dark-logo { background: #090b10; border-color: rgb(255 255 255 / 18%); }

.integration-mark.is-large {
  width: 76px;
  height: 76px;
  border-radius: 21px;
}

.integration-mark.is-large b { font-size: 20px; }
.integration-mark.is-large i { top: 12px; right: 12px; width: 8px; height: 8px; }
.integration-mark.is-large.is-wordmark { width: 152px; }
</style>
