<script setup lang="ts">
const domains = [
  { key: 'engineering', label: 'Engineering', sample: 'Product delivery', glyph: 'EN' },
  { key: 'content', label: 'Content', sample: 'Research to publishing', glyph: 'CT' },
  { key: 'finance', label: 'Finance', sample: 'Month-end review', glyph: 'FN' },
  { key: 'shopify', label: 'Shopify', sample: 'Collection launches', glyph: 'SH' },
  { key: 'support', label: 'Customer Support', sample: 'Issue to resolution', glyph: 'CS' },
  { key: 'marketing', label: 'Marketing', sample: 'Campaign production', glyph: 'MK' },
  { key: 'sales', label: 'Sales & RevOps', sample: 'Pipeline follow-up', glyph: 'SL' },
  { key: 'seo', label: 'SEO', sample: 'Opportunity to article', glyph: 'SE' },
  { key: 'operations', label: 'Operations', sample: 'Weekly action review', glyph: 'OP' },
]

const carouselItems = [
  'Launch a Shopify collection',
  'Close the month',
  'Ship a product update',
  'Build a content campaign',
  'Review pipeline risk',
  'Resolve customer feedback',
  'Publish an SEO article',
  'Run a weekly operations review',
]
</script>

<template>
  <section class="domains section section--ink" aria-labelledby="domains-title">
    <div class="shell">
      <div v-reveal class="section-heading section-heading--wide">
        <div>
          <p class="eyebrow eyebrow--dark">Not just for software teams</p>
          <h2 id="domains-title">Any team. Any process.<br /><em>The same clear rhythm.</em></h2>
        </div>
        <p>
          StackOS does not replace the software your team already uses. It organizes the work
          that moves between people, AI tools, and business apps—from the first request to the final result.
        </p>
      </div>

      <div class="domain-grid">
        <article
          v-for="(domain, index) in domains"
          :key="domain.key"
          v-reveal="(index % 3) * 50"
          class="domain-card"
        >
          <div class="domain-card__glyph">{{ domain.glyph }}</div>
          <div><strong>{{ domain.label }}</strong><span>{{ domain.sample }}</span></div>
          <code>→</code>
        </article>
      </div>

      <div v-reveal="130" class="connect-story">
        <div class="connect-story__copy">
          <span class="eyebrow eyebrow--dark">Built around your real work</span>
          <h3>Keep the tools.<br />Connect the process.</h3>
          <p>
            Your AI assistant still thinks and acts. Your business apps still hold the work.
            StackOS gives the whole job a shared plan, visible progress, and a memory that survives the chat.
          </p>
        </div>
        <div class="connect-path" aria-label="How StackOS connects existing tools">
          <div>
            <span>1</span>
            <small>Talk where you already work</small>
            <strong>Codex · Claude Code · Gemini</strong>
          </div>
          <i aria-hidden="true">↓</i>
          <div>
            <span>2</span>
            <small>StackOS organizes the job</small>
            <strong>Your request · saved workflow · human review</strong>
          </div>
          <i aria-hidden="true">↓</i>
          <div>
            <span>3</span>
            <small>Work moves through your apps</small>
            <strong>Shopify · Slack · WordPress · your own tools</strong>
          </div>
        </div>
      </div>
    </div>

    <div class="workflow-marquee" aria-hidden="true">
      <div class="workflow-marquee__track">
        <div v-for="copy in 2" :key="copy" class="workflow-marquee__group">
          <template v-for="item in carouselItems" :key="`${copy}-${item}`">
            <span>{{ item }}</span><i>✦</i>
          </template>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.domains {
  padding-bottom: 0;
  overflow: hidden;
}

.domain-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
  margin-top: 62px;
}

.domain-card {
  display: grid;
  grid-template-columns: 42px 1fr auto;
  gap: 13px;
  align-items: center;
  min-width: 0;
  min-height: 78px;
  padding: 14px;
  background: rgb(255 255 255 / 3%);
  border: 1px solid var(--border-strong);
  border-radius: 13px;
  transition: background 180ms ease, border-color 180ms ease, transform 180ms ease;
}

