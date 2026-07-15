<script setup lang="ts">
const route = useRoute()
const slug = String(route.params.slug)
const { workflows, workflowBySlug } = useLibraryCatalog()
const item = workflowBySlug(slug)

if (!item) throw createError({ statusCode: 404, statusMessage: 'Workflow not found' })

const related = computed(() => workflows.filter((entry) => entry.domain === item.domain && entry.slug !== slug).slice(0, 3))
const setupLabels = {
  available: 'Ready with project context',
  'connection-required': 'Connection required',
  'project-adapter-required': 'Project setup required',
  mixed: 'Depends on the selected path',
} as const
const handoffWorkflows = computed(() => item.handoffs.flatMap((handoff) => {
  const workflow = workflows.find((entry) => entry.key === handoff.workflowKey)
  return workflow ? [{ ...handoff, workflow }] : []
}))

useLibrarySeo({ title: `${item.name} workflow — StackOS Library`, description: item.description })
useSchemaOrg([
  defineWebPage({ name: `${item.name} workflow`, description: item.description }),
  defineBreadcrumb({ itemListElement: [{ name: 'Home', item: '/' }, { name: 'Library', item: '/library' }, { name: 'Workflows', item: '/library/workflows' }, { name: item.name, item: route.path }] }),
])
</script>

<template>
  <LibraryFrame :breadcrumb-current-label="item.name">
    <section class="detail-hero">
      <div class="shell detail-hero__grid">
        <div>
          <p class="library-kicker">{{ item.audience }} / {{ item.domain.replace('-', ' ') }}</p>
          <h1>{{ item.name }}</h1>
          <p class="detail-hero__lede">{{ item.description }}</p>
          <div class="detail-tags">
            <span>{{ item.stages.length }} stages</span>
            <span>{{ item.agentRefs.length }} specialist agents</span>
            <span>{{ setupLabels[item.setup] }}</span>
          </div>
        </div>
        <GeneratedVisual class="detail-hero__visual" mode="workflow" :color="item.color" :label="item.name" />
      </div>
    </section>

    <section class="detail-body">
      <div class="detail-body__narrow">
        <p class="eyebrow">The problem</p>
        <h2>{{ item.problem }}</h2>
        <p v-if="item.whyAi" class="detail-body__intro">{{ item.whyAi }}</p>
      </div>

      <div class="shell experience-grid">
        <article>
          <p class="eyebrow eyebrow--dark">What you do</p>
          <ol><li v-for="step in item.operatorPath" :key="step">{{ step }}</li></ol>
        </article>
        <article>
          <p class="eyebrow eyebrow--dark">What the agent does</p>
          <ol><li v-for="step in item.agentPath" :key="step">{{ step }}</li></ol>
        </article>
      </div>

      <div class="detail-body__narrow detail-space">
        <p class="eyebrow">When this workflow helps</p>
        <h2>A reusable method, adapted to the request.</h2>
        <ul class="detail-list">
          <li v-for="useCase in item.whenToUse" :key="useCase">{{ useCase }}</li>
        </ul>
      </div>

      <div class="shell readiness-grid">
        <article>
          <p class="eyebrow eyebrow--dark">Before it starts</p>
          <h3>{{ setupLabels[item.setup] }}</h3>
          <ul><li v-for="prerequisite in item.prerequisites" :key="prerequisite">{{ prerequisite }}</li></ul>
        </article>
        <article>
          <p class="eyebrow eyebrow--dark">What proves it worked</p>
          <h3>Evidence, not a success claim.</h3>
          <ul><li v-for="proof in item.proof" :key="proof">{{ proof }}</li></ul>
        </article>
      </div>

      <div class="shell workflow-showcase">
        <div class="library-section__heading">
          <div><p class="eyebrow">Workflow path</p><h2>The reusable stages of the work.</h2></div>
          <p>A real run expands these stages around the request, context, selected tools, approvals, and dependencies.</p>
        </div>
        <WorkflowMap :stages="item.stages" :color="item.color" />
      </div>

      <div class="detail-body__narrow" style="margin-top: 88px">
        <template v-if="item.safeStoppingPoints.length || item.recovery.length">
          <p class="eyebrow">Safe stopping and recovery</p>
          <h2>Useful even when the whole path cannot run.</h2>
          <ul class="detail-list">
            <li v-for="point in item.safeStoppingPoints" :key="point">{{ point }}</li>
            <li v-for="point in item.recovery" :key="point">{{ point }}</li>
          </ul>
        </template>

        <template v-if="handoffWorkflows.length">
          <p class="eyebrow detail-space">Documented follow-on work</p>
          <h2>The next workflow is conditional, not hidden.</h2>
          <div class="handoff-list">
            <NuxtLink v-for="handoff in handoffWorkflows" :key="handoff.workflowKey" :to="`/library/workflows/${handoff.workflow.slug}`">
              <span>{{ handoff.relationship }}</span>
              <strong>{{ handoff.workflow.name }}</strong>
              <p>{{ handoff.when }}</p>
            </NuxtLink>
          </div>
        </template>

        <template v-if="item.agentRefs.length">
          <p class="eyebrow detail-space">Specialists inside this workflow</p>
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
        <div class="library-section__heading"><div><p class="eyebrow eyebrow--dark">Keep exploring</p><h2>Related workflows.</h2></div><NuxtLink to="/library/workflows">See all {{ workflows.length }} →</NuxtLink></div>
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
.experience-grid,
.readiness-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; margin-top: 70px; }
.experience-grid article,
.readiness-grid article { padding: clamp(24px, 4vw, 42px); color: var(--ink); background: #eceae2; border: 1px solid var(--paper-border); border-radius: 16px; }
.experience-grid ol,
.readiness-grid ul { display: grid; gap: 13px; margin: 24px 0 0; padding-left: 22px; color: #404147; line-height: 1.6; }
.readiness-grid h3 { margin: 10px 0 0; font-size: 23px; letter-spacing: -.03em; }
.handoff-list { display: grid; gap: 10px; margin-top: 26px; }
.handoff-list a { display: grid; grid-template-columns: 100px 1fr; gap: 6px 18px; padding: 20px; color: var(--ink); text-decoration: none; background: #eceae2; border: 1px solid var(--paper-border); border-radius: 12px; }
.handoff-list span { color: var(--cobalt); font-family: var(--font-mono); font-size: 11px; letter-spacing: .08em; text-transform: uppercase; }
.handoff-list strong { font-size: 15px; }
.handoff-list p { grid-column: 2; margin: 0; color: #55565d; font-size: 14px; line-height: 1.55; }
.detail-tags--paper span { color: #51514b; background: #eceae2; border-color: var(--paper-border); }
.related-section .library-section__heading > a { align-self: end; color: var(--signal); text-decoration: none; }
@media (max-width: 720px) {
  .detail-link-grid,
  .experience-grid,
  .readiness-grid { grid-template-columns: 1fr; }
  .handoff-list a { grid-template-columns: 1fr; }
  .handoff-list p { grid-column: 1; }
}
</style>
