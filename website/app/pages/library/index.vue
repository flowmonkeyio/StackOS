<script setup lang="ts">
const catalog = useLibraryCatalog()
const integrationCatalog = useIntegrationCatalog()
const workflows = catalog.workflows.filter((item) => item.featured).slice(0, 6)
const agents = catalog.agents.filter((item) => item.featured).slice(0, 3)
const orchestrators = catalog.orchestrators
const featuredIntegrationSlugs = new Set(['slack-bot', 'shopify', 'wordpress', 'trackbooth', 'openai-images', 'google-search-console'])
const featuredIntegrations = integrationCatalog.providers.filter((provider) => featuredIntegrationSlugs.has(provider.slug))
const { data: articles } = await useAsyncData('library-featured-articles', () =>
  queryCollection('articles').where('featured', '=', true).order('publishedAt', 'DESC').limit(4).all(),
)

useLibrarySeo({
  title: 'AI workflow library — Agentic workflows, agents, and orchestration | StackOS',
  description: 'Explore real AI workflow automation examples, specialist agents, orchestration patterns, and practical guides for putting AI to work across your existing tools.',
})

useSchemaOrg([
  defineWebPage({ '@type': 'CollectionPage', name: 'StackOS Library', description: 'Workflows, agents, orchestrators, integrations, and practical guides for AI-powered work.' }),
  defineBreadcrumb({ itemListElement: [{ name: 'Home', item: '/' }, { name: 'Library', item: '/library' }] }),
])

useHead({ script: [{ key: 'library-faq', type: 'application/ld+json', innerHTML: JSON.stringify({ '@context': 'https://schema.org', '@type': 'FAQPage', mainEntity: [{ '@type': 'Question', name: 'What is in the StackOS AI workflow library?', acceptedAnswer: { '@type': 'Answer', text: 'The StackOS Library documents complete agentic workflows, the focused AI agents inside them, the orchestrators that coordinate each job, and practical implementation guides.' } }] }) }] })

function articleSlug(stem: string) {
  return stem.split('/').at(-1) || stem
}
</script>

