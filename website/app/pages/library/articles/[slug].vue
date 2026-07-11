<script setup lang="ts">
const route = useRoute()
const slug = String(route.params.slug)
const { data: article } = await useAsyncData(`article-${slug}`, () => queryCollection('articles').where('stem', '=', `articles/${slug}`).first())

if (!article.value) throw createError({ statusCode: 404, statusMessage: 'Article not found' })

const { data: allArticles } = await useAsyncData(`related-articles-${slug}`, () => queryCollection('articles').order('publishedAt', 'DESC').all())
const { workflows: allWorkflows, agents: allAgents } = useLibraryCatalog()

const relatedArticles = computed(() => (allArticles.value || []).filter((entry) => article.value!.relatedArticles?.includes(entry.stem.split('/').at(-1) || '')))
const relatedWorkflows = computed(() => allWorkflows.filter((entry) => article.value!.relatedWorkflows?.includes(entry.slug)))
const relatedAgents = computed(() => allAgents.filter((entry) => article.value!.relatedAgents?.includes(entry.slug)))
const config = useRuntimeConfig()

useLibrarySeo({ title: `${article.value.title} — StackOS`, description: article.value.description, type: 'article', publishedAt: article.value.publishedAt, updatedAt: article.value.updatedAt })
useSchemaOrg([
  defineArticle({
    headline: article.value.title,
    description: article.value.description,
    datePublished: article.value.publishedAt,
    dateModified: article.value.updatedAt,
    author: { '@type': 'Organization', name: article.value.author },
    image: article.value.heroImage?.src || `${config.public.siteUrl}/images/plugins.png`,
  }),
  defineBreadcrumb({ itemListElement: [{ name: 'Home', item: '/' }, { name: 'Library', item: '/library' }, { name: 'Articles', item: '/library/articles' }, { name: article.value.title, item: route.path }] }),
])

function articleSlug(stem: string) { return stem.split('/').at(-1) || stem }
function displayDate(date: string) {
  return new Intl.DateTimeFormat('en-US', { dateStyle: 'long', timeZone: 'UTC' }).format(new Date(`${date}T12:00:00Z`))
}
</script>

<template>
  <LibraryFrame v-if="article">
    <article>
      <header class="article-hero">
        <div class="shell article-hero__grid">
          <div>
            <p class="library-kicker">{{ article.category }}</p>
            <h1>{{ article.title }}</h1>
            <p class="article-hero__description">{{ article.description }}</p>
            <div class="article-hero__byline"><span>{{ article.author }}</span><time :datetime="article.publishedAt">Published {{ displayDate(article.publishedAt) }}</time><span>{{ article.readingTime }}</span></div>
          </div>
          <GeneratedVisual class="article-hero__visual" :mode="article.visual" color="#d9ff63" :label="article.category" />
        </div>
      </header>

      <div class="article-body">
        <div class="article-prose">
          <ContentRenderer :value="article" />

          <section v-if="relatedWorkflows.length || relatedAgents.length" class="article-related" aria-labelledby="related-product">
            <p class="eyebrow">Explore the system</p>
            <h2 id="related-product">Related workflows and agents</h2>
            <div class="article-related__links">
              <NuxtLink v-for="workflow in relatedWorkflows" :key="workflow.slug" :to="`/library/workflows/${workflow.slug}`"><span>Workflow</span><strong>{{ workflow.name }}</strong><b>→</b></NuxtLink>
              <NuxtLink v-for="agent in relatedAgents" :key="agent.slug" :to="`/library/agents/${agent.slug}`"><span>Agent</span><strong>{{ agent.name }}</strong><b>→</b></NuxtLink>
            </div>
          </section>
        </div>

        <section v-if="relatedArticles.length" class="shell article-more" aria-labelledby="keep-reading">
          <div class="library-section__heading"><div><p class="eyebrow">Keep reading</p><h2 id="keep-reading">Related guides.</h2></div><NuxtLink to="/library/articles">All articles →</NuxtLink></div>
          <div class="article-grid"><ArticleCard v-for="entry in relatedArticles" :key="entry.stem" :slug="articleSlug(entry.stem)" :title="entry.title" :description="entry.description" :category="entry.category" :reading-time="entry.readingTime" :visual="entry.visual" /></div>
        </section>
      </div>
    </article>
  </LibraryFrame>
</template>

<style scoped>
.article-related__links { display: grid; gap: 9px; }
.article-related__links a { display: grid; grid-template-columns: 1fr auto; gap: 4px 16px; padding: 15px 16px; color: var(--ink); text-decoration: none; background: #eceae2; border: 1px solid var(--paper-border); border-radius: 11px; }
.article-related__links span { color: var(--cobalt); font-family: var(--font-mono); font-size: 11px; letter-spacing: .08em; text-transform: uppercase; }
.article-related__links strong { font-size: 14px; }
.article-related__links b { grid-row: 1 / 3; grid-column: 2; align-self: center; color: var(--cobalt); }
.article-more { margin-top: 105px; padding-top: 82px; border-top: 1px solid var(--paper-border); }
.article-more .library-section__heading > a { align-self: end; color: var(--cobalt); font-size: 13px; font-weight: 700; text-decoration: none; }
</style>
