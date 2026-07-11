<script setup lang="ts">
const config = useRuntimeConfig()
const consent = useCookie<string | null>('stackos-analytics-consent', {
  maxAge: 60 * 60 * 24 * 365,
  sameSite: 'lax',
})
const consentState = useState<string | null>('stackos-analytics-consent', () => consent.value)
const ready = ref(false)

const visible = computed(() => ready.value && Boolean(config.public.gaMeasurementId) && !consentState.value)

onMounted(() => {
  ready.value = true
})

function decide(value: 'granted' | 'denied') {
  consent.value = value
  consentState.value = value
}
</script>

<template>
  <aside v-if="visible" class="analytics-consent" aria-label="Analytics preference">
    <div>
      <strong>Help us improve StackOS</strong>
      <p>Allow anonymous Google Analytics data. No advertising cookies, and nothing loads before you choose.</p>
    </div>
    <div class="analytics-consent__actions">
      <button type="button" class="analytics-consent__secondary" @click="decide('denied')">Not now</button>
      <button type="button" class="analytics-consent__primary" @click="decide('granted')">Allow analytics</button>
    </div>
  </aside>
</template>

<style scoped>
.analytics-consent {
  position: fixed;
  right: 22px;
  bottom: 22px;
  z-index: 80;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 24px;
  width: min(620px, calc(100% - 44px));
  padding: 20px;
  color: var(--paper);
  background: rgb(15 18 26 / 96%);
  border: 1px solid rgb(255 255 255 / 15%);
  border-radius: 16px;
  box-shadow: 0 28px 80px rgb(0 0 0 / 45%);
  backdrop-filter: blur(18px);
}

.analytics-consent strong {
  font-size: 15px;
}

.analytics-consent p {
  margin: 6px 0 0;
  color: var(--ink-soft);
  font-size: 13px;
  line-height: 1.55;
}

.analytics-consent__actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.analytics-consent button {
  min-height: 42px;
  padding: 0 14px;
  color: var(--paper);
  font-size: 12px;
  font-weight: 720;
  background: transparent;
  border: 1px solid rgb(255 255 255 / 15%);
  border-radius: 9px;
  cursor: pointer;
}

.analytics-consent .analytics-consent__primary {
  color: var(--ink);
  background: var(--signal);
  border-color: var(--signal);
}

@media (max-width: 640px) {
  .analytics-consent {
    right: 12px;
    bottom: 12px;
    grid-template-columns: 1fr;
    gap: 15px;
    width: calc(100% - 24px);
  }

  .analytics-consent__actions {
    justify-content: flex-end;
  }
}
</style>