<template>
  <LibraryFrame>
    <section class="library-hero">
      <div class="shell library-hero__grid">
        <div>
          <p class="library-kicker"><span class="status-dot" /> The work behind the product</p>
          <h1>See how AI work <em>actually gets done.</em></h1>
          <p class="library-hero__lede">
            Explore complete workflows, the specialist agents inside them, and practical guides for connecting AI to the tools your team already uses.
          </p>
        </div>
        <div class="library-hero__visual" aria-label="Library contents">
          <NuxtLink class="library-stat" to="/library/workflows"><strong>{{ catalog.workflows.length }}</strong><span>Complete workflows</span></NuxtLink>
          <NuxtLink class="library-stat" to="/library/agents"><strong>{{ catalog.agents.length }}</strong><span>Specialist agents</span></NuxtLink>
          <NuxtLink class="library-stat" to="/library/orchestrators"><strong>{{ catalog.orchestrators.length }}</strong><span>Coordinators</span></NuxtLink>
          <NuxtLink class="library-stat" to="/library/articles"><strong>{{ articles?.length || 0 }}+</strong><span>Practical guides</span></NuxtLink>
        </div>
      </div>
    </section>

    <section class="library-map" aria-labelledby="library-map-title">
      <div class="shell library-map__grid">
        <div class="library-map__intro">
          <p class="eyebrow">The system in one minute</p>
          <h2 id="library-map-title">Three parts. <em>One complete job.</em></h2>
          <p>A request becomes a visible workflow. Focused agents handle each responsibility. An orchestrator keeps the plan, tools, dependencies, and approvals connected until the result is done.</p>
        </div>
        <div class="library-map__links">
          <NuxtLink to="/library/workflows"><span>01 / Workflow</span><strong>The path the work follows.</strong><small>Explore {{ catalog.workflows.length }} complete workflows →</small></NuxtLink>
          <NuxtLink to="/library/agents"><span>02 / Agents</span><strong>The specialists who handle each stage.</strong><small>Meet {{ catalog.agents.length }} focused agents →</small></NuxtLink>
          <NuxtLink to="/library/orchestrators"><span>03 / Orchestration</span><strong>The coordination that keeps the job moving.</strong><small>See every coordinator →</small></NuxtLink>
        </div>
      </div>
    </section>

    <section class="library-integrations">
      <div class="shell library-integrations__grid">
        <div>
          <p class="eyebrow eyebrow--dark">Use the stack you already have</p>
          <h2>The tools stay. <em>The work gets organized.</em></h2>
          <p>Browse {{ integrationCatalog.counts.providers }} providers and {{ integrationCatalog.counts.actions }} supported actions across communication, publishing, SEO, commerce, advertising, research, media, and more.</p>
          <NuxtLink to="/library/integrations">Explore every integration →</NuxtLink>
        </div>
        <div class="library-integrations__providers">
          <NuxtLink v-for="provider in featuredIntegrations" :key="provider.slug" :to="`/library/integrations/${provider.slug}`">
            <IntegrationMark :name="provider.name" :color="provider.color" :logo="provider.logo" />
            <span><strong>{{ provider.name }}</strong><small>{{ provider.actionCount }} actions</small></span>
          </NuxtLink>
        </div>
      </div>
    </section>

    <section class="library-section library-section--ink">
      <div class="shell">
        <div class="library-section__heading">
          <div><p class="eyebrow eyebrow--dark">Ready-made paths</p><h2>Start with a complete workflow.</h2></div>
          <div><p>See the real stages, specialists, connected apps, and approval points for work across engineering, content, sales, support, SEO, and paid media.</p><NuxtLink to="/library/workflows">Explore all workflows →</NuxtLink></div>
        </div>
        <div class="library-grid">
          <CatalogCard
            v-for="item in workflows || []"
            :key="item.slug"
            kind="workflows"
            :slug="item.slug"
            :name="item.name"
            :description="item.description"
            :domain="item.domain"
            :audience="item.audience"
            :color="item.color"
            :meta="`${item.stages.length} stages`"
          />
        </div>
      </div>
    </section>

    <section class="library-section">
      <div class="shell">
        <div class="library-section__heading">
          <div><p class="eyebrow">Learn the system</p><h2>Useful answers, not AI filler.</h2></div>
          <div><p>Each guide starts with the direct answer, then uses real product workflows, interactive visuals, and connected examples to explain the details.</p><NuxtLink to="/library/articles">Read all articles →</NuxtLink></div>
        </div>
        <div class="article-grid">
          <ArticleCard
            v-for="article in articles || []"
            :key="article.stem"
            :slug="articleSlug(article.stem)"
            :title="article.title"
            :description="article.description"
            :category="article.category"
            :reading-time="article.readingTime"
            :visual="article.visual"
          />
        </div>
      </div>
    </section>

    <section class="library-section library-section--deep">
      <div class="shell">
        <div class="library-section__heading">
          <div><p class="eyebrow eyebrow--dark">Focused responsibilities</p><h2>The right specialist at the right stage.</h2></div>
          <div><p>Agents research, create, review, and verify. Orchestrators keep the entire job consistent as it moves between them.</p><NuxtLink to="/library/agents">Meet every agent →</NuxtLink></div>
        </div>
        <div class="library-grid">
          <CatalogCard
            v-for="item in agents || []"
            :key="item.slug"
            kind="agents"
            :slug="item.slug"
            :name="item.name"
            :description="item.description"
            :domain="item.domain"
            :audience="item.audience"
            :color="item.color"
            :meta="item.role"
          />
        </div>
        <div class="library-grid" style="margin-top: 17px">
          <CatalogCard
            v-for="item in orchestrators || []"
            :key="item.slug"
            kind="orchestrators"
            :slug="item.slug"
            :name="item.name"
            :description="item.description"
            :domain="item.domain"
            :audience="item.audience"
            :color="item.color"
            :meta="`${item.workflowKeys.length} workflows`"
          />
        </div>
      </div>
    </section>
  </LibraryFrame>
</template>

