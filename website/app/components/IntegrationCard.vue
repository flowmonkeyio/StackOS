<script setup lang="ts">
import type { IntegrationProvider } from '~/composables/useIntegrationCatalog'

defineProps<{ provider: IntegrationProvider }>()
</script>

<template>
  <article class="integration-card" :style="{ '--integration-color': provider.color }">
    <div class="integration-card__top">
      <IntegrationMark :name="provider.name" :color="provider.color" :logo="provider.logo" />
      <span>{{ provider.pluginName }}</span>
    </div>

    <NuxtLink class="integration-card__body" :to="`/library/integrations/${provider.slug}`">
      <h2>{{ provider.name }}</h2>
      <p>{{ provider.description }}</p>
    </NuxtLink>

    <div class="integration-card__capabilities">
      <span v-for="capability in provider.capabilities.slice(0, 2)" :key="capability">{{ capability }}</span>
      <span v-if="provider.capabilities.length > 2">+{{ provider.capabilities.length - 2 }}</span>
    </div>

    <footer>
      <NuxtLink :to="`/library/integrations/${provider.slug}`">
        {{ provider.actionCount ? `${provider.actionCount} actions` : 'Learn more' }} <span aria-hidden="true">→</span>
      </NuxtLink>
      <a v-if="provider.primaryUrl" :href="provider.primaryUrl" target="_blank" rel="noopener noreferrer">
        Visit tool <span aria-hidden="true">↗</span>
      </a>
    </footer>
  </article>
</template>

<style scoped>
.integration-card {
  display: flex;
  min-width: 0;
  min-height: 330px;
  flex-direction: column;
  padding: 22px;
  background: #11151e;
  border: 1px solid rgb(255 255 255 / 9%);
  border-radius: 18px;
  transition: transform 200ms ease, border-color 200ms ease, box-shadow 200ms ease;
}

.integration-card:hover {
  border-color: color-mix(in srgb, var(--integration-color) 48%, transparent);
  box-shadow: 0 22px 60px rgb(0 0 0 / 20%);
  transform: translateY(-4px);
}

.integration-card__top {
  display: flex;
  align-items: start;
  justify-content: space-between;
  gap: 18px;
}

.integration-card__top > span {
  padding: 6px 8px;
  color: color-mix(in srgb, var(--integration-color) 70%, white);
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: .06em;
  text-transform: uppercase;
  background: rgb(255 255 255 / 4%);
  border: 1px solid rgb(255 255 255 / 7%);
  border-radius: 999px;
}

.integration-card__body {
  color: inherit;
  text-decoration: none;
}

.integration-card h2 {
  margin: 25px 0 10px;
  color: var(--paper);
  font-size: 24px;
  line-height: 1.1;
  letter-spacing: -.04em;
}

.integration-card p {
  display: -webkit-box;
  margin: 0;
  overflow: hidden;
  color: var(--ink-soft);
  font-size: 15px;
  line-height: 1.6;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}

.integration-card__capabilities {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 17px;
}

.integration-card__capabilities span {
  padding: 5px 7px;
  color: #aeb6c6;
  font-size: 11px;
  background: rgb(255 255 255 / 4%);
  border-radius: 6px;
}

.integration-card footer {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 10px 16px;
  margin-top: auto;
  padding-top: 22px;
  border-top: 1px solid rgb(255 255 255 / 7%);
}

.integration-card footer a {
  color: color-mix(in srgb, var(--integration-color) 72%, white);
  font-size: 13px;
  font-weight: 700;
  text-decoration: none;
}

.integration-card footer a:last-child { color: var(--ink-soft); }
</style>
