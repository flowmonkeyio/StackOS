<script setup lang="ts">
interface Stage {
  id: string
  title: string
  summary: string
}

const props = defineProps<{ stages: Stage[]; color?: string }>()
const visibleStages = computed(() => props.stages.slice(0, 10))
const mapStyle = computed(() => ({ '--workflow-color': props.color || '#7892ff' }))
</script>

<template>
  <section class="workflow-map" :style="mapStyle" aria-labelledby="workflow-map-title">
    <header>
      <span class="status-dot" />
      <strong id="workflow-map-title">{{ visibleStages.length }} ordered workflow stages</strong>
    </header>
    <ol aria-label="Workflow stages">
      <li v-for="(stage, index) in visibleStages" :key="stage.id" data-workflow-stage>
        <span>{{ String(index + 1).padStart(2, '0') }}</span>
        <div>
          <h3>{{ stage.title }}</h3>
          <p>{{ stage.summary }}</p>
        </div>
        <small>{{ index === visibleStages.length - 1 ? 'Verified outcome' : 'Passes to next stage' }}</small>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.workflow-map {
  position: relative;
  min-height: 0;
  padding: 24px;
  overflow: hidden;
  color: var(--paper);
  background:
    radial-gradient(circle at 75% 15%, color-mix(in srgb, var(--workflow-color) 14%, transparent), transparent 32%),
    #0b0e14;
  border: 1px solid rgb(255 255 255 / 10%);
  border-radius: 24px;
}

.workflow-map::before {
  position: absolute;
  inset: 0;
  pointer-events: none;
  content: '';
  opacity: 0.35;
  background-image: radial-gradient(rgb(255 255 255 / 12%) 0.7px, transparent 0.7px);
  background-size: 22px 22px;
}

.workflow-map header {
  position: relative;
  z-index: 1;
  display: inline-flex;
  align-items: center;
  gap: 9px;
  padding: 8px 11px;
  color: var(--ink-soft);
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  background: #11151e;
  border: 1px solid rgb(255 255 255 / 10%);
  border-radius: 999px;
}

.workflow-map ol {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 18px;
  margin: 24px 0 0;
  padding: 0;
  list-style: none;
}

.workflow-map li {
  position: relative;
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr);
  gap: 6px 12px;
  min-height: 210px;
  padding: 18px;
  background: #141923;
  border: 1px solid #303746;
  border-radius: 15px;
  box-shadow: 0 12px 40px rgb(0 0 0 / 24%);
}

.workflow-map li:not(:last-child)::after {
  position: absolute;
  top: 50%;
  right: -15px;
  z-index: 2;
  width: 12px;
  height: 2px;
  content: '';
  background: var(--workflow-color);
}

.workflow-map li > span {
  color: var(--workflow-color);
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.07em;
}

.workflow-map h3 {
  margin: 0;
  color: #f3f1ea;
  font-size: 21px;
  line-height: 1.2;
  letter-spacing: -0.03em;
}

.workflow-map p {
  margin: 10px 0 0;
  color: var(--ink-soft);
  font-size: 15px;
  line-height: 1.55;
}

.workflow-map small {
  grid-column: 2;
  align-self: end;
  color: var(--ink-muted);
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}

@media (max-width: 980px) {
  .workflow-map ol {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .workflow-map li:nth-child(2n)::after {
    display: none;
  }
}

@media (max-width: 640px) {
  .workflow-map {
    padding: 18px 14px;
  }

  .workflow-map ol {
    grid-template-columns: 1fr;
    gap: 10px;
  }

  .workflow-map li {
    min-height: 0;
  }

  .workflow-map li::after {
    display: none;
  }
}
</style>
