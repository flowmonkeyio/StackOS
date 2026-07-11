<script setup lang="ts">
const route = useRoute()
const slug = String(route.params.slug)
const catalog = useIntegrationCatalog()
const plugin = catalog.pluginBySlug(slug)

if (!plugin) throw createError({ statusCode: 404, statusMessage: 'Integration plugin not found' })

const providers = catalog.providersForPlugin(slug)

useLibrarySeo({
  title: `${plugin.name} integrations — ${plugin.providerCount} providers | StackOS`,
  description: `${plugin.description} Browse ${plugin.providerCount} providers and ${plugin.actionCount} supported actions in the StackOS ${plugin.name} plugin.`,
})

useSchemaOrg([
  defineWebPage({ '@type': 'CollectionPage', name: `${plugin.name} integrations`, description: plugin.description }),
  defineBreadcrumb({ itemListElement: [{ name: 'Home', item: '/' }, { name: 'Library', item: '/library' }, { name: 'Integrations', item: '/library/integrations' }, { name: plugin.name, item: route.path }] }),
])

useHead({ script: [{ key: 'plugin-integrations-list', type: 'application/ld+json', innerHTML: JSON.stringify({ '@context': 'https://schema.org', '@type': 'ItemList', name: `${plugin.name} integrations`, numberOfItems: providers.length, itemListElement: providers.map((provider, index) => ({ '@type': 'ListItem', position: index + 1, name: provider.name, url: `/library/integrations/${provider.slug}` })) }) }] })
</script>

<template>
  <LibraryFrame>
    <section class="plugin-detail-hero" :style="{ '--plugin-color': plugin.color }">
      <div class="shell">
        <div class="plugin-detail-hero__breadcrumb"><NuxtLink to="/library/integrations">← All integrations</NuxtLink></div>
        <p class="library-kicker">StackOS plugin</p>
        <h1>{{ plugin.name }}</h1>
        <p>{{ plugin.description }}</p>
        <div class="plugin-detail-hero__stats"><span><strong>{{ plugin.providerCount }}</strong> providers</span><span><strong>{{ plugin.actionCount }}</strong> supported actions</span><span><strong>{{ plugin.capabilityCount }}</strong> capabilities</span></div>
      </div>
    </section>

    <section class="plugin-provider-list">
      <div class="shell">
        <div class="library-section__heading">
          <div><p class="eyebrow">Available tools</p><h2>Providers in {{ plugin.name }}.</h2></div>
          <p>Each provider is one tool the plugin can use. Open a provider to see its exact supported actions and official product links.</p>
        </div>
        <div class="plugin-provider-grid"><IntegrationCard v-for="provider in providers" :key="provider.key" :provider="provider" /></div>
      </div>
    </section>
  </LibraryFrame>
</template>

<style scoped>
.plugin-detail-hero { padding: 76px 0 82px; color: var(--paper); background: radial-gradient(circle at 82% 18%, color-mix(in srgb, var(--plugin-color) 18%, transparent), transparent 34%), var(--ink); }
.plugin-detail-hero__breadcrumb { display: block; margin-bottom: 52px; }
.plugin-detail-hero__breadcrumb a { color: var(--ink-soft); font-size: 13px; text-decoration: none; }
.plugin-detail-hero .library-kicker { display: flex; margin: 0 0 20px; color: color-mix(in srgb, var(--plugin-color) 72%, white); }
.plugin-detail-hero h1 { max-width: 900px; margin: 0; font-size: clamp(58px, 8vw, 108px); line-height: .92; letter-spacing: -.075em; }
.plugin-detail-hero > .shell > p:not(.library-kicker) { max-width: 790px; margin: 29px 0 0; color: var(--ink-soft); font-size: clamp(18px, 1.6vw, 21px); line-height: 1.65; }
.plugin-detail-hero__stats { display: flex; flex-wrap: wrap; gap: 9px; margin-top: 29px; }
.plugin-detail-hero__stats span { padding: 8px 10px; color: var(--ink-soft); font-size: 12px; background: rgb(255 255 255 / 4%); border: 1px solid rgb(255 255 255 / 9%); border-radius: 8px; }
.plugin-detail-hero__stats strong { color: var(--paper); font-size: 16px; }
.plugin-provider-list { padding: 95px 0 110px; color: var(--paper); background: #0d1017; }
.plugin-provider-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 15px; }
@media (max-width: 960px) { .plugin-provider-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 620px) { .plugin-detail-hero h1 { font-size: clamp(49px, 15vw, 76px); } .plugin-provider-grid { grid-template-columns: 1fr; } }
</style>
