<script setup lang="ts">
import type { IntegrationPlugin, IntegrationProvider } from '~/composables/useIntegrationCatalog'

const catalog = useIntegrationCatalog()
const heroProviderKeys = new Set([
  'communications.slack-bot',
  'shopify.shopify',
  'publishing.wordpress',
  'gtm.hubspot',
  'media-buying.google-ads',
  'media-buying.meta-ads',
  'gtm.salesforce',
  'utils.google-gemini-image',
])
const heroProviders = catalog.providers.filter((provider) => heroProviderKeys.has(provider.key))
const search = ref('')
const searchInput = ref<HTMLInputElement | null>(null)
const view = ref<'providers' | 'plugins'>('plugins')
const selectedPlugin = ref('all')
const sort = ref<'name' | 'actions'>('name')

const query = computed(() => search.value.trim().toLowerCase())

function providerMatches(provider: IntegrationProvider) {
  if (selectedPlugin.value !== 'all' && provider.pluginSlug !== selectedPlugin.value) return false
  if (!query.value) return true
  return [
    provider.name,
    provider.description,
    provider.pluginName,
    provider.capabilities.join(' '),
    provider.actions.map((action) => `${action.name} ${action.description} ${action.keywords}`).join(' '),
  ].join(' ').toLowerCase().includes(query.value)
}

function pluginMatches(plugin: IntegrationPlugin) {
  if (!query.value) return true
  const providerContent = catalog.providers
    .filter((provider) => provider.pluginSlug === plugin.slug)
    .map((provider) => [
      provider.name,
      provider.description,
      provider.capabilities.join(' '),
      provider.actions.map((action) => `${action.name} ${action.description} ${action.keywords}`).join(' '),
    ].join(' '))
    .join(' ')
  return [plugin.name, plugin.description, plugin.providerNames.join(' '), providerContent].join(' ').toLowerCase().includes(query.value)
}

const filteredProviders = computed(() => {
  const items = catalog.providers.filter(providerMatches)
  return [...items].sort((a, b) => sort.value === 'actions' ? b.actionCount - a.actionCount || a.name.localeCompare(b.name) : a.name.localeCompare(b.name))
})
const filteredPlugins = computed(() => [...catalog.plugins.filter(pluginMatches)].sort((a, b) => sort.value === 'actions' ? b.actionCount - a.actionCount || a.name.localeCompare(b.name) : a.name.localeCompare(b.name)))
const resultCount = computed(() => view.value === 'providers' ? filteredProviders.value.length : filteredPlugins.value.length)

function selectPlugin(slug: string) {
  selectedPlugin.value = slug
  view.value = 'providers'
}

function focusSearch(event: KeyboardEvent) {
  const target = event.target as HTMLElement | null
  if (event.key !== '/' || target?.matches('input, textarea, select, [contenteditable="true"]')) return
  event.preventDefault()
  searchInput.value?.focus()
}

onMounted(() => window.addEventListener('keydown', focusSearch))
onBeforeUnmount(() => window.removeEventListener('keydown', focusSearch))

useLibrarySeo({
  title: `${catalog.counts.providers} AI integrations for agents and workflows | StackOS`,
  description: `Search ${catalog.counts.providers} tools across ${catalog.counts.plugins} StackOS plugins, with ${catalog.counts.actions} supported actions for communication, publishing, SEO, commerce, advertising, research, and more.`,
})

useSchemaOrg([
  defineWebPage({ '@type': 'CollectionPage', name: 'StackOS integrations library', description: 'Search the tools and supported actions StackOS workflows can use.' }),
  defineBreadcrumb({ itemListElement: [{ name: 'Home', item: '/' }, { name: 'Library', item: '/library' }, { name: 'Integrations', item: '/library/integrations' }] }),
])

useHead({ script: [
  { key: 'integrations-list', type: 'application/ld+json', innerHTML: JSON.stringify({ '@context': 'https://schema.org', '@type': 'ItemList', name: 'StackOS integrations', numberOfItems: catalog.counts.providers, itemListElement: catalog.providers.map((provider, index) => ({ '@type': 'ListItem', position: index + 1, name: provider.name, url: `/library/integrations/${provider.slug}` })) }) },
  { key: 'integrations-faq', type: 'application/ld+json', innerHTML: JSON.stringify({ '@context': 'https://schema.org', '@type': 'FAQPage', mainEntity: [
    { '@type': 'Question', name: 'What tools can StackOS work with?', acceptedAnswer: { '@type': 'Answer', text: `StackOS currently documents ${catalog.counts.providers} providers across ${catalog.counts.plugins} plugins, including communication, publishing, SEO, commerce, advertising, research, media, and local tools.` } },
    { '@type': 'Question', name: 'Do I need to replace my existing tools?', acceptedAnswer: { '@type': 'Answer', text: 'No. Keep the tools your team already uses. You start the request in your AI. The AI chooses the next step, StackOS scopes and records the call, and the connected provider performs the action.' } },
    { '@type': 'Question', name: 'What is the difference between a plugin and a provider?', acceptedAnswer: { '@type': 'Answer', text: 'A plugin is a StackOS capability pack for a type of work. A provider is the specific external or local tool used by that plugin, such as Slack, Shopify, WordPress, or Trackbooth.' } },
  ] }) },
] })
</script>

