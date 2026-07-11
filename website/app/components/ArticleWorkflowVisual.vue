<script setup lang="ts">
const props = defineProps<{ workflow: string; title: string }>()
const { workflowBySlug } = useLibraryCatalog()
const item = workflowBySlug(props.workflow)
</script>

<template>
  <figure v-if="item" class="article-visual">
    <figcaption>
      <span>Interactive workflow</span>
      <strong>{{ title }}</strong>
    </figcaption>
    <WorkflowMap :stages="item.stages" :color="item.color" />
  </figure>
</template>

<style scoped>
.article-visual {
  width: min(1120px, calc(100vw - 48px));
  margin: 70px 50% 72px;
  transform: translateX(-50%);
}

.article-visual figcaption {
  margin-bottom: 17px;
}

.article-visual figcaption span,
.article-visual figcaption strong {
  display: block;
}

.article-visual figcaption span {
  color: var(--cobalt);
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.09em;
  text-transform: uppercase;
}

.article-visual figcaption strong {
  margin-top: 7px;
  color: var(--ink);
  font-size: clamp(20px, 3vw, 34px);
  line-height: 1.15;
  letter-spacing: -0.04em;
}

@media (max-width: 720px) {
  .article-visual {
    width: calc(100vw - 32px);
    margin-block: 52px;
  }
}
</style>
