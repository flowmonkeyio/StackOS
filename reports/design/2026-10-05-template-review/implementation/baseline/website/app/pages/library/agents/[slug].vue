<script setup lang="ts">
const route = useRoute()
const slug = String(route.params.slug)
const { workflows, agentBySlug } = useLibraryCatalog()
const item = agentBySlug(slug)
if (!item) throw createError({ statusCode: 404, statusMessage: 'Agent not found' })

const relatedWorkflows = computed(() => workflows.filter((workflow) => item.workflowKeys.includes(workflow.key)))
const seoTitle = /\bAI agent\b/i.test(item.name)
  ? item.name.replace(/\bAI agent\b/i, 'AI agent')
  : /\bAgent\b/i.test(item.name)
    ? item.name.replace(/\bAgent\b/i, 'AI agent')
    : `${item.name} AI agent`

useSiteSeo({ title: seoTitle, description: item.description })
useSchemaOrg([defineWebPage({ name: seoTitle, description: item.description }), defineBreadcrumb({ itemListElement: [{ name: 'Home', item: '/' }, { name: 'Library', item: '/library/' }, { name: 'Agents', item: '/library/agents/' }, { name: item.name, item: canonicalPath(route.path) }] })])
</script>

<template>
  <LibraryFrame :breadcrumb-current-label="item.name">
    <section class="detail-hero">
      <div class="shell detail-hero__grid">
        <div>
          <p class="library-kicker">Specialist agent / {{ item.domain }}</p>
          <h1>{{ item.name }}</h1>
          <p class="detail-hero__lede">{{ item.description }}</p>
          <div class="detail-tags"><span>{{ item.role }}</span><span>{{ item.roleClass }} role</span><span>{{ item.workflowKeys.length }} workflow{{ item.workflowKeys.length === 1 ? '' : 's' }}</span></div>
        </div>
        <GeneratedVisual class="detail-hero__visual" mode="roles" :color="item.color" :label="item.role" />
      </div>
    </section>

    <section class="detail-body" :data-agent-contract="item.slug">
      <div class="detail-body__narrow">
        <section class="agent-contract-section" aria-labelledby="agent-mission">
          <p class="eyebrow">Mission</p>
          <h2 id="agent-mission">What this agent is here to do.</h2>
          <p class="detail-body__intro">{{ item.mission }}</p>
        </section>

        <section class="agent-contract-section" aria-labelledby="agent-responsibilities">
          <p class="eyebrow">Responsibilities</p>
          <h2 id="agent-responsibilities">What it owns inside the workflow.</h2>
          <ul class="detail-list">
            <li v-for="responsibility in item.responsibilities" :key="responsibility">{{ responsibility }}</li>
          </ul>
        </section>

        <section class="agent-contract-section" aria-labelledby="agent-boundaries">
          <p class="eyebrow">Boundaries</p>
          <h2 id="agent-boundaries">What it must and must not do.</h2>
          <div class="agent-contract-grid">
            <section aria-labelledby="agent-must-do">
              <h3 id="agent-must-do">Must do</h3>
              <ul class="detail-list">
                <li v-for="requirement in item.must_do" :key="requirement">{{ requirement }}</li>
              </ul>
            </section>
            <section aria-labelledby="agent-must-not-do">
              <h3 id="agent-must-not-do">Must not do</h3>
              <ul class="detail-list">
                <li v-for="boundary in item.must_not_do" :key="boundary">{{ boundary }}</li>
              </ul>
            </section>
          </div>
        </section>

        <section class="agent-contract-section" aria-labelledby="agent-handoff">
          <p class="eyebrow">Handoff contract</p>
          <h2 id="agent-handoff">What it receives and returns.</h2>
          <div class="agent-contract-grid">
            <section aria-labelledby="agent-handoff-inputs">
              <h3 id="agent-handoff-inputs">Handoff inputs</h3>
              <ul class="detail-list">
                <li v-for="input in item.handoff_inputs" :key="input">{{ input }}</li>
              </ul>
            </section>
            <section aria-labelledby="agent-handoff-outputs">
              <h3 id="agent-handoff-outputs">Handoff outputs</h3>
              <ul class="detail-list">
                <li v-for="output in item.handoff_outputs" :key="output">{{ output }}</li>
              </ul>
            </section>
          </div>
        </section>

        <section class="agent-contract-section" aria-labelledby="agent-success-criteria">
          <p class="eyebrow">Success criteria</p>
          <h2 id="agent-success-criteria">How the handoff is ready.</h2>
          <ul class="detail-list">
            <li v-for="criterion in item.success_criteria" :key="criterion">{{ criterion }}</li>
          </ul>
        </section>

        <section v-if="relatedWorkflows.length" class="agent-contract-section" aria-labelledby="agent-workflows">
          <p class="eyebrow">Where this agent works</p>
          <h2 id="agent-workflows">Part of these workflows.</h2>
        </section>
      </div>
      <div v-if="relatedWorkflows.length" class="shell library-grid agent-workflows">
        <CatalogCard v-for="workflow in relatedWorkflows" :key="workflow.slug" kind="workflows" :slug="workflow.slug" :name="workflow.name" :description="workflow.description" :domain="workflow.domain" :audience="workflow.audience" :color="workflow.color" :meta="`${workflow.stages.length} stages`" />
      </div>
    </section>
  </LibraryFrame>
</template>

<style scoped>
.agent-contract-section + .agent-contract-section { margin-top: 80px; }
.agent-contract-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 32px; margin-top: 28px; }
.agent-contract-grid h3 { margin: 0; color: var(--ink); font-size: 16px; letter-spacing: -0.02em; }
.agent-contract-grid .detail-list { margin-top: 14px; }
.agent-workflows { margin-top: 32px; }
@media (max-width: 620px) { .agent-contract-grid { grid-template-columns: 1fr; } }
</style>