<style scoped>
.library-stat { color: inherit; text-decoration: none; transition: transform 180ms ease, border-color 180ms ease; }
.library-stat:hover { border-color: currentColor; transform: translateY(-3px); }
.library-section__heading > div:last-child { display: grid; gap: 14px; justify-items: start; }
.library-map { padding: 38px 0; color: var(--ink); background: var(--paper); border-bottom: 1px solid rgb(7 10 15 / 10%); }
.library-map__grid { display: grid; grid-template-columns: minmax(0, .8fr) minmax(0, 1.2fr); gap: clamp(36px, 6vw, 90px); align-items: center; }
.library-map__intro h2 { max-width: 560px; margin: 9px 0 13px; font-size: clamp(30px, 3.6vw, 52px); line-height: .98; letter-spacing: -.05em; }
.library-map__intro h2 em { color: var(--blue); font-style: normal; }
.library-map__intro > p:last-child { max-width: 610px; margin: 0; color: #50535a; font-size: 17px; line-height: 1.62; }
.library-map__links { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); border: 1px solid rgb(7 10 15 / 13%); border-radius: 16px; overflow: hidden; }
.library-map__links a { display: grid; min-width: 0; min-height: 170px; padding: 20px; color: inherit; text-decoration: none; background: #fff; transition: color 180ms ease, background 180ms ease; }
.library-map__links a + a { border-left: 1px solid rgb(7 10 15 / 10%); }
.library-map__links a:hover { color: #fff; background: #3157d9; }
.library-map__links span { font-size: 12px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; opacity: .68; }
.library-map__links strong { align-self: end; font-size: 16px; line-height: 1.25; letter-spacing: -.02em; }
.library-map__links small { margin-top: 9px; font-size: 12px; font-weight: 700; opacity: .78; }
.library-integrations { padding: 72px 0; color: var(--paper); background: var(--ink); border-block: 1px solid rgb(255 255 255 / 8%); }
.library-integrations__grid { display: grid; grid-template-columns: minmax(0, .8fr) minmax(420px, 1fr); gap: clamp(42px, 7vw, 105px); align-items: center; }
.library-integrations h2 { max-width: 660px; margin: 9px 0 17px; font-size: clamp(39px, 5vw, 68px); line-height: .98; letter-spacing: -.06em; }
.library-integrations .eyebrow { color: var(--signal); }
.library-integrations h2 em { color: var(--signal); font-style: normal; }
.library-integrations__grid > div:first-child > p:not(.eyebrow) { max-width: 650px; color: var(--ink-soft); font-size: 17px; line-height: 1.68; }
.library-integrations__grid > div:first-child > a { display: inline-block; margin-top: 13px; color: var(--signal); font-size: 14px; font-weight: 750; text-decoration: none; }
.library-integrations__providers { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px; }
.library-integrations__providers > a { display: flex; min-width: 0; align-items: center; gap: 12px; padding: 12px; color: var(--paper); text-decoration: none; background: #11151e; border: 1px solid rgb(255 255 255 / 10%); border-radius: 14px; transition: background 180ms ease, transform 180ms ease; }
.library-integrations__providers > a:hover { background: #171c27; transform: translateY(-2px); }
.library-integrations__providers span { display: grid; min-width: 0; gap: 3px; }
.library-integrations__providers strong { overflow: hidden; font-size: 14px; text-overflow: ellipsis; white-space: nowrap; }
.library-integrations__providers small { color: var(--ink-muted); font-size: 11px; }
@media (max-width: 900px) { .library-map__grid { grid-template-columns: 1fr; gap: 25px; } }
@media (max-width: 900px) { .library-integrations__grid { grid-template-columns: 1fr; } }
@media (max-width: 640px) { .library-map { padding: 30px 0; } .library-map__intro > p:last-child { font-size: 16px; } .library-map__links { grid-template-columns: 1fr; } .library-map__links a { min-height: 132px; } .library-map__links a + a { border-top: 1px solid rgb(7 10 15 / 10%); border-left: 0; } .library-integrations { padding: 58px 0; } .library-integrations__providers { grid-template-columns: 1fr; } }
</style>
