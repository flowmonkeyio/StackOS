<script setup lang="ts">
defineProps<{ mode: string; title: string; caption: string }>()
</script>

<template>
  <figure class="article-concept">
    <figcaption>
      <strong>{{ title }}</strong>
      <span>{{ caption }}</span>
    </figcaption>
    <GeneratedVisual :mode="mode" color="#7892ff" :label="title" />
  </figure>
</template>

<style scoped>
.article-concept {
  width: min(840px, calc(100vw - 48px));
  margin: 48px 50% 54px;
  transform: translateX(-50%);
}

.article-concept figcaption {
  max-width: 720px;
  margin: 0 0 15px;
  color: var(--muted-on-paper);
  font-size: 14px;
  line-height: 1.65;
}

.article-concept figcaption strong,
.article-concept figcaption span { display: block; }

.article-concept figcaption strong {
  margin-bottom: 5px;
  color: var(--ink);
  font-size: 18px;
  letter-spacing: -0.02em;
}

@media (max-width: 720px) {
  .article-concept {
    width: calc(100vw - 32px);
    margin-block: 40px;
  }
}
</style>
