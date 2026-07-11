<script setup lang="ts">
const props = withDefaults(
  defineProps<{
    mode?: string
    color?: string
    label?: string
    compact?: boolean
  }>(),
  { mode: 'workflow', color: '#7892ff', label: 'StackOS workflow', compact: false },
)

const aiClients = [
  { name: 'Codex', src: '/images/openai.webp' },
  { name: 'Claude', src: '/images/claude.webp' },
  { name: 'Gemini', src: '/images/gemini.webp' },
]

const copy = computed(() => {
  if (props.mode === 'roles') {
    return {
      eyebrow: 'A team around the work',
      title: 'Focused agents. One complete job.',
      badge: 'Agent teamwork',
      stackos: 'Chooses the right role',
      end: ['Research', 'Create', 'Review'],
    }
  }
  if (props.mode === 'connections') {
    return {
      eyebrow: 'Keep the tools you use',
      title: 'Your AI works across your apps.',
      badge: 'Connected work',
      stackos: 'Connects the plan',
      end: ['Slack', 'Shopify', 'WordPress'],
    }
  }
  if (props.mode === 'security') {
    return {
      eyebrow: 'Your private logins stay local',
      title: 'The AI asks. StackOS checks and acts.',
      badge: 'Local control',
      stackos: 'Checks permission',
      end: ['Approved', 'Action complete'],
    }
  }
  return {
    eyebrow: 'From request to result',
    title: 'A complete plan you can follow.',
    badge: 'Visible plan',
    stackos: 'Builds the plan',
    end: ['Plan', 'Do', 'Check'],
  }
})

const modeColor = computed(() => {
  if (props.mode === 'roles') return '#7892ff'
  if (props.mode === 'connections') return '#79dfff'
  if (props.mode === 'security') return '#7ee2ad'
  return '#d9ff63'
})
const visualStyle = computed(() => ({ '--visual-color': modeColor.value }))
const visualLabel = computed(() => `${copy.value.title} ${props.label}. ${copy.value.eyebrow}.`)
</script>

<template>
  <div
    class="generated-visual"
    :class="[`generated-visual--${mode}`, { 'is-compact': compact }]"
    :style="visualStyle"
    role="img"
    :aria-label="visualLabel"
  >
    <header class="generated-visual__header">
      <div>
        <span>{{ copy.eyebrow }}</span>
        <strong>{{ copy.title }}</strong>
      </div>
      <small>{{ copy.badge }}</small>
    </header>

    <div class="generated-visual__lane" aria-hidden="true">
      <div class="visual-node visual-node--clients">
        <span class="visual-node__label">Start in your AI</span>
        <div class="client-list">
          <span v-for="client in aiClients" :key="client.name" class="client-chip">
            <img :src="client.src" alt="" width="34" height="34" loading="lazy" decoding="async">
            <b>{{ client.name }}</b>
          </span>
        </div>
      </div>

      <i class="visual-connector"><span /></i>

      <div class="visual-node visual-node--stackos">
        <img src="/images/stackos-icon.png" alt="" width="42" height="42" loading="lazy" decoding="async">
        <strong>StackOS</strong>
        <small>{{ copy.stackos }}</small>
      </div>

      <i class="visual-connector"><span /></i>

      <div class="visual-node visual-node--outcome">
        <span class="visual-node__label">
          {{ mode === 'connections' ? 'Connected apps' : mode === 'roles' ? 'Specialists' : mode === 'security' ? 'Safe result' : 'Visible stages' }}
        </span>
        <div class="outcome-list">
          <span v-for="(item, index) in copy.end" :key="item">
            <i v-if="mode === 'security'" class="result-dot" />
            <b v-else>{{ index + 1 }}</b>
            {{ item }}
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.generated-visual {
  position: relative;
  min-height: 250px;
  padding: 22px;
  overflow: hidden;
  color: var(--paper);
  background:
    radial-gradient(circle at 5% 10%, color-mix(in srgb, var(--visual-color) 20%, transparent), transparent 34%),
    #0b0e15;
  border: 1px solid rgb(255 255 255 / 11%);
  border-radius: 20px;
  box-shadow: inset 0 1px rgb(255 255 255 / 4%);
}

.generated-visual::after {
  position: absolute;
  inset: 0;
  pointer-events: none;
  content: '';
  background-image: radial-gradient(rgb(255 255 255 / 12%) 0.7px, transparent 0.7px);
  background-size: 18px 18px;
  opacity: 0.18;
  mask-image: linear-gradient(to bottom, black, transparent 78%);
}

.generated-visual__header,
.generated-visual__lane {
  position: relative;
  z-index: 1;
}

.generated-visual__header {
  display: flex;
  gap: 18px;
  align-items: start;
  justify-content: space-between;
  margin-bottom: 22px;
}

.generated-visual__header div > span,
.generated-visual__header strong {
  display: block;
}

.generated-visual__header div > span {
  color: color-mix(in srgb, var(--visual-color) 72%, white);
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 650;
  letter-spacing: 0.09em;
  text-transform: uppercase;
}

.generated-visual__header strong {
  margin-top: 6px;
  color: var(--paper);
  font-size: clamp(18px, 2vw, 24px);
  line-height: 1.2;
  letter-spacing: -0.025em;
}

.generated-visual__header > small {
  max-width: 150px;
  padding: 5px 8px;
  overflow: hidden;
  color: #aeb5c5;
  font-family: var(--font-mono);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
  background: rgb(255 255 255 / 4%);
  border: 1px solid rgb(255 255 255 / 8%);
  border-radius: 999px;
}

