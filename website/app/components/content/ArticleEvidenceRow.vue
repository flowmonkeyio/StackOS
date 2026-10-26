<script setup lang="ts">
defineProps<{
  label: string
  recordHeading: string
  evidenceLabel: string
  decisionLabel: string
}>()
</script>

<template>
  <dl class="article-evidence-row">
    <dt><span class="article-evidence-row__record-heading">{{ recordHeading }}: </span>{{ label }}</dt>
    <dd>
      <div class="article-evidence-row__evidence">
        <p class="article-evidence-row__heading">{{ evidenceLabel }}</p>
        <slot name="evidence" />
      </div>
      <div class="article-evidence-row__decision">
        <p class="article-evidence-row__heading">{{ decisionLabel }}</p>
        <slot name="decision" />
      </div>
    </dd>
  </dl>
</template>

<style scoped>
.article-evidence-row { display: grid; grid-template-columns: minmax(0, 220px) minmax(0, 1fr); gap: 24px; margin: 0; padding: 25px 0; border-top: 1px solid var(--paper-border); }
.article-evidence-row + .article-evidence-row { margin-top: 0; }
.article-evidence-row dt { color: var(--ink); font-size: 23px; font-weight: 750; line-height: 1.3; letter-spacing: -.035em; overflow-wrap: anywhere; }
.article-evidence-row dd { min-width: 0; margin: 0; }
.article-evidence-row__record-heading { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
.article-evidence-row .article-evidence-row__heading { margin: 0 0 8px; color: var(--muted-on-paper); font-family: var(--font-mono); font-size: 11px; font-weight: 700; line-height: 1.5; letter-spacing: .06em; text-transform: uppercase; }
.article-evidence-row__decision { margin-top: 18px; padding-left: 22px; border-left: 2px solid var(--cobalt); }
.article-evidence-row__decision .article-evidence-row__heading { color: var(--cobalt); }
.article-evidence-row :deep(p:not(.article-evidence-row__heading)) { margin: 0; font-size: 17px; line-height: 1.5; letter-spacing: -.015em; }
.article-evidence-row :deep(p + p) { margin-top: 12px; }
@media (max-width: 720px) {
  .article-evidence-row { grid-template-columns: minmax(0, 1fr); gap: 18px; padding-block: 24px; }
  .article-evidence-row dt { font-size: 21px; }
  .article-evidence-row__decision { padding-left: 16px; }
}
</style>
