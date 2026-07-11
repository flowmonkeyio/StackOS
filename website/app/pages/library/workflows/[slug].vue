<script setup lang="ts">
const route = useRoute()
const slug = String(route.params.slug)
const { workflows, workflowBySlug } = useLibraryCatalog()
const item = workflowBySlug(slug)

if (!item) throw createError({ statusCode: 404, statusMessage: 'Workflow not found' })

const related = computed(() => workflows.filter((entry) => entry.domain === item.domain && entry.slug !== slug).slice(0, 3))

useLibrarySeo({ title: `${item.name} workflow — StackOS Library`, description: item.description })
useSchemaOrg([
  defineWebPage({ name: `${item.name} workflow`, description: item.description }),
  defineBreadcrumb({ itemListElement: [{ name: 'Home', item: '/' }, { name: 'Library', item: '/library' }, { name: 'Workflows', item: '/library/workflows' }, { name: item.name, item: route.path }] }),
])
</script>

<template>
  <LibraryFrame>
    <section class="detail-hero">
      <div class="shell detail-hero__grid">
        <div>
          <p class="library-kicker">{{ item.audience }} / {{ item.domain.replace('-', ' ') }}</p>
          <h1>{{ item.name }}</h1>
          <p class="detail-hero__lede">{{ item.description }}</p>
          <div class="detail-tags">
            <span>{{ item.stages.length }} stages</span>
            <span>{{ item.agentRefs.length }} specialist agents</span>
            <span v-if="item.integrations.length">Works across connected apps</span>
          </div>
        </div>
        <GeneratedVisual class="detail-hero__visual" mode="workflow" :color="item.color" :label="item.name" />
      </div>
    </section>

    <section class="detail-body">
      <div class="detail-body__narrow">
        <p class="eyebrow">When this workflow helps</p>
        <h2>A repeatable path, adapted to the request.</h2>
        <p class="detail-body__intro">The preset supplies the proven shape of the work. Your request and current context determine the exact plan before anything starts.</p>
        <ul class="detail-list">
          <li v-for="useCase in item.whenToUse" :key="useCase">{{ useCase }}</li>
        </ul>
      </div>

      <div class="shell workflow-showcase">
        <div class="library-section__heading">
          <div><p class="eyebrow">Live plan</p><h2>Watch the work move step by step.</h2></div>
          <p>The highlighted stage is active. Earlier work is done, later work waits, and dependencies keep the order truthful.</p>
        </div>
        <WorkflowMap :stages="item.stages" :color="item.color" />
      </div>

      <div class="detail-body__narrow" style="margin-top: 88px">
        <template v-if="item.agentRefs.length">
          <p class="eyebrow">Specialists inside this workflow</p>
          <h2>Clear roles for each part of the job.</h2>
          <div class="detail-link-grid">
            <NuxtLink v-for="agent in item.agentRefs" :key="agent.key" :to="`/library/agents/${agent.slug}`">
              <span>Agent</span><strong>{{ agent.name }}</strong><b aria-hidden="true">→</b>
            </NuxtLink>
          </div>
        </template>

        <template v-if="item.integrations.length">
          <p class="eyebrow detail-space">Connected work</p>
          <h2>Built to use the tools you already have.</h2>
          <div class="detail-tags detail-tags--paper"><span v-for="integration in item.integrations" :key="integration">{{ integration }}</span></div>
        </template>
      </div>
    </section>

    <section v-if="related.length" class="related-section">
      <div class="shell">
        <div class="library-section__heading"><div><p class="eyebrow eyebrow--dark">Keep exploring</p><h2>Related workflows.</h2></div><NuxtLink to="/library/workflows">See all 23 →</NuxtLink></div>
        <div class="library-grid">
          <CatalogCard v-for="entry in related" :key="entry.slug" kind="workflows" :slug="entry.slug" :name="entry.name" :description="entry.description" :domain="entry.domain" :audience="entry.audience" :color="entry.color" :meta="`${entry.stages.length} stages`" />
        </div>
      </div>
    </section>
  </LibraryFrame>
</template>

<style scoped>
.detail-link-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-top: 30px; }
.detail-link-grid a { display: grid; grid-template-columns: 1fr auto; gap: 6px 16px; padding: 18px; color: var(--ink); text-decoration: none; background: #eceae2; border: 1px solid var(--paper-border); border-radius: 12px; }
.detail-link-grid span { grid-column: 1; color: var(--cobalt); font-family: var(--font-mono); font-size: 11px; letter-spacing: .08em; text-transform: uppercase; }
.detail-link-grid strong { font-size: 14px; }
.detail-link-grid b { grid-row: 1 / 3; grid-column: 2; align-self: center; color: var(--cobalt); }
.detail-space { margin-top: 80px; }
.detail-tags--paper span { color: #51514b; background: #eceae2; border-color: var(--paper-border); }
.related-section .library-section__heading > a { align-self: end; color: var(--signal); text-decoration: none; }
@media (max-width: 620px) { .detail-link-grid { grid-template-columns: 1fr; } }
</style>