.domain-card:hover {
  background: rgb(255 255 255 / 5%);
  border-color: rgb(121 223 255 / 24%);
  transform: translateY(-2px);
}

.domain-card__glyph {
  display: grid;
  width: 42px;
  height: 42px;
  place-items: center;
  color: var(--paper);
  font-family: var(--font-mono);
  font-size: 10px;
  background: #1a202d;
  border: 1px solid rgb(255 255 255 / 8%);
  border-radius: 11px;
}

.domain-card strong,
.domain-card span {
  display: block;
}

.domain-card strong {
  margin-bottom: 4px;
  font-size: 12px;
}

.domain-card span {
  color: var(--ink-muted);
  font-size: 10px;
}

.domain-card code {
  color: #8790a0;
  font-size: 13px;
}

.connect-story {
  display: grid;
  grid-template-columns: 0.8fr 1.2fr;
  gap: 70px;
  align-items: center;
  margin: 90px 0 120px;
  padding: 46px;
  background: var(--surface-raised);
  border: 1px solid var(--border-strong);
  border-radius: 22px;
}

.connect-story h3 {
  margin: 14px 0;
  font-size: clamp(34px, 3.6vw, 52px);
  line-height: 0.98;
  letter-spacing: -0.06em;
}

.connect-story p {
  margin: 0;
  color: var(--ink-soft);
  font-size: 13px;
  line-height: 1.75;
}

.connect-path {
  display: grid;
  padding: 18px;
  background: #090b10;
  border: 1px solid rgb(255 255 255 / 8%);
  border-radius: 17px;
  box-shadow: 0 30px 70px rgb(0 0 0 / 28%);
}

.connect-path > div {
  display: grid;
  grid-template-columns: 34px 1fr;
  gap: 5px 12px;
  align-items: center;
  padding: 16px;
  background: rgb(255 255 255 / 3%);
  border: 1px solid rgb(255 255 255 / 7%);
  border-radius: 12px;
}

.connect-path > div > span {
  display: grid;
  grid-row: 1 / 3;
  width: 34px;
  height: 34px;
  place-items: center;
  color: var(--ink);
  font-family: var(--font-mono);
  font-size: 9px;
  background: var(--signal);
  border-radius: 9px;
}

.connect-path small {
  color: var(--ink-muted);
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.connect-path strong {
  font-size: 12px;
}

.connect-path > i {
  height: 24px;
  padding-left: 32px;
  color: var(--cobalt-soft);
  font-style: normal;
  line-height: 24px;
}

.workflow-marquee {
  width: 100%;
  padding: 19px 0;
  overflow: hidden;
  color: var(--ink);
  background: var(--signal);
  border-top: 1px solid #c8ed52;
  border-bottom: 1px solid #c8ed52;
}

.workflow-marquee__track,
.workflow-marquee__group {
  display: flex;
  width: max-content;
  align-items: center;
}

.workflow-marquee__track {
  will-change: transform;
}

.workflow-marquee__group {
  flex: 0 0 auto;
  gap: 24px;
  padding-right: 24px;
  font-size: 16px;
  font-weight: 760;
  letter-spacing: -0.03em;
  white-space: nowrap;
}

.workflow-marquee i {
  font-size: 9px;
  font-style: normal;
}

@media (prefers-reduced-motion: no-preference) {
  .workflow-marquee__track {
    animation: seamless-marquee 42s linear infinite;
  }

  @keyframes seamless-marquee {
    to { transform: translate3d(-50%, 0, 0); }
  }
}

@media (max-width: 880px) {
  .domain-grid { grid-template-columns: repeat(2, 1fr); }
  .connect-story { grid-template-columns: 1fr; gap: 35px; }
}

@media (max-width: 560px) {
  .domain-grid { grid-template-columns: 1fr; }
  .connect-story { padding: 26px 20px; }
}
</style>
