<script setup lang="ts">
const { workflows: items } = useLibraryCatalog()
const previewSlugs = ['seo-website-analysis', 'seo-content-refresh', 'engineering-tracked-delivery']
const workflowPreviews = previewSlugs.flatMap((slug) => {
  const item = items.find((workflow) => workflow.slug === slug)
  return item ? [item] : []
})

useSiteSeo({ title: `AI workflow automation library — ${items.length} complete workflows`, description: `Explore ${items.length} workflow contracts with visible stages, state, tool boundaries, outputs, recovery, and verification across engineering, content, marketing, sales, support, SEO, communications, and paid media.` })
useSchemaOrg([defineWebPage({ '@type': 'CollectionPage', name: 'StackOS Workflow Library' }), defineBreadcrumb({ itemListElement: [{ name: 'Home', item: '/' }, { name: 'Library', item: '/library/' }, { name: 'Workflows', item: '/library/workflows/' }] })])
useHead({ script: [{ key: 'workflow-library-faq', type: 'application/ld+json', innerHTML: JSON.stringify({ '@context': 'https://schema.org', '@type': 'FAQPage', mainEntity: [{ '@type': 'Question', name: 'What is AI workflow automation?', acceptedAnswer: { '@type': 'Answer', text: 'AI workflow automation gives an AI system a reusable execution contract from a request to a verified result. The contract defines state, dependencies, scoped tools and actions, expected outputs, recovery, and completion criteria.' } }] }) }] })
</script>

<template>
  <LibraryFrame>
    <CatalogIndex
      kind="workflows"
      title="Complete workflows for"
      accent="real work."
      description="A workflow gives AI a reusable execution contract from request to verified result. Browse each StackOS workflow and inspect the stages, state, tool boundaries, outputs, and recovery paths inside."
      answer-title="What is AI workflow automation?"
      answer="AI workflow automation combines a reusable workflow contract with a concrete run plan for the current request. The workflow defines the operating boundary; the run records state, dependencies, scoped tools, outputs, recovery, and verification."
      :answer-points="[{ label: 'Workflow', text: 'The reusable contract and rules for a type of work.' }, { label: 'Run plan', text: 'The concrete steps, state, grants, and outputs for this request.' }, { label: 'Verification', text: 'Acceptance criteria and receipts show whether the result is complete.' }]"
      answer-link="/library/articles/what-is-an-agentic-workflow/"
      answer-link-label="Learn how agentic workflows work"
      :items="items"
    >
      <template #before-list>
        <section class="workflow-previews" aria-labelledby="workflow-previews-title">
          <div class="shell">
            <div class="library-section__heading">
              <div><p class="eyebrow">Three places to start</p><h2 id="workflow-previews-title">Check what the job needs and produces.</h2></div>
              <p>Compare these workflows before choosing one. The prerequisites and steps below come from the same contracts as their detail pages.</p>
            </div>
            <div class="workflow-previews__grid">
              <article v-for="item in workflowPreviews" :key="item.slug">
                <h3>{{ item.name }}</h3>
                <p class="workflow-previews__outcome">{{ item.outcome }}</p>
                <h4>Before you start</h4>
                <ul><li v-for="prerequisite in item.prerequisites" :key="prerequisite">{{ prerequisite }}</li></ul>
                <details>
                  <summary>Steps and decisions</summary>
                  <h4>What the agent does</h4>
                  <ol><li v-for="step in item.agentPath" :key="step">{{ step }}</li></ol>
                  <h4>What you provide or decide</h4>
                  <ul><li v-for="step in item.operatorPath" :key="step">{{ step }}</li></ul>
                </details>
                <NuxtLink :to="`/library/workflows/${item.slug}/`">Explore {{ item.name }} →</NuxtLink>
              </article>
            </div>
          </div>
        </section>
      </template>
    </CatalogIndex>
  </LibraryFrame>
</template>

<style scoped>
.workflow-previews { padding: 48px 0 56px; color: var(--ink); background: var(--paper); border-bottom: 1px solid rgb(7 10 15 / 10%); }
.workflow-previews__grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; }
.workflow-previews__grid article { min-width: 0; padding: 24px; background: #fff; border: 1px solid rgb(7 10 15 / 13%); border-radius: 16px; }
.workflow-previews h3 { margin: 0 0 14px; font-size: 23px; line-height: 1.2; letter-spacing: -.03em; }
.workflow-previews h4 { margin: 24px 0 10px; font-size: 15px; }
.workflow-previews__outcome, .workflow-previews li { color: #50535a; font-size: 14px; line-height: 1.65; }
.workflow-previews ul, .workflow-previews ol { display: grid; gap: 10px; margin: 0; padding-left: 20px; }
.workflow-previews details { margin: 24px 0; padding-top: 18px; border-top: 1px solid rgb(7 10 15 / 13%); }
.workflow-previews summary { cursor: pointer; font-size: 15px; font-weight: 700; }
.workflow-previews a { display: inline-block; margin-top: 12px; color: var(--cobalt); font-size: 14px; font-weight: 700; text-underline-offset: 3px; }
@media (max-width: 1000px) { .workflow-previews__grid { grid-template-columns: 1fr; } }
</style>
