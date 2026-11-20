<script setup lang="ts">
import { computed } from 'vue'
import type { SchemaFinanceRecordOut } from '@/api'
import { UiButton } from '@/components/ui'
import { formatMoney, object, objects, refs, text } from '@/lib/finance'
const props = defineProps<{ detail: SchemaFinanceRecordOut }>()
const emit = defineEmits<{ (e: 'inspect', recordRef: string, event: Event): void }>()
const currentAmount = computed(
  () => props.detail.amount ?? { ...object(props.detail.record.amount), exponent: null },
)
</script>

<template>
  <details class="mt-5 text-sm">
    <summary class="focus-ring cursor-pointer">
      History and source details
    </summary>
    <p class="mt-2 break-all">
      Current record: {{ detail.current_ref }}
    </p>
    <p>Recorded amount: {{ formatMoney(currentAmount) }}</p>
    <p>Recorded state: {{ text(detail.record.status) || 'Prepared/unposted' }}</p>
    <p>An available document does not confirm payment or posted books.</p>
    <p class="mt-2">
      Original date:
      {{ text(detail.original.business_date) || text(detail.original.date) || 'Undated' }}
    </p>
    <p>Sources: {{ detail.source_refs.join(', ') || 'Not supplied' }}</p>
    <ol
      v-if="detail.correction_history?.length"
      class="mt-3 space-y-3"
    >
      <li
        v-for="item in detail.correction_history"
        :key="item.correction_ref"
        data-test="correction-history-item"
        class="space-y-1 break-words rounded-md border border-default p-3"
      >
        <p>{{ item.reason }}</p>
        <p>
          {{ item.at || 'Application time unavailable' }} ·
          {{ item.actor || 'Actor unavailable' }} · {{ item.status }}
        </p>
        <p>
          <UiButton
            size="sm"
            variant="ghost"
            @click="emit('inspect', item.superseded_ref, $event)"
          >
            {{ item.superseded_ref }}
          </UiButton>
          →
          <UiButton
            size="sm"
            variant="ghost"
            @click="emit('inspect', item.replacement_ref, $event)"
          >
            {{ item.replacement_ref }}
          </UiButton>
          <span v-if="item.replacement_ref === detail.current_ref"> · Current record</span>
        </p>
        <p>Correction: {{ item.correction_ref }}</p>
        <p>Decision: {{ item.decision_ref || 'Unavailable' }}</p>
        <p>Evidence: {{ item.evidence_refs.join(', ') || 'Not supplied' }}</p>
        <p>Sources: {{ item.source_refs.join(', ') || 'Not supplied' }}</p>
      </li>
    </ol>
    <p class="mt-3 break-words">
      Lineage references: {{ refs(detail.lineage).join(' → ') || 'Not supplied' }}
    </p>
    <h4
      v-if="objects(detail.record.status_history).length"
      class="mt-3 font-medium"
    >
      Current record status history
    </h4>
    <p
      v-for="(item, index) in objects(detail.record.status_history)"
      :key="index"
    >
      {{ text(item.at) }} · {{ text(item.actor) }} · {{ text(item.to_status) }} ·
      {{ text(item.reason) }}
    </p>
  </details>
</template>
