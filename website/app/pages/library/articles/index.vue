<script setup lang="ts">
const { data: articles } = await useAsyncData('all-library-articles', () => queryCollection('articles').order('publishedAt', 'DESC').all())

useLibrarySeo({ title: 'StackOS articles — Practical guides to AI agents and agentic workflows', description: 'Clear, visual guides to AI agents, agentic workflows, orchestrators, connected tools, security, and putting AI to work across a business.' })
useSchemaOrg([defineWebPage({ '@type': 'CollectionPage', name: 'StackOS Articles' }), defineBreadcrumb({ itemListElement: [{ name: 'Home', item: '/' }, { name: 'Library', item: '/library' }, { name: 'Articles', item: '/library/articles' }] })])

function articleSlug(stem: string) { return stem.split('/').at(-1) || stem }
</script>

<template>
  <LibraryFrame>
    <section class="catalog-hero">
      <div class="shell">
        <p class="library-kicker">StackOS Library / Articles</p>
        <h1>Practical answers with <em>the work attached.</em></h1>
        <p class="catalog-hero__lede">Direct explanations, real workflow examples, and interactive visuals for people building a dependable way to work with AI.</p>
        <div class="catalog-hero__meta"><span>{{ articles?.length || 0 }} guides</span><span>Written for people and answer engines</span><span>Reviewed product sources</span></div>
      </div>
    </section>

    <section class="library-section">
      <div class="shell article-grid">
        <ArticleCard v-for="article in articles || []" :key="article.stem" :slug="articleSlug(article.stem)" :title="article.title" :description="article.description" :category="article.category" :reading-time="article.readingTime" :visual="article.visual" />
      </div>
    </section>
  </LibraryFrame>
</template>
