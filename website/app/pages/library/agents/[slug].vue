<script setup lang="ts">
const route = useRoute()
const slug = String(route.params.slug)
const { workflows, agentBySlug } = useLibraryCatalog()
const item = agentBySlug(slug)
if (!item) throw createError({ statusCode: 404, statusMessage: 'Agent not found' })

const relatedWorkflows = computed(() => workflows.filter((workflow) => item.workflowKeys.includes(workflow.key)))

useLibrarySeo({ title: `${item.name} AI agent — StackOS Library`, description: item.description })
useSchemaOrg([defineWebPage({ name: `${item.name} AI agent`, description: item.description }), defineBreadcrumb({ itemListElement: [{ name: 'Home', item: '/' }, { name: 'Library', item: '/library' }, { name: 'Agents', item: '/library/agents' }, { name: item.name, item: route.path }] })])
</script>

<template>
  <LibraryFrame>
    <section class="detail-hero">
      <div class="shell detail-hero__grid">
        <div>
          <p class="library-kicker">Specialist agent / {{ item.domain }}</p>
          <h1>{{ item.name }}</h1>
          <p class="detail-hero__lede">{{ item.description }}</p>
          <div class="detail-tags"><span>{{ item.role }}</span><span>{{ item.audience }}</span><span>{{ item.workflowKeys.length }} workflow{{ item.workflowKeys.length === 1 ? '' : 's' }}</span></div>
        </div>
        <GeneratedVisual class="detail-hero__visual" mode="roles" :color="item.color" :label="item.role" />
      </div>
    </section>

    <section class="detail-body">
      <div class="detail-body__narrow">
        <p class="eyebrow">Why this role exists</p>
        <h2>One focused responsibility, inside a complete job.</h2>
        <p class="detail-body__intro">StackOS agents are specialists. This role owns {{ item.role.toLowerCase() }} work while the workflow controls inputs, handoffs, checks, and approvals around it.</p>
        <ul class="detail-list">
          <li>Receives only the context and connected capabilities needed for its stage.</li>
          <li>Returns a clear result that the next stage can use and verify.</li>
          <li>Works within the visible workflow instead of becoming a separate, untracked chat.</li>
        </ul>

        <template v-if="relatedWorkflows.length">
          <p class="eyebrow detail-space">Where this agent works</p>
          <h2>Part of these workflows.</h2>
        </template>
      </div>
      <div v-if="relatedWorkflows.length" class="shell library-grid agent-workflows">
        <CatalogCard v-for="workflow in relatedWorkflows" :key="workflow.slug" kind="workflows" :slug="workflow.slug" :name="workflow.name" :description="workflow.description" :domain="workflow.domain" :audience="workflow.audience" :color="workflow.color" :meta="`${workflow.stages.length} stages`" />
      </div>
    </section>
  </LibraryFrame>
</template>

<style scoped>
.detail-space { margin-top: 80px; }
.agent-workflows { margin-top: 32px; }
</style>
