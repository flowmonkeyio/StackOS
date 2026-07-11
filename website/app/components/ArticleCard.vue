<script setup lang="ts">
defineProps<{
  slug: string
  title: string
  description: string
  category: string
  readingTime: string
  visual: string
}>()
</script>

<template>
  <NuxtLink class="article-card" :to="`/library/articles/${slug}`">
    <GeneratedVisual :mode="visual" color="#d9ff63" :label="category" compact />
    <div class="article-card__meta"><span>{{ category }}</span><span>{{ readingTime }}</span></div>
    <h3>{{ title }}</h3>
    <p>{{ description }}</p>
    <b>Read the guide <span aria-hidden="true">→</span></b>
  </NuxtLink>
</template>

<style scoped>
.article-card {
  display: flex;
  min-width: 0;
  flex-direction: column;
  color: var(--ink);
  text-decoration: none;
}

.article-card :deep(.generated-visual) {
  transition: transform 240ms ease, box-shadow 240ms ease;
}

.article-card:hover :deep(.generated-visual) {
  box-shadow: 0 20px 55px rgb(22 27 39 / 18%);
  transform: translateY(-4px);
}

.article-card__meta {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  margin-top: 18px;
  color: var(--muted-on-paper);
  font-family: var(--font-mono);
  font-size: 12px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.article-card h3 {
  margin: 13px 0 9px;
  font-size: clamp(22px, 2.2vw, 30px);
  line-height: 1.12;
  letter-spacing: -0.045em;
}

.article-card p {
  margin: 0;
  color: var(--muted-on-paper);
  font-size: 16px;
  line-height: 1.7;
}

.article-card > b {
  margin-top: 18px;
  color: var(--cobalt);
  font-size: 15px;
}
</style>
