<script setup lang="ts">
import type { SchemaFinancePreviewOut } from '@/api'
import {
  UiButton,
  UiDescriptionList,
  UiFormField,
  UiInput,
  UiSelect,
  UiTextarea,
} from '@/components/ui'
import { formatMoney, object, text } from '@/lib/finance'
import type { FinanceRecordDraft } from './useFinanceRecordEditor'

defineProps<{
  recordRef: string | null
  allowed: string[]
  authorityCurrent: boolean
  canEdit: boolean
  saving: boolean
  uncertain: boolean
  previewing: boolean
  stale: boolean
  preview: SchemaFinancePreviewOut | null
}>()
const draft = defineModel<FinanceRecordDraft>({ required: true })
const emit = defineEmits<{
  (e: 'change' | 'preview' | 'save' | 'cancel' | 'refresh'): void
}>()
</script>

<template>
  <form
    class="mt-5 space-y-4"
    @submit.prevent="emit('preview')"
  >
    <h3 class="t-h3">
      {{ recordRef ? 'Edit transaction' : 'Transaction details' }}
    </h3>
    <fieldset
      :disabled="saving || uncertain"
      class="space-y-4"
    >
      <div class="grid grid-cols-2 gap-4">
        <UiFormField
          v-if="allowed.includes('date') || allowed.includes('business_date')"
          label="Date"
          required
        >
          <UiInput
            v-model="draft.date"
            data-test="date"
            type="date"
            min="0001-01-01"
            max="9999-12-31"
            @update:model-value="emit('change')"
          />
        </UiFormField>
        <UiFormField
          v-if="allowed.includes('merchant')"
          label="Vendor"
        >
          <UiInput
            v-model="draft.merchant"
            @update:model-value="emit('change')"
          />
        </UiFormField>
        <UiFormField
          v-if="allowed.includes('amount')"
          label="Amount"
          :help="
            recordRef === null
              ? 'Enter the amount in the selected currency.'
              : 'Leave unchanged to keep the recorded amount.'
          "
        >
          <UiInput
            v-model="draft.amount"
            inputmode="decimal"
            data-test="amount"
            @update:model-value="emit('change')"
          />
        </UiFormField>
        <UiFormField
          v-if="allowed.includes('amount')"
          label="Currency"
        >
          <UiInput
            v-model="draft.currency"
            maxlength="3"
            @update:model-value="emit('change')"
          />
        </UiFormField>
      </div>
      <details class="space-y-3">
        <summary class="focus-ring cursor-pointer text-sm">
          Additional fields
        </summary>
        <div class="mt-3 grid grid-cols-2 gap-4">
          <UiFormField
            v-if="allowed.includes('amount')"
            label="Currency decimal places"
            :help="
              draft.precision
                ? 'Explicit precision for this entry; currencies remain separate.'
                : 'Source precision unavailable. Confirm it to change the amount.'
            "
          >
            <UiInput
              v-model="draft.precision"
              inputmode="numeric"
              maxlength="1"
              @update:model-value="emit('change')"
            />
          </UiFormField>
          <UiFormField
            v-if="allowed.includes('category')"
            label="Category"
            help="Use an existing category. Leave blank if unknown."
          >
            <UiInput
              v-model="draft.category"
              @update:model-value="emit('change')"
            />
          </UiFormField>
          <UiFormField
            v-if="allowed.includes('account_ref')"
            label="Account"
          >
            <UiInput
              v-model="draft.account"
              @update:model-value="emit('change')"
            />
          </UiFormField>
          <UiFormField
            v-if="allowed.includes('external_project_ref')"
            label="Project"
            help="Leave shared or company-wide costs unassigned."
          >
            <UiInput
              v-model="draft.project"
              @update:model-value="emit('change')"
            />
          </UiFormField>
        </div>
      </details>
      <UiFormField
        v-if="allowed.includes('business_purpose')"
        label="Business purpose"
      >
        <UiTextarea
          v-model="draft.purpose"
          @update:model-value="emit('change')"
        />
      </UiFormField>
      <details class="space-y-3">
        <summary class="focus-ring cursor-pointer text-sm">
          Supporting sources
        </summary>
        <UiFormField
          class="mt-3"
          label="Source references"
          help="Existing supporting evidence, one reference per line."
        >
          <UiTextarea
            v-model="draft.sources"
            @update:model-value="emit('change')"
          />
        </UiFormField>
      </details>
      <UiFormField
        v-if="recordRef === null"
        label="Supporting note"
        help="Explain how you know this amount if there is no supporting document."
      >
        <UiTextarea
          v-model="draft.attestation"
          @update:model-value="emit('change')"
        />
      </UiFormField>
      <details
        v-if="allowed.includes('reporting')"
        class="space-y-3"
      >
        <summary class="focus-ring cursor-pointer text-sm">
          Reporting options
        </summary>
        <UiFormField
          label="Treatment"
          help="Blank leaves the entry outside recognized totals. Sign alone does not establish treatment."
        >
          <UiSelect
            v-model="draft.treatment"
            :options="[
              { value: '', label: 'Unclassified' },
              { value: 'expense', label: 'Expense' },
              { value: 'revenue', label: 'Income' },
              { value: 'fee', label: 'Fee' },
              { value: 'expense_refund', label: 'Expense refund' },
              { value: 'revenue_refund', label: 'Income refund' },
              { value: 'transfer', label: 'Transfer' },
              { value: 'reserve', label: 'Reserve' },
              { value: 'tax_payment', label: 'Tax payment' },
              { value: 'owner_movement', label: 'Owner movement' },
            ]"
            @update:model-value="emit('change')"
          />
        </UiFormField>
        <UiFormField
          label="Transaction identity"
          help="Reuse an existing economic event reference only for the same transaction."
        >
          <UiInput
            v-model="draft.event"
            @update:model-value="emit('change')"
          />
        </UiFormField>
        <UiFormField
          label="Component identity"
          help="Distinguish principal, fees and refunds without counting a representation twice."
        >
          <UiInput
            v-model="draft.component"
            @update:model-value="emit('change')"
          />
        </UiFormField>
      </details>
      <UiFormField
        label="Reason"
        required
      >
        <UiTextarea
          v-model="draft.reason"
          data-test="reason"
          @update:model-value="emit('change')"
        />
      </UiFormField>
    </fieldset>
    <section
      v-if="preview"
      class="space-y-2 rounded-md border border-default p-3"
      aria-label="Change preview"
    >
      <h3 class="t-h3">
        Review before saving
      </h3>
      <UiDescriptionList
        :items="
          Object.entries(object(preview.diff.fields)).map(([key, value]) => ({
            label: key.replaceAll('_', ' '),
            value:
              key === 'amount'
                ? formatMoney({
                  ...object(value),
                  exponent: /^\d$/.test(draft.precision) ? Number(draft.precision) : null,
                })
                : typeof value === 'string'
                  ? value
                  : key === 'reporting'
                    ? text(object(value).treatment)
                    : text(object(value).category_ref) || text(object(value).state),
          }))
        "
      />
    </section>
    <div class="flex flex-wrap gap-2">
      <UiButton
        data-test="cancel-edit"
        :disabled="saving || uncertain"
        @click="emit('cancel')"
      >
        Cancel
      </UiButton>
      <UiButton
        v-if="!preview"
        data-test="preview-edit"
        type="submit"
        :loading="previewing"
        :disabled="authorityCurrent === false || !canEdit || saving || stale"
        variant="primary"
      >
        Preview changes
      </UiButton>
      <UiButton
        v-else
        data-test="save-edit"
        :loading="saving"
        :disabled="authorityCurrent === false || !canEdit || stale"
        variant="primary"
        @click="emit('save')"
      >
        {{ uncertain ? 'Recheck same save' : 'Save reviewed changes' }}
      </UiButton>
      <UiButton
        v-if="stale"
        @click="emit('refresh')"
      >
        Refresh current source
      </UiButton>
    </div>
  </form>
</template>
