<script setup lang="ts">
const stages = [
  {
    number: '01',
    label: 'Ask',
    title: 'Tell your AI tool what you want.',
    body: 'Stay in Codex, Claude Code, Gemini, or the assistant you already use.',
    code: '“Launch our fall collection”',
  },
  {
    number: '02',
    label: 'Build',
    title: 'StackOS turns the request into a plan.',
    body: 'Your request and chosen workflow become clear steps, owners, checks, and approvals.',
    code: '7 steps prepared',
  },
  {
    number: '03',
    label: 'Review',
    title: 'You see the whole job before it starts.',
    body: 'Change the order, add context, or approve the plan when it looks right.',
    code: 'ready for approval',
  },
  {
    number: '04',
    label: 'Work',
    title: 'Each step moves in the right tool.',
    body: 'Your AI works across the apps you connected while StackOS keeps the sequence visible.',
    code: 'step 4 of 7',
  },
  {
    number: '05',
    label: 'Finish',
    title: 'The result and its story stay together.',
    body: 'See what finished, what changed, what needs attention, and where every result lives.',
    code: 'work complete',
  },
]
</script>

<template>
  <section class="execution section section--paper" aria-labelledby="execution-title">
    <div class="shell">
      <div v-reveal class="section-heading section-heading--wide">
        <div>
          <p class="eyebrow">A complete journey, not a one-off prompt</p>
          <h2 id="execution-title">Say what you want.<br /><em>See the plan. Press start.</em></h2>
        </div>
        <p>
          You stay in the AI tool you know. StackOS adds the shared plan, progress, and memory
          that real work needs.
        </p>
      </div>

      <div class="execution-track" aria-label="StackOS execution lifecycle">
        <article v-for="(stage, index) in stages" :key="stage.number" v-reveal="index * 55">
          <div class="execution-track__topline">
            <span>{{ stage.number }}</span>
            <i v-if="index < stages.length - 1" aria-hidden="true" />
          </div>
          <small>{{ stage.label }}</small>
          <h3>{{ stage.title }}</h3>
          <p>{{ stage.body }}</p>
          <code>{{ stage.code }}</code>
        </article>
      </div>

      <div v-reveal="140" class="execution-receipt">
        <div class="execution-receipt__label">
          <span class="status-dot" />
          Live work summary
        </div>
        <div class="execution-receipt__row">
          <span>AI tool</span><strong>Claude Code</strong><code>connected</code>
        </div>
        <div class="execution-receipt__row">
          <span>Current step</span><strong>Create the Shopify collection</strong><code>working</code>
        </div>
        <div class="execution-receipt__row">
          <span>Next</span><strong>Check inventory and pricing</strong><code>waiting</code>
        </div>
        <div class="execution-receipt__row">
          <span>Progress</span><strong>4 of 7 steps finished</strong><code>on track</code>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.execution-track {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  margin-top: 66px;
  border-top: 1px solid var(--paper-border);
  border-bottom: 1px solid var(--paper-border);
}

.execution-track article {
  min-width: 0;
  padding: 25px 20px 28px;
  border-left: 1px solid var(--paper-border);
}

.execution-track article:last-child {
  border-right: 1px solid var(--paper-border);
}

.execution-track__topline {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--cobalt);
  font-family: var(--font-mono);
  font-size: 10px;
}

.execution-track__topline i {
  flex: 1;
  height: 1px;
  background: repeating-linear-gradient(90deg, #bbb8ae 0 4px, transparent 4px 8px);
}

.execution-track small {
  display: block;
  margin: 28px 0 11px;
  color: var(--muted-on-paper);
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.execution-track h3 {
  min-height: 70px;
  margin: 0;
  font-size: 19px;
  line-height: 1.18;
  letter-spacing: -0.035em;
}

.execution-track p {
  min-height: 86px;
  margin: 14px 0 16px;
  color: var(--muted-on-paper);
  font-size: 12px;
  line-height: 1.65;
}

.execution-track code {
  color: #5c6473;
  font-size: 10px;
}

.execution-receipt {
  display: grid;
  grid-template-columns: 185px repeat(4, 1fr);
  margin-top: 36px;
  overflow: hidden;
  color: var(--paper);
  background: var(--ink);
  border: 1px solid #1c222e;
  border-radius: 16px;
}

.execution-receipt__label,
.execution-receipt__row {
  padding: 18px;
  border-right: 1px solid rgb(255 255 255 / 8%);
}

.execution-receipt__label {
  display: flex;
  align-items: center;
  gap: 9px;
  font-family: var(--font-mono);
  font-size: 10px;
  text-transform: uppercase;
}

.execution-receipt__row span,
.execution-receipt__row strong,
.execution-receipt__row code {
  display: block;
}

.execution-receipt__row span {
  color: var(--ink-muted);
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.execution-receipt__row strong {
  margin: 7px 0 9px;
  overflow: hidden;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.execution-receipt__row code {
  color: var(--signal);
  font-size: 9px;
}

@media (max-width: 960px) {
  .execution-track {
    grid-template-columns: repeat(2, 1fr);
  }

  .execution-track article:nth-child(odd) {
    border-left: 1px solid var(--paper-border);
  }

  .execution-track h3,
  .execution-track p {
    min-height: 0;
  }

  .execution-receipt {
    grid-template-columns: repeat(2, 1fr);
  }

  .execution-receipt__label {
    grid-column: 1 / -1;
    border-bottom: 1px solid rgb(255 255 255 / 8%);
  }
}

@media (max-width: 580px) {
  .execution-track,
  .execution-receipt {
    grid-template-columns: 1fr;
  }

  .execution-track article,
  .execution-track article:last-child {
    border-right: 1px solid var(--paper-border);
  }
}
</style>