<template>
  <LibraryFrame>
    <LibraryCollectionHero
      kicker="StackOS Library / Integrations"
      title="Bring AI into the"
      accent="stack you already use."
      description="Search every tool StackOS can bring into a workflow—from Slack and Shopify to SEO data, publishing, advertising, media generation, and Trackbooth."
    >
      <template #meta>
            <span><strong>{{ catalog.counts.providers }}</strong> providers</span>
            <span><strong>{{ catalog.counts.plugins }}</strong> plugins</span>
            <span><strong>{{ catalog.counts.actions }}</strong> actions</span>
      </template>
      <template #aside>
        <div class="integrations-hero__map" aria-label="Example connected tools">
          <span v-for="provider in heroProviders" :key="provider.slug" :style="{ '--provider-color': provider.color }">
            <IntegrationMark :name="provider.name" :color="provider.color" :logo="provider.logo" />
            <b>{{ provider.name }}</b>
          </span>
          <i>Your agent chooses the action. StackOS scopes and records the call.</i>
        </div>
      </template>
    </LibraryCollectionHero>

    <section class="integration-directory">
      <div class="shell">
        <div class="integration-search">
          <label for="integration-query">
            <span>Search integrations and actions</span>
            <input id="integration-query" ref="searchInput" v-model="search" type="search" placeholder="Try Slack, Shopify, publishing, reports…" autocomplete="off">
            <kbd>/</kbd>
          </label>
          <div class="integration-search__view" aria-label="Browse integrations by">
            <button :class="{ 'is-active': view === 'plugins' }" type="button" @click="view = 'plugins'">Plugins</button>
            <button :class="{ 'is-active': view === 'providers' }" type="button" @click="view = 'providers'">Providers</button>
          </div>
        </div>

        <div class="integration-filters">
          <div v-if="view === 'providers'" class="integration-filters__plugins" aria-label="Filter providers by plugin">
            <button :class="{ 'is-active': selectedPlugin === 'all' }" type="button" @click="selectPlugin('all')">All <span>{{ catalog.counts.providers }}</span></button>
            <button v-for="plugin in catalog.plugins" :key="plugin.slug" :class="{ 'is-active': selectedPlugin === plugin.slug }" type="button" @click="selectPlugin(plugin.slug)">
              {{ plugin.name }} <span>{{ plugin.providerCount }}</span>
            </button>
          </div>
          <p v-else>Start with the kind of work, then choose the provider inside it.</p>
          <IntegrationSortMenu v-model="sort" />
        </div>

        <div class="integration-results">
          <p><strong>{{ resultCount }}</strong> {{ view === 'providers' ? 'providers' : 'plugins' }} found<span v-if="query"> for “{{ search.trim() }}”</span></p>
          <button v-if="query || selectedPlugin !== 'all'" type="button" @click="search = ''; selectedPlugin = 'all'">Clear filters</button>
        </div>

        <div v-if="view === 'providers' && filteredProviders.length" class="integration-grid">
          <IntegrationCard v-for="provider in filteredProviders" :key="provider.key" :provider="provider" />
        </div>
        <div v-else-if="view === 'plugins' && filteredPlugins.length" class="integration-grid integration-grid--plugins">
          <IntegrationPluginCard v-for="plugin in filteredPlugins" :key="plugin.slug" :plugin="plugin" />
        </div>
        <div v-else class="integration-empty">
          <strong>No matching integration.</strong>
          <p>Try a tool name, a type of work, or an action such as “send message”, “report”, or “publish”.</p>
        </div>
      </div>
    </section>

    <section class="integration-explainer">
      <div class="shell">
        <div class="library-section__heading">
          <div><p class="eyebrow">How the catalog fits together</p><h2>Plugins organize the work. Providers connect the tools.</h2></div>
          <p>A workflow can use several plugins and providers. The agent decides what should happen next. StackOS keeps the plan, authority, status, and receipts together while each provider performs its bounded part.</p>
        </div>
        <div class="integration-explainer__steps">
          <article><span>01</span><h3>Start in your AI</h3><p>Describe the job in Codex, Claude Code, Gemini CLI, or another connected client.</p></article>
          <article><span>02</span><h3>The agent chooses the next step</h3><p>It reads the workflow state, selects the action, and supplies the relevant context.</p></article>
          <article><span>03</span><h3>Run the bounded action</h3><p>StackOS scopes the call and resolves the configured credential. The provider performs the action, and StackOS records the result.</p></article>
        </div>
      </div>
    </section>
  </LibraryFrame>
</template>

