<script setup lang="ts">
const route = useRoute()
const slug = String(route.params.slug)
const catalog = useIntegrationCatalog()
const provider = catalog.providerBySlug(slug)

if (!provider) throw createError({ statusCode: 404, statusMessage: 'Integration not found' })

const actionSearch = ref('')
const selectedCapability = ref('all')
const visibleLimit = ref(36)
const actionQuery = computed(() => actionSearch.value.trim().toLowerCase())
const filteredActions = computed(() => provider.actions.filter((action) => {
  if (selectedCapability.value !== 'all' && action.capability !== selectedCapability.value) return false
  if (!actionQuery.value) return true
  return `${action.name} ${action.description} ${action.keywords}`.toLowerCase().includes(actionQuery.value)
}))
const visibleActions = computed(() => filteredActions.value.slice(0, visibleLimit.value))
const capabilityOptions = computed(() => {
  const options = new Map<string, { key: string; name: string; count: number }>()
  for (const action of provider.actions) {
    const existing = options.get(action.capability)
    if (existing) existing.count += 1
    else options.set(action.capability, { key: action.capability, name: action.capabilityName, count: 1 })
  }
  return [...options.values()].sort((a, b) => a.name.localeCompare(b.name))
})
const related = computed(() => catalog.providers.filter((item) => item.pluginSlug === provider.pluginSlug && item.slug !== provider.slug).slice(0, 3))
const providerCapabilityAnswer = provider.capabilities.length
  ? `${provider.actionCount} ${provider.name} actions across ${provider.capabilities.join(', ')}`
  : `the ${provider.name} provider through the ${provider.pluginName} plugin`

function riskLabel(risk: string) {
  if (risk === 'read') return 'Reads data'
  if (risk === 'write') return 'Can make changes'
  if (risk === 'cost') return 'Uses a paid service'
  return 'Available action'
}

watch([actionSearch, selectedCapability], () => { visibleLimit.value = 36 })

useLibrarySeo({
  title: `${provider.name} integration for AI agents and workflows | StackOS`,
  description: `${provider.description} Explore ${provider.actionCount} supported ${provider.name} actions and see how it fits into StackOS workflows.`,
})

useSchemaOrg([
  defineWebPage({ name: `${provider.name} integration`, description: provider.description }),
  defineBreadcrumb({ itemListElement: [{ name: 'Home', item: '/' }, { name: 'Library', item: '/library' }, { name: 'Integrations', item: '/library/integrations' }, { name: provider.name, item: route.path }] }),
])

const softwareSchema = {
  '@context': 'https://schema.org',
  '@type': 'SoftwareApplication',
  name: provider.name,
  description: provider.description,
  applicationCategory: provider.pluginName,
  ...(provider.primaryUrl ? { url: provider.primaryUrl } : {}),
}

useHead({ script: [
  { key: 'integration-software', type: 'application/ld+json', innerHTML: JSON.stringify(softwareSchema) },
  { key: 'integration-faq', type: 'application/ld+json', innerHTML: JSON.stringify({ '@context': 'https://schema.org', '@type': 'FAQPage', mainEntity: [
    { '@type': 'Question', name: `What can the ${provider.name} integration do in StackOS?`, acceptedAnswer: { '@type': 'Answer', text: `The current catalog includes ${providerCapabilityAnswer}. The exact actions used depend on the workflow and approval rules.` } },
    { '@type': 'Question', name: `Does StackOS replace ${provider.name}?`, acceptedAnswer: { '@type': 'Answer', text: `No. ${provider.name} remains the tool that performs its part of the job. StackOS organizes the workflow, keeps status and dependencies visible, and calls the tool only when the plan reaches the right approved action.` } },
  ] }) },
] })
</script>

