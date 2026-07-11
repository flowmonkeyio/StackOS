<script setup lang="ts">
import type { IntegrationPlugin } from '~/composables/useIntegrationCatalog'

defineProps<{ plugin: IntegrationPlugin }>()
</script>

<template>
  <NuxtLink class="integration-plugin" :to="`/library/integrations/plugins/${plugin.slug}`" :style="{ '--plugin-color': plugin.color }">
    <div>
      <span>StackOS plugin</span>
      <b>{{ plugin.providerCount }} providers</b>
    </div>
    <h2>{{ plugin.name }}</h2>
    <p>{{ plugin.description }}</p>
    <div class="integration-plugin__providers">
      <div>
        <span v-for="name in plugin.providerNames.slice(0, 4)" :key="name">{{ name }}</span>
      </div>
      <span v-if="plugin.providerCount > 4" class="integration-plugin__more">+{{ plugin.providerCount - 4 }}</span>
    </div>
    <footer><strong>{{ plugin.actionCount }} actions</strong><b aria-hidden="true">→</b></footer>
  </NuxtLink>
</template>

<style scoped>
.integration-plugin {
  display: flex;
  min-height: 310px;
  flex-direction: column;
  padding: 24px;
  color: var(--paper);
  text-decoration: none;
  background:
    radial-gradient(circle at 92% 8%, color-mix(in srgb, var(--plugin-color) 17%, transparent), transparent 34%),
    #11151e;
  border: 1px solid rgb(255 255 255 / 9%);
  border-radius: 18px;
  transition: transform 200ms ease, border-color 200ms ease;
}

.integration-plugin:hover { border-color: color-mix(in srgb, var(--plugin-color) 50%, transparent); transform: translateY(-4px); }
.integration-plugin > div:first-child { display: flex; justify-content: space-between; gap: 16px; color: color-mix(in srgb, var(--plugin-color) 68%, white); font-family: var(--font-mono); font-size: 11px; letter-spacing: .06em; text-transform: uppercase; }
.integration-plugin h2 { margin: 38px 0 12px; font-size: 30px; line-height: 1; letter-spacing: -.05em; }
.integration-plugin p { margin: 0; color: var(--ink-soft); font-size: 15px; line-height: 1.65; }
.integration-plugin__providers { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 6px; margin-top: 18px; overflow: hidden; }
.integration-plugin__providers > div { display: flex; min-width: 0; gap: 6px; overflow: hidden; }
.integration-plugin__providers span { flex: 0 0 auto; max-width: 132px; padding: 5px 7px; overflow: hidden; color: #b8bfcc; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; background: rgb(255 255 255 / 5%); border-radius: 6px; }
.integration-plugin__providers .integration-plugin__more { position: relative; z-index: 1; color: color-mix(in srgb, var(--plugin-color) 72%, white); background: #1a1f2a; }
.integration-plugin footer { display: flex; align-items: end; justify-content: space-between; margin-top: auto; padding-top: 22px; color: color-mix(in srgb, var(--plugin-color) 70%, white); font-size: 13px; border-top: 1px solid rgb(255 255 255 / 7%); }
.integration-plugin footer > b { font-size: 20px; }
</style>