<style scoped>
.catalog-hero__meta strong { color: var(--paper); font-size: 16px; }
.integrations-hero__map { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px; padding: 16px; background: rgb(255 255 255 / 3%); border: 1px solid rgb(255 255 255 / 9%); border-radius: 22px; }
.integrations-hero__map > span { display: flex; min-width: 0; align-items: center; gap: 10px; padding: 10px; background: #11151e; border: 1px solid rgb(255 255 255 / 7%); border-radius: 13px; }
.integrations-hero__map b { overflow: hidden; font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.integrations-hero__map i { grid-column: 1 / -1; padding: 10px 7px 2px; color: var(--ink-muted); font-size: 12px; font-style: normal; text-align: center; }
.integration-directory { min-height: 720px; padding: 68px 0 105px; color: var(--paper); background: #0d1017; }
.integration-search { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 15px; padding: 14px; background: #151a24; border: 1px solid rgb(255 255 255 / 9%); border-radius: 17px; }
.integration-search > label { position: relative; display: block; }
.integration-search > label > span { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0,0,0,0); }
.integration-search input { width: 100%; min-height: 54px; padding: 0 52px 0 17px; color: var(--paper); font: inherit; font-size: 16px; background: #0b0e15; border: 1px solid rgb(255 255 255 / 10%); border-radius: 11px; outline: none; }
.integration-search input:focus { border-color: var(--cobalt-soft); box-shadow: 0 0 0 3px rgb(120 146 255 / 15%); }
.integration-search input::placeholder { color: #7f8797; }
.integration-search kbd { position: absolute; top: 14px; right: 14px; display: grid; width: 26px; height: 26px; place-items: center; color: #8891a1; font-family: var(--font-mono); font-size: 11px; background: #171c27; border: 1px solid rgb(255 255 255 / 9%); border-radius: 6px; }
.integration-search__view { display: flex; gap: 5px; padding: 5px; background: #0b0e15; border: 1px solid rgb(255 255 255 / 9%); border-radius: 11px; }
.integration-search__view button { min-width: 104px; padding: 0 15px; color: var(--ink-soft); font-size: 13px; font-weight: 700; background: transparent; border: 0; border-radius: 8px; cursor: pointer; }
.integration-search__view button.is-active { color: var(--ink); background: var(--signal); }
.integration-filters { display: flex; align-items: start; justify-content: space-between; gap: 24px; margin-top: 22px; }
.integration-filters__plugins { display: flex; flex-wrap: wrap; gap: 7px; }
.integration-filters button { padding: 8px 10px; color: var(--ink-soft); font-size: 12px; font-weight: 650; background: transparent; border: 1px solid rgb(255 255 255 / 10%); border-radius: 999px; cursor: pointer; }
.integration-filters button span { margin-left: 4px; opacity: .62; }
.integration-filters button.is-active { color: var(--paper); background: rgb(255 255 255 / 8%); border-color: rgb(255 255 255 / 18%); }
.integration-filters > p { margin: 10px 0 0; color: var(--ink-muted); font-size: 13px; }
.integration-results { display: flex; align-items: center; justify-content: space-between; gap: 18px; margin: 40px 0 17px; }
.integration-results p { margin: 0; color: var(--ink-muted); font-size: 13px; }
.integration-results strong { color: var(--paper); }
.integration-results button { color: var(--cobalt-soft); font-size: 12px; background: none; border: 0; cursor: pointer; }
.integration-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 15px; }
.integration-grid--plugins { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.integration-empty { padding: 80px 24px; text-align: center; background: #11151e; border: 1px solid rgb(255 255 255 / 8%); border-radius: 18px; }
.integration-empty strong { font-size: 24px; }
.integration-empty p { color: var(--ink-soft); }
.integration-explainer { padding: 100px 0; background: var(--paper); }
.integration-explainer__steps { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); border: 1px solid var(--paper-border); border-radius: 17px; overflow: hidden; }
.integration-explainer__steps article { min-height: 235px; padding: 25px; background: #eceae2; }
.integration-explainer__steps article + article { border-left: 1px solid var(--paper-border); }
.integration-explainer__steps span { color: var(--cobalt); font-family: var(--font-mono); font-size: 12px; }
.integration-explainer__steps h3 { margin: 58px 0 10px; font-size: 23px; letter-spacing: -.04em; }
.integration-explainer__steps p { margin: 0; color: var(--muted-on-paper); font-size: 15px; line-height: 1.65; }
@media (max-width: 1020px) { .integration-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 700px) { .integrations-hero__map { grid-template-columns: 1fr 1fr; padding: 10px; } .integrations-hero__map > span { padding: 7px; } .integrations-hero__map :deep(.integration-mark) { width: 38px; height: 38px; } .integration-directory { padding-top: 38px; } .integration-search { grid-template-columns: 1fr; } .integration-search__view button { min-height: 43px; flex: 1; } .integration-filters { align-items: center; } .integration-filters > p { max-width: 190px; margin-top: 0; } .integration-grid { grid-template-columns: 1fr; } .integration-explainer { padding: 72px 0; } .integration-explainer__steps { grid-template-columns: 1fr; } .integration-explainer__steps article { min-height: 190px; } .integration-explainer__steps article + article { border-top: 1px solid var(--paper-border); border-left: 0; } .integration-explainer__steps h3 { margin-top: 38px; } }
</style>