.generated-visual__lane {
  display: grid;
  grid-template-columns: minmax(0, 1.08fr) minmax(18px, 0.2fr) minmax(86px, 0.68fr) minmax(18px, 0.2fr) minmax(0, 0.9fr);
  align-items: stretch;
}

.visual-node {
  min-width: 0;
  min-height: 112px;
  padding: 13px;
  background: rgb(19 24 36 / 92%);
  border: 1px solid rgb(255 255 255 / 11%);
  border-radius: 13px;
}

.visual-node__label {
  display: block;
  margin-bottom: 10px;
  color: #98a1b3;
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.client-list {
  display: flex;
  gap: 6px;
}

.client-chip {
  display: grid;
  min-width: 0;
  flex: 1;
  place-items: center;
  gap: 5px;
}

.client-chip img {
  width: 34px;
  height: 34px;
  object-fit: cover;
  background: #171b25;
  border: 1px solid rgb(255 255 255 / 13%);
  border-radius: 9px;
}

.client-chip b {
  max-width: 100%;
  overflow: hidden;
  color: #dfe3eb;
  font-size: 11px;
  font-weight: 650;
  text-overflow: ellipsis;
}

.visual-node--stackos {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  border-color: color-mix(in srgb, var(--visual-color) 50%, transparent);
  box-shadow: 0 0 32px color-mix(in srgb, var(--visual-color) 10%, transparent);
}

.visual-node--stackos img {
  width: 42px;
  height: 42px;
  object-fit: cover;
  border-radius: 11px;
}

.visual-node--stackos strong {
  margin-top: 7px;
  color: var(--paper);
  font-size: 14px;
}

.visual-node--stackos small {
  margin-top: 3px;
  color: #9ca5b6;
  font-size: 11px;
  line-height: 1.3;
}

.visual-connector {
  position: relative;
  align-self: center;
  height: 1px;
  overflow: hidden;
  background: rgb(255 255 255 / 13%);
}

.visual-connector span {
  position: absolute;
  top: -1px;
  left: 0;
  width: 40%;
  height: 3px;
  background: linear-gradient(90deg, transparent, var(--visual-color), transparent);
  transform: translateX(-100%);
  animation: visual-signal 2.2s linear infinite;
}

.outcome-list {
  display: grid;
  gap: 6px;
}

.outcome-list > span {
  display: flex;
  min-width: 0;
  gap: 6px;
  align-items: center;
  padding: 7px 8px;
  overflow: hidden;
  color: #e2e5ec;
  font-size: 12px;
  font-weight: 620;
  text-overflow: ellipsis;
  white-space: nowrap;
  background: rgb(255 255 255 / 4%);
  border: 1px solid rgb(255 255 255 / 7%);
  border-radius: 7px;
}

.outcome-list b {
  display: grid;
  width: 15px;
  height: 15px;
  flex: 0 0 auto;
  place-items: center;
  color: #0b0e15;
  font-family: var(--font-mono);
  font-size: 9px;
  background: var(--visual-color);
  border-radius: 5px;
}

.result-dot {
  width: 7px;
  height: 7px;
  flex: 0 0 auto;
  background: #7ee2ad;
  border-radius: 50%;
  box-shadow: 0 0 10px rgb(126 226 173 / 70%);
}

.generated-visual.is-compact {
  min-height: 204px;
  padding: 14px;
  border-radius: 14px;
}

.is-compact .generated-visual__header {
  margin-bottom: 13px;
}

.is-compact .generated-visual__header strong {
  margin-top: 4px;
  font-size: 17px;
}

.is-compact .generated-visual__header div > span { font-size: 11px; }
.is-compact .generated-visual__header > small { max-width: 118px; font-size: 11px; }

.is-compact .generated-visual__lane {
  grid-template-columns: minmax(0, 1fr) 20px minmax(64px, 0.68fr) 20px minmax(0, 0.92fr);
}

.is-compact .visual-node {
  min-height: 112px;
  padding: 9px;
}

.is-compact .visual-node__label { margin-bottom: 7px; font-size: 11px; }

.is-compact .client-chip img {
  width: 27px;
  height: 27px;
  border-radius: 7px;
}

.is-compact .visual-node--stackos img {
  width: 33px;
  height: 33px;
}

.is-compact .visual-node--stackos strong {
  font-size: 12px;
}

.is-compact .visual-node--stackos small { font-size: 11px; }
.is-compact .client-chip b { font-size: 11px; }

.is-compact .outcome-list {
  gap: 4px;
}

.is-compact .outcome-list > span {
  padding: 5px;
  font-size: 11px;
}

@keyframes visual-signal {
  to { transform: translateX(250%); }
}

@media (max-width: 520px) {
  .generated-visual {
    min-height: 226px;
    padding: 15px;
  }

  .generated-visual__header {
    margin-bottom: 14px;
  }

  .generated-visual__header strong {
    font-size: 17px;
  }

  .generated-visual__header > small {
    display: none;
  }

  .generated-visual__lane {
    grid-template-columns: minmax(0, 1fr) 13px minmax(62px, 0.65fr) 13px minmax(0, 0.9fr);
  }

  .visual-node {
    min-height: 96px;
    padding: 8px;
  }

  .client-list {
    height: 100%;
    align-items: center;
  }

  .client-chip img {
    width: 27px;
    height: 27px;
  }

  .visual-node--stackos img {
    width: 34px;
    height: 34px;
  }

  .outcome-list > span {
    padding: 6px 5px;
    font-size: 10px;
  }

  .outcome-list b { width: 15px; height: 15px; font-size: 8px; }
}

@media (prefers-reduced-motion: reduce) {
  .visual-connector span { animation: none; transform: translateX(85%); }
}
</style>
