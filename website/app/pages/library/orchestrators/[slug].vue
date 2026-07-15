<script setup lang="ts">
const route = useRoute()
const slug = String(route.params.slug)
const { workflows, orchestratorBySlug } = useLibraryCatalog()
const item = orchestratorBySlug(slug)
if (!item) throw createError({ statusCode: 404, statusMessage: 'Orchestrator not found' })

const relatedWorkflows = computed(() => workflows.filter((workflow) => item.workflowKeys.includes(workflow.key)))

useLibrarySeo({ title: `${item.name} — AI workflow orchestrator`, description: item.description })
useSchemaOrg([defineWebPage({ name: item.name, description: item.description }), defineBreadcrumb({ itemListElement: [{ name: 'Home', item: '/' }, { name: 'Library', item: '/library' }, { name: 'Orchestrators', item: '/library/orchestrators' }, { name: item.name, item: route.path }] })])
</script>

<template>
  <LibraryFrame :breadcrumb-current-label="item.name">
    <section class="detail-hero">
      <div class="shell detail-hero__grid">
        <div>
          <p class="library-kicker">Workflow coordinator / {{ item.domain }}</p>
          <h1>{{ item.name }}</h1>
          <p class="detail-hero__lede">{{ item.description }}</p>
          <div class="detail-tags"><span>{{ item.workflowKeys.length }} workflows</span><span>{{ item.agentRefs.length }} specialist roles</span><span>{{ item.audience }}</span></div>
        </div>
        <GeneratedVisual class="detail-hero__visual" mode="connections" :color="item.color" label="Complete job coordination" />
      </div>
    </section>

    <section class="detail-body">
      <div class="detail-body__narrow">
        <p class="eyebrow">The coordinating role</p>
        <h2>Keeps the whole job coherent.</h2>
        <p class="detail-body__intro">The orchestrator turns the request into a complete plan, assembles the right context for every stage, brings in focused specialists, and protects review and approval boundaries.</p>
        <ul class="detail-list">
          <li>Adapts the reusable workflow to the current goal and available context.</li>
          <li>Separates research, creation, review, and action so responsibilities stay clear.</li>
          <li>Keeps status, dependencies, decisions, and proof connected from start to finish.</li>
        </ul>

        <p v-if="item.agentRefs.length" class="eyebrow detail-space">Specialists it coordinates</p>
        <h2 v-if="item.agentRefs.length">A team assembled around the work.</h2>
        <div class="coordinator-agents">
          <NuxtLink v-for="agent in item.agentRefs" :key="agent.key" :to="`/library/agents/${agent.slug}`">{{ agent.name }} <span>→</span></NuxtLink>
        </div>
      </div>

      <div v-if="relatedWorkflows.length" class="shell orchestrator-workflows">
        <div class="library-section__heading"><div><p class="eyebrow">Workflows it coordinates</p><h2>See the complete plans.</h2></div><p>Open a workflow to watch its stages move and see which specialists and connected apps take part.</p></div>
        <div class="library-grid"><CatalogCard v-for="workflow in relatedWorkflows" :key="workflow.slug" kind="workflows" :slug="workflow.slug" :name="workflow.name" :description="workflow.description" :domain="workflow.domain" :audience="workflow.audience" :color="workflow.color" :meta="`${workflow.stages.length} stages`" /></div>
      </div>
    </section>
  </LibraryFrame>
</template>

<style scoped>
.detail-space { margin-top: 80px; }
.coordinator-agents { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px; margin-top: 28px; }
.coordinator-agents a { display: flex; justify-content: space-between; gap: 12px; padding: 15px; color: var(--ink); font-size: 13px; font-weight: 650; text-decoration: none; background: #eceae2; border: 1px solid var(--paper-border); border-radius: 10px; }
.coordinator-agents span { color: var(--cobalt); }
.orchestrator-workflows { margin-top: 95px; }
@media (max-width: 620px) { .coordinator-agents { grid-template-columns: 1fr; } }
</style>
