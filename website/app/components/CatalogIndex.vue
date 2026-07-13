<script setup lang="ts">
interface CatalogItem {
  slug: string
  name: string
  description: string
  domain: string
  audience: string
  color: string
  role?: string
  roleClass?: 'reasoning' | 'mechanical' | 'review'
  stages?: unknown[]
  workflowKeys?: string[]
}

interface AnswerPoint {
  label: string
  text: string
}

const props = defineProps<{
  kind: 'workflows' | 'agents' | 'orchestrators'
  title: string
  accent: string
  description: string
  answerTitle: string
  answer: string
  answerPoints: AnswerPoint[]
  answerLink?: string
  answerLinkLabel?: string
  items: CatalogItem[]
}>()

const selectedDomain = ref('all')
const domains = computed(() => ['all', ...new Set(props.items.map((item) => item.domain))])
const visibleItems = computed(() =>
  selectedDomain.value === 'all' ? props.items : props.items.filter((item) => item.domain === selectedDomain.value),
)

function meta(item: CatalogItem) {
  if (props.kind === 'workflows') return `${item.stages?.length || 0} stages`
  if (props.kind === 'agents') return item.roleClass ? `${item.roleClass} role` : item.role
  return `${item.workflowKeys?.length || 0} workflows`
}
</script>

<template>
  <LibraryCollectionHero
    :kicker="`StackOS Library / ${kind}`"
    :title="title"
    :accent="accent"
    :description="description"
  >
    <template #meta>
        <span>{{ items.length }} in the library</span>
        <span>{{ domains.length - 1 }} areas of work</span>
        <span>Based on real StackOS workflows</span>
    </template>
  </LibraryCollectionHero>

  <section class="catalog-answer" aria-labelledby="catalog-answer-title">
    <div class="shell catalog-answer__grid">
      <div class="catalog-answer__copy">
        <p class="eyebrow">Quick answer</p>
        <h2 id="catalog-answer-title">{{ answerTitle }}</h2>
        <p>{{ answer }}</p>
        <NuxtLink v-if="answerLink" :to="answerLink">
          {{ answerLinkLabel || 'Read the complete guide' }} →
        </NuxtLink>
      </div>
      <div class="catalog-answer__points">
        <article v-for="point in answerPoints" :key="point.label">
          <span>{{ point.label }}</span>
          <p>{{ point.text }}</p>
        </article>
      </div>
    </div>
  </section>

  <section class="catalog-list">
    <div class="shell">
      <div v-if="items.length" class="catalog-filter" aria-label="Filter by area">
        <button
          v-for="domain in domains"
          :key="domain"
          type="button"
          :class="{ 'is-active': selectedDomain === domain }"
          @click="selectedDomain = domain"
        >
          {{ domain === 'all' ? 'All areas' : domain.replace('-', ' ') }}
        </button>
      </div>

      <div v-if="visibleItems.length" class="library-grid">
        <CatalogCard
          v-for="item in visibleItems"
          :key="item.slug"
          :kind="kind"
          :slug="item.slug"
          :name="item.name"
          :description="item.description"
          :domain="item.domain"
          :audience="item.audience"
          :color="item.color"
          :meta="meta(item)"
        />
      </div>

      <div v-else class="catalog-empty" role="status">
        <img src="/images/stackos-icon.png" alt="" width="46" height="46">
        <div>
          <h2>{{ items.length ? 'No matches in this area yet.' : 'The Library is refreshing.' }}</h2>
          <p>{{ items.length ? 'Choose another area to continue exploring.' : 'The latest StackOS definitions are being prepared. You can still explore practical guides now.' }}</p>
          <button v-if="items.length" type="button" @click="selectedDomain = 'all'">Show every area</button>
          <NuxtLink v-else to="/library/articles">Read practical guides →</NuxtLink>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.catalog-answer {
  padding: 34px 0;
  color: var(--ink);
  background: var(--paper);
  border-bottom: 1px solid rgb(7 10 15 / 10%);
}

.catalog-answer__grid {
  display: grid;
  grid-template-columns: minmax(0, 0.88fr) minmax(0, 1.12fr);
  gap: clamp(34px, 6vw, 90px);
  align-items: center;
}

.catalog-answer__copy h2 {
  max-width: 650px;
  margin: 8px 0 12px;
  font-size: clamp(28px, 3.1vw, 46px);
  line-height: 1.02;
  letter-spacing: -0.045em;
}

.catalog-answer__copy > p:not(.eyebrow) {
  max-width: 650px;
  margin: 0;
  color: #4f5157;
  font-size: 17px;
  line-height: 1.62;
}

.catalog-answer__copy a {
  display: inline-flex;
  margin-top: 14px;
  color: var(--blue);
  font-size: 14px;
  font-weight: 750;
  text-decoration: none;
}

.catalog-answer__points {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  border: 1px solid rgb(7 10 15 / 13%);
  border-radius: 16px;
  overflow: hidden;
}

.catalog-answer__points article {
  min-width: 0;
  padding: 19px;
  background: #fff;
}

.catalog-answer__points article + article { border-left: 1px solid rgb(7 10 15 / 10%); }
.catalog-answer__points span { color: #3157d9; font-size: 12px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
.catalog-answer__points p { margin: 8px 0 0; color: #272a31; font-size: 14px; line-height: 1.48; }

.catalog-empty {
  display: flex;
  max-width: 720px;
  gap: 18px;
  align-items: flex-start;
  padding: 26px;
  color: var(--paper);
  background: rgb(255 255 255 / 4%);
  border: 1px solid rgb(255 255 255 / 10%);
  border-radius: 16px;
}

.catalog-empty img { border-radius: 12px; }
.catalog-empty h2 { margin: 0; font-size: 24px; letter-spacing: -0.03em; }
.catalog-empty p { margin: 8px 0 0; color: var(--ink-soft); font-size: 15px; line-height: 1.65; }
.catalog-empty button,
.catalog-empty a { display: inline-flex; margin-top: 15px; padding: 0; color: var(--signal); font: inherit; font-size: 13px; font-weight: 700; text-decoration: none; background: none; border: 0; cursor: pointer; }

@media (max-width: 900px) {
  .catalog-answer__grid { grid-template-columns: 1fr; gap: 24px; }
}

@media (max-width: 640px) {
  .catalog-answer { padding: 28px 0; }
  .catalog-answer__copy > p:not(.eyebrow) { font-size: 16px; }
  .catalog-answer__points { grid-template-columns: 1fr; }
  .catalog-answer__points article + article { border-top: 1px solid rgb(7 10 15 / 10%); border-left: 0; }
}
</style>