<template>
  <LibraryFrame :breadcrumb-current-label="provider.name">
    <section class="integration-detail-hero" :style="{ '--integration-color': provider.color }">
      <div class="shell integration-detail-hero__grid">
        <div>
          <div class="integration-detail-hero__identity">
            <IntegrationMark :name="provider.name" :color="provider.color" :logo="provider.logo" size="large" />
            <div><span>{{ provider.pluginName }} plugin</span><h1>{{ provider.name }}</h1></div>
          </div>
          <p>{{ provider.description }}</p>
          <div class="integration-detail-hero__meta">
            <span><strong>{{ provider.actionCount }}</strong> supported actions</span>
            <span><strong>{{ provider.capabilities.length }}</strong> capability groups</span>
          </div>
        </div>
        <aside>
          <p>Use {{ provider.name }} inside a complete plan.</p>
          <strong>StackOS keeps the request, status, approvals, and proof connected around every tool action.</strong>
          <div>
            <a v-for="link in provider.links.slice(0, 4)" :key="link.url" :href="link.url" target="_blank" rel="noopener noreferrer">{{ link.label }} <span aria-hidden="true">↗</span></a>
          </div>
        </aside>
      </div>
    </section>

    <section class="integration-actions">
      <div class="shell">
        <div class="library-section__heading">
          <div><p class="eyebrow">Supported work</p><h2>What StackOS can do with {{ provider.name }}.</h2></div>
          <p>The catalog reflects the actions currently available through the {{ provider.pluginName }} plugin. A workflow still decides which actions belong in the job and where approval is required.</p>
        </div>

        <div v-if="provider.actions.length" class="integration-action-tools">
          <label for="action-query"><span>Search {{ provider.name }} actions</span><input id="action-query" v-model="actionSearch" type="search" :placeholder="`Search ${provider.actionCount} actions…`" autocomplete="off"></label>
          <div v-if="capabilityOptions.length > 1" class="integration-action-tools__filters">
            <button :class="{ 'is-active': selectedCapability === 'all' }" type="button" @click="selectedCapability = 'all'">All <span>{{ provider.actionCount }}</span></button>
            <button v-for="capability in capabilityOptions" :key="capability.key" :class="{ 'is-active': selectedCapability === capability.key }" type="button" @click="selectedCapability = capability.key">{{ capability.name }} <span>{{ capability.count }}</span></button>
          </div>
          <p>{{ filteredActions.length }} matching actions</p>
        </div>

        <div v-if="visibleActions.length" class="integration-action-grid">
          <article v-for="action in visibleActions" :key="action.key">
            <div><span>{{ action.capabilityName }}</span><b :class="`is-${action.risk}`">{{ riskLabel(action.risk) }}</b></div>
            <h3>{{ action.name }}</h3>
            <p>{{ action.description }}</p>
          </article>
        </div>
        <div v-else-if="provider.actions.length" class="integration-action-empty">No actions match that search.</div>
        <div v-else class="integration-action-empty">This provider is available to the plugin, but it does not publish callable actions yet.</div>

        <button v-if="visibleActions.length < filteredActions.length" class="integration-load-more" type="button" @click="visibleLimit += 36">
          Show 36 more <span>({{ filteredActions.length - visibleActions.length }} remaining)</span>
        </button>
      </div>
    </section>

    <section class="integration-answer">
      <div class="detail-body__narrow">
        <p class="eyebrow">Direct answer</p>
        <h2>How does the {{ provider.name }} integration work?</h2>
        <p>People continue using {{ provider.name }} as their tool. StackOS adds it to a visible workflow, keeps the work in order, and passes only the approved action to the integration when that stage is ready.</p>
        <IntegrationRouteFlow :provider="provider.name" :provider-slug="provider.slug" :color="provider.color" />
      </div>
    </section>

    <section v-if="related.length" class="related-section">
      <div class="shell">
        <div class="library-section__heading"><div><p class="eyebrow eyebrow--dark">More from {{ provider.pluginName }}</p><h2>Related integrations.</h2></div><NuxtLink :to="`/library/integrations/plugins/${provider.pluginSlug}`">See the plugin →</NuxtLink></div>
        <div class="integration-related-grid"><IntegrationCard v-for="item in related" :key="item.key" :provider="item" /></div>
      </div>
    </section>
  </LibraryFrame>
</template>

