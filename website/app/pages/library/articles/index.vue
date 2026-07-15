<script setup lang="ts">
const { data: articles } = await useAsyncData('all-library-articles', () => queryCollection('articles').order('publishedAt', 'DESC').all())

useLibrarySeo({ title: 'StackOS articles — Practical guides to AI agents and agentic workflows', description: 'Practical guides grounded in firsthand workflow work, primary sources, and specific implementation evidence across AI agents, orchestration, security, and connected tools.' })
useSchemaOrg([defineWebPage({ '@type': 'CollectionPage', name: 'StackOS Articles' }), defineBreadcrumb({ itemListElement: [{ name: 'Home', item: '/' }, { name: 'Library', item: '/library' }, { name: 'Articles', item: '/library/articles' }] })])

function articleSlug(stem: string) { return stem.split('/').at(-1) || stem }
</script>

<template>
  <LibraryFrame>
    <section class="catalog-hero">
      <div class="shell">
        <p class="library-kicker">StackOS Library / Articles</p>
        <h1>Practical answers with <em>the work attached.</em></h1>
        <p class="catalog-hero__lede">Direct explanations, field-tested methods, and source-backed examples for people building a dependable way to work with AI.</p>
        <div class="catalog-hero__meta"><span>{{ articles?.length || 0 }} guides</span><span>Firsthand practice and primary sources</span><span>Reviewed for claims, voice, and disclosure</span></div>
      </div>
    </section>

    <section class="library-section">
      <div class="shell article-grid">
        <ArticleCard v-for="article in articles || []" :key="article.stem" :slug="articleSlug(article.stem)" :title="article.title" :description="article.description" :category="article.category" :reading-time="article.readingTime" :visual="article.visual" />
      </div>
    </section>
  </LibraryFrame>
</template>
