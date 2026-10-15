<script setup lang="ts">
import { UiButton, UiSectionHeader } from '@/components/ui'
import type { InsightsModel } from './insightsViewModel'

defineProps<{
  model: InsightsModel
  status: string
  hasRequest: boolean
  requestAllowed: boolean
  refreshAllowed: boolean
  loading?: boolean
}>()
const emit = defineEmits<{ request: []; refresh: []; inspect: [recordRef: string] }>()
</script>
<template>
  <section
    class="min-w-0 space-y-4 border-t border-subtle pt-4"
    aria-label="AI categories and concerns"
  >
    <UiSectionHeader
      title="AI categories and concerns"
      description="Reviewed suggestions from your finance agent. Proposed groups do not change source categories or financial totals."
    >
      <template #actions>
        <UiButton :disabled="!requestAllowed" @click="emit('request')">{{
          hasRequest
            ? 'Request new category and concern review'
            : 'Request category and concern review'
        }}</UiButton>
        <UiButton
          v-if="hasRequest"
          :disabled="!refreshAllowed"
          :loading="loading"
          @click="emit('refresh')"
          >Refresh reviewed suggestions</UiButton
        >
      </template>
    </UiSectionHeader>
    <p role="status" class="text-sm">{{ status }}</p>
    <p v-if="!model.hasResult" class="text-sm text-fg-muted">
      No reviewed suggestions yet. Request a review, then refresh its status.
    </p>
    <template v-else>
      <p class="text-xs text-fg-muted">
        Original review: {{ model.window }} · Source revision {{ model.sourceRevision }}. Review
        scope stays attached to this original selection.
      </p>
      <p v-if="model.historical" role="status" class="text-sm text-fg-muted">
        {{
          model.historicalSource
            ? 'Historical review: the source changed.'
            : 'This review covers a different selection.'
        }}
        The suggestions below describe the original review and do not bucket current records.
        Request a new review for the selected source and filters.
      </p>
      <p v-if="model.partial" class="text-sm text-fg-muted">
        Partial review: source or considered-record gaps remain.
      </p>
      <ul
        v-if="model.gaps.length"
        class="space-y-1 text-sm text-fg-muted"
        aria-label="AI review gaps"
      >
        <li v-for="(gap, index) in model.gaps" :key="index">{{ gap }}</li>
      </ul>
      <section class="space-y-3" aria-label="Proposed AI categories">
        <h3 class="t-h3">Proposed descriptive categories</h3>
        <p v-if="!model.supplied" class="text-sm text-fg-muted">
          This legacy review did not supply category proposals or considered-record coverage. It
          does not establish that items are uncategorized.
        </p>
        <template v-else>
          <p class="text-sm text-fg-muted">
            {{ model.considered.length }} records considered ·
            {{ model.proposals.filter((proposal) => proposal.categoryRef).length }} proposed
            assignments · {{ model.unassignedItems.length }} unassigned. Records outside this
            considered set have no AI category from this review.
          </p>
          <p v-if="model.emptyCategories" class="text-sm text-fg-muted">
            Reviewed empty category output: no proposed groups or assignments were supplied. This is
            not a conclusion about unconsidered records.
          </p>
          <section
            v-for="group in model.groups"
            :key="group.categoryRef"
            class="space-y-2 border-t border-subtle pt-3"
            :aria-label="`Proposed category ${group.label}`"
          >
            <h4 class="font-medium">
              {{ group.label }} · proposed · {{ group.items.length }} item(s)
            </h4>
            <p class="text-sm">{{ group.description }}</p>
            <p class="text-sm">{{ group.rationale }}</p>
            <p class="text-xs text-fg-muted">Uncertainty: {{ group.uncertainty }}</p>
            <div class="flex flex-wrap gap-2">
              <UiButton
                v-for="refValue in group.evidenceRefs"
                :key="refValue"
                size="sm"
                variant="ghost"
                @click="emit('inspect', refValue)"
                >Inspect {{ refValue }}</UiButton
              >
            </div>
            <p v-if="!group.items.length" class="text-xs text-fg-muted">
              No considered item was assigned to this proposed group.
            </p>
            <ul class="space-y-3">
              <li v-for="item in group.items" :key="item.recordRef" class="space-y-1">
                <p class="text-sm font-medium">{{ item.label }}</p>
                <p class="text-xs text-fg-muted">
                  Proposed assignment · Confidence: {{ item.confidence }}
                </p>
                <p class="text-sm">{{ item.rationale }}</p>
                <p class="text-xs text-fg-muted">Uncertainty: {{ item.uncertainty }}</p>
                <UiButton
                  size="sm"
                  :aria-label="`Inspect ${item.label}`"
                  @click="emit('inspect', item.recordRef)"
                  >Inspect item</UiButton
                >
                <details class="text-xs text-fg-muted">
                  <summary class="focus-ring cursor-pointer">Assignment references</summary>
                  <p class="break-all">Record: {{ item.recordRef }}</p>
                  <p class="break-all">Proposed category: {{ item.categoryRef }}</p>
                  <div class="flex flex-wrap gap-2">
                    <UiButton
                      v-for="refValue in item.evidenceRefs"
                      :key="refValue"
                      size="sm"
                      variant="ghost"
                      @click="emit('inspect', refValue)"
                      >Inspect {{ refValue }}</UiButton
                    >
                  </div>
                </details>
              </li>
            </ul>
          </section>
          <section
            v-if="model.unassignedItems.length"
            class="space-y-2"
            aria-label="AI unassigned items"
          >
            <h4 class="font-medium">Explicitly unassigned by this review</h4>
            <ul class="space-y-3">
              <li v-for="item in model.unassignedItems" :key="item.recordRef" class="space-y-1">
                <p class="text-sm">{{ item.label }}</p>
                <p v-if="item.proposal" class="text-xs text-fg-muted">
                  Confidence: {{ item.proposal.confidence }} · {{ item.proposal.rationale }} ·
                  Uncertainty: {{ item.proposal.uncertainty }}
                </p>
                <p v-else class="text-xs text-fg-muted">
                  No category was assigned; item-specific confidence and rationale were not
                  supplied.
                </p>
                <UiButton
                  size="sm"
                  :aria-label="`Inspect unassigned ${item.label}`"
                  @click="emit('inspect', item.recordRef)"
                  >Inspect unassigned item</UiButton
                >
                <details class="text-xs text-fg-muted">
                  <summary class="focus-ring cursor-pointer">Unassigned references</summary>
                  <p class="break-all">Record: {{ item.recordRef }}</p>
                  <div v-if="item.proposal" class="flex flex-wrap gap-2">
                    <UiButton
                      v-for="refValue in item.proposal.evidenceRefs"
                      :key="refValue"
                      size="sm"
                      variant="ghost"
                      @click="emit('inspect', refValue)"
                      >Inspect {{ refValue }}</UiButton
                    >
                  </div>
                </details>
              </li>
            </ul>
          </section>
        </template>
      </section>
      <section class="space-y-3" aria-label="Reviewed concern highlights">
        <h3 class="t-h3">Reviewed concerns and opportunities</h3>
        <p v-if="!model.opportunities.length" class="text-sm text-fg-muted">
          This review supplied no concern findings. Remaining data gaps and unconsidered records
          still apply.
        </p>
        <article
          v-for="finding in model.opportunities"
          :key="finding.ref"
          class="space-y-2 border-t border-subtle pt-3"
        >
          <h4 class="font-medium">{{ finding.title }}</h4>
          <p class="text-xs text-fg-muted">
            {{ finding.kind }} ·
            {{
              finding.attention ? `Attention: ${finding.attention}` : 'Attention level not supplied'
            }}
          </p>
          <p class="text-sm">{{ finding.rationale }}</p>
          <p class="text-xs text-fg-muted">Uncertainty: {{ finding.uncertainty }}</p>
          <ul
            v-if="finding.assumptions.length"
            class="space-y-1 text-xs text-fg-muted"
            aria-label="Finding assumptions"
          >
            <li v-for="(assumption, index) in finding.assumptions" :key="index">
              Assumption: {{ assumption }}
            </li>
          </ul>
          <p v-if="finding.impact" class="text-sm">Estimated impact: {{ finding.impact }}</p>
          <div class="flex flex-wrap gap-2">
            <UiButton
              v-for="refValue in finding.evidenceRefs"
              :key="refValue"
              size="sm"
              variant="ghost"
              @click="emit('inspect', refValue)"
              >Inspect {{ refValue }}</UiButton
            >
          </div>
        </article>
      </section>
    </template>
  </section>
</template>
