<script setup lang="ts">
import { computed } from 'vue'
import type { SchemaFinanceProjectionOut } from '@/api'
import { UiButton, UiCallout } from '@/components/ui'
import { reportingReadiness } from './reportingReadiness'
const props = defineProps<{ projection: SchemaFinanceProjectionOut }>()
const emit = defineEmits<{ records: [] }>()
const readiness = computed(() => reportingReadiness(props.projection))
</script>
<template>
  <UiCallout v-if="readiness.needsSetup" variant="warning" aria-label="Reporting setup">
    <h2 class="t-h3">Reporting setup needed</h2>
    <p v-if="readiness.missing">
      {{ readiness.missing }} bookkeeping entries need reporting setup before they can contribute to
      income, expenses or profit. The entries remain accessible below.
    </p>
    <p v-if="readiness.policyMissing">
      Reporting policy and source coverage have not been confirmed. Missing income is not zero, and
      vendor payment evidence is not a bank reconciliation.
    </p>
    <p v-if="readiness.precision">
      {{ readiness.precision }} amounts also need their currency units verified against the
      originals.
    </p>
    <p v-if="!readiness.missing && readiness.reasons.length">
      Records need review: {{ readiness.reasons.join(', ') }}.
    </p>
    <p v-if="!readiness.policyMissing && readiness.coverageIssues.length">
      Source coverage needs review: {{ readiness.coverageIssues.join(', ') }}.
    </p>
    <p class="mt-2">
      Next: review each entry's treatment and date basis against its sources, then confirm the
      accounts and period covered by the report. These decisions are not inferred from file
      recovery.
    </p>
    <UiButton class="mt-2" size="sm" variant="secondary" @click="emit('records')">
      Inspect existing records
    </UiButton>
  </UiCallout>
</template>