<style scoped>
.integration-detail-hero { padding: 74px 0 78px; color: var(--paper); background: radial-gradient(circle at 80% 20%, color-mix(in srgb, var(--integration-color) 13%, transparent), transparent 32%), var(--ink); }
.integration-detail-hero__grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(330px, .48fr); gap: 75px; align-items: end; }
.integration-detail-hero__identity { display: flex; align-items: center; gap: 22px; }
.integration-detail-hero__identity span { color: color-mix(in srgb, var(--integration-color) 72%, white); font-family: var(--font-mono); font-size: 12px; letter-spacing: .08em; text-transform: uppercase; }
.integration-detail-hero h1 { margin: 4px 0 0; font-size: clamp(48px, 6.5vw, 88px); line-height: .95; letter-spacing: -.07em; }
.integration-detail-hero__grid > div > p { max-width: 780px; margin: 27px 0 0; color: var(--ink-soft); font-size: clamp(18px, 1.6vw, 21px); line-height: 1.65; }
.integration-detail-hero__meta { display: flex; flex-wrap: wrap; gap: 9px; margin-top: 27px; }
.integration-detail-hero__meta span { padding: 8px 10px; color: var(--ink-soft); font-size: 12px; background: rgb(255 255 255 / 4%); border: 1px solid rgb(255 255 255 / 8%); border-radius: 8px; }
.integration-detail-hero__meta strong { color: var(--paper); font-size: 15px; }
.integration-detail-hero aside { padding: 24px; background: #11151e; border: 1px solid rgb(255 255 255 / 10%); border-radius: 17px; }
.integration-detail-hero aside p { margin: 0 0 13px; color: color-mix(in srgb, var(--integration-color) 72%, white); font-size: 13px; font-weight: 750; }
.integration-detail-hero aside > strong { display: block; color: #d8dce5; font-size: 17px; line-height: 1.55; }
.integration-detail-hero aside > div { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 22px; }
.integration-detail-hero aside a { padding: 8px 10px; color: var(--paper); font-size: 12px; font-weight: 700; text-decoration: none; background: rgb(255 255 255 / 5%); border: 1px solid rgb(255 255 255 / 9%); border-radius: 8px; }
.integration-actions { padding: 95px 0 110px; background: var(--paper); }
.integration-action-tools { display: grid; grid-template-columns: minmax(280px, .65fr) minmax(0, 1fr) auto; gap: 15px; align-items: center; margin: 50px 0 22px; padding: 13px; background: #e8e6dc; border: 1px solid var(--paper-border); border-radius: 15px; }
.integration-action-tools label > span { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0,0,0,0); }
.integration-action-tools input { width: 100%; min-height: 46px; padding: 0 14px; color: var(--ink); font: inherit; font-size: 15px; background: #f8f7f2; border: 1px solid var(--paper-border); border-radius: 9px; }
.integration-action-tools__filters { display: flex; gap: 6px; overflow-x: auto; scrollbar-width: none; }
.integration-action-tools__filters button { flex: 0 0 auto; padding: 8px 9px; color: #565962; font-size: 11px; background: transparent; border: 1px solid #d1cec2; border-radius: 999px; cursor: pointer; }
.integration-action-tools__filters button.is-active { color: #fff; background: var(--cobalt); border-color: var(--cobalt); }
.integration-action-tools__filters span { opacity: .65; }
.integration-action-tools > p { margin: 0; color: #6c6b65; font-size: 12px; white-space: nowrap; }
.integration-action-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 9px; }
.integration-action-grid article { min-height: 154px; padding: 17px; background: #eceae2; border: 1px solid var(--paper-border); border-radius: 12px; }
.integration-action-grid article > div { display: flex; align-items: center; justify-content: space-between; gap: 15px; }
.integration-action-grid article > div > span { color: var(--cobalt); font-family: var(--font-mono); font-size: 11px; letter-spacing: .06em; text-transform: uppercase; }
.integration-action-grid article > div > b { color: #64645d; font-size: 11px; font-weight: 650; }
.integration-action-grid article > div > b.is-write { color: #a04434; }
.integration-action-grid article > div > b.is-cost { color: #8b5e00; }
.integration-action-grid h3 { margin: 21px 0 7px; font-size: 18px; line-height: 1.18; letter-spacing: -.035em; }
.integration-action-grid p { display: -webkit-box; margin: 0; overflow: hidden; color: #555650; font-size: 13.5px; line-height: 1.5; -webkit-box-orient: vertical; -webkit-line-clamp: 3; }
.integration-action-empty { padding: 60px 20px; color: #5f605a; text-align: center; background: #eceae2; border-radius: 13px; }
.integration-load-more { display: block; min-height: 48px; margin: 25px auto 0; padding: 0 18px; color: #fff; font-size: 13px; font-weight: 750; background: var(--cobalt); border: 0; border-radius: 9px; cursor: pointer; }
.integration-load-more span { opacity: .72; }
.integration-answer { padding: 92px 0; background: #e8e6dc; }
.integration-answer h2 { margin: 9px 0 20px; font-size: clamp(36px, 4.5vw, 60px); line-height: 1; letter-spacing: -.06em; }
.integration-answer p { color: #4f504b; font-size: 18px; line-height: 1.72; }
.integration-related-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 15px; }
@media (max-width: 1050px) { .integration-action-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 900px) { .integration-detail-hero__grid { grid-template-columns: 1fr; gap: 36px; } .integration-action-tools { grid-template-columns: 1fr; } .integration-action-tools > p { white-space: normal; } .integration-related-grid { grid-template-columns: 1fr 1fr; } }
@media (max-width: 640px) { .integration-detail-hero { padding-top: 60px; } .integration-detail-hero__identity { align-items: start; } .integration-detail-hero h1 { font-size: clamp(43px, 12vw, 62px); } .integration-action-grid { grid-template-columns: 1fr; } .integration-action-grid article { min-height: 154px; } .integration-related-grid { grid-template-columns: 1fr; } }
</style>
