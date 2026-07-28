<script setup lang="ts">
const props = defineProps<{ provider: string; providerSlug: string; color: string }>()
const steps = computed(() => [
  { id: 'request', title: 'Your request', note: 'Start in your AI' },
  { id: 'plan', title: 'StackOS plan', note: 'Expands the steps' },
  { id: 'provider', title: props.provider, note: 'Does its bounded part' },
  { id: 'result', title: 'Checked result', note: 'Keeps the proof' },
])
</script>

<template>
  <ol
    class="integration-route-flow"
    :style="{ '--route-color': color }"
    :data-integration-route="providerSlug"
    :aria-label="`How StackOS routes approved work to ${provider}`"
  >
    <li v-for="(step, index) in steps" :key="step.id">
      <span>{{ String(index + 1).padStart(2, '0') }}</span>
      <strong>{{ step.title }}</strong>
      <small>{{ step.note }}</small>
    </li>
  </ol>
</template>

<style scoped>
.integration-route-flow {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 22px;
  width: 100%;
  margin: 38px 0 0;
  padding: 28px;
  background: #f4f2eb;
  border: 1px solid var(--paper-border);
  border-radius: 15px;
  list-style: none;
}

.integration-route-flow li {
  position: relative;
  display: grid;
  min-height: 116px;
  align-content: center;
  padding: 16px 18px;
  color: var(--ink);
  background: #fff;
  border: 1px solid #d5d2c7;
  border-radius: 12px;
  box-shadow: 0 9px 28px rgb(29 32 38 / 7%);
}

.integration-route-flow li:not(:last-child)::after {
  position: absolute;
  top: calc(50% - 1px);
  right: -23px;
  width: 22px;
  height: 2px;
  content: '';
  background: var(--route-color);
}

.integration-route-flow li > span {
  color: var(--cobalt);
  font-family: var(--font-mono);
  font-size: 10px;
}

.integration-route-flow strong {
  margin-top: 9px;
  overflow: hidden;
  font-size: 16px;
  line-height: 1.15;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.integration-route-flow small {
  margin-top: 5px;
  color: #666760;
  font-size: 12px;
}

@media (max-width: 760px) {
  .integration-route-flow {
    grid-template-columns: 1fr;
    gap: 12px;
    padding: 18px;
  }

  .integration-route-flow li {
    min-height: 104px;
  }

  .integration-route-flow li::after {
    display: none;
  }
}
</style>
