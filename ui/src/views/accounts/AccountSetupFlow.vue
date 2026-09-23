<!--
  AccountSetupFlow — presentation-only progress for staged Account setup.
  The Account editor owns stage selection and the slotted provider UI owns its
  inputs and calls. Empty steps keep the wrapper transparent for simple setup.
-->
<script setup lang="ts">
import { UiIcon } from '@/components/ui'

export type AccountSetupStepState = 'complete' | 'current' | 'upcoming'

export interface AccountSetupStep {
  key: string
  label: string
  state: AccountSetupStepState
  optional?: boolean
}

withDefaults(defineProps<{
  steps: AccountSetupStep[]
  ariaLabel?: string
}>(), {
  ariaLabel: 'Account setup progress',
})
</script>

<template>
  <div class="account-setup-flow min-w-0">
    <nav
      v-if="steps.length"
      :aria-label="ariaLabel"
      class="mb-6"
    >
      <ol class="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2 text-sm">
        <li
          v-for="(step, index) in steps"
          :key="step.key"
          class="inline-flex min-w-0 items-center gap-3"
        >
          <UiIcon
            v-if="index > 0"
            name="arrow-right"
            class="h-3.5 w-3.5 text-fg-subtle"
            aria-hidden="true"
          />
          <span
            :aria-current="step.state === 'current' ? 'step' : undefined"
            :class="[
              'inline-flex min-w-0 items-center gap-1.5',
              step.state === 'current'
                ? 'font-semibold text-fg-strong'
                : step.state === 'complete'
                  ? 'text-fg-muted'
                  : 'text-fg-subtle',
            ]"
          >
            <span
              v-if="step.state === 'complete'"
              class="sr-only"
            >Completed: </span>
            <span>{{ step.label }}</span>
            <UiIcon
              v-if="step.state === 'complete'"
              name="check"
              class="h-3.5 w-3.5"
              aria-hidden="true"
            />
            <span
              v-if="step.optional"
              class="text-2xs font-normal text-fg-subtle"
            >Optional</span>
          </span>
        </li>
      </ol>
    </nav>
    <slot />
  </div>
</template>
