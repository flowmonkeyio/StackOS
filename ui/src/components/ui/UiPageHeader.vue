<!--
  UiPageHeader — top of every page. Title, optional breadcrumbs / metadata,
  primary action(s) on the right. Stays out of the way; not a hero.
-->
<script setup lang="ts">
defineProps<{
  title: string;
  /** Eyebrow / overline text above the title. */
  eyebrow?: string;
  /** Subtitle / lede beneath the title. Keep short. */
  description?: string;
  /** Allows wrapper components to define a conditional breadcrumb slot. */
  showBreadcrumbs?: boolean;
  /** When true, sticks to the top under the app shell (z-sticky). */
  sticky?: boolean;
  /** Reduce vertical padding. */
  compact?: boolean;
}>();
</script>

<template>
  <header
    :class="[
      'ui-page-header w-full bg-bg-app',
      sticky && 'sticky top-0 z-sticky border-b border-subtle backdrop-blur-sm bg-bg-app/95',
      compact ? 'py-2' : 'py-4',
    ]"
  >
    <div class="flex flex-col gap-3 sm:flex-row sm:items-start">
      <div class="min-w-0 flex-1">
        <nav
          v-if="showBreadcrumbs !== false && $slots.breadcrumbs"
          aria-label="Breadcrumb"
          class="mb-1 text-xs text-fg-muted"
        >
          <slot name="breadcrumbs" />
        </nav>
        <p
          v-if="eyebrow"
          class="t-overline text-fg-subtle mb-0.5"
        >
          {{ eyebrow }}
        </p>
        <div class="flex items-center gap-3 flex-wrap">
          <h1 class="t-h1 text-fg-strong truncate">
            {{ title }}
          </h1>
          <slot name="titleMeta" />
        </div>
        <p
          v-if="description"
          class="mt-1 text-sm text-fg-muted text-balance max-w-prose"
        >
          {{ description }}
        </p>
        <div
          v-if="$slots.meta"
          class="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-fg-muted"
        >
          <slot name="meta" />
        </div>
      </div>
      <div
        v-if="$slots.actions"
        class="flex shrink-0 flex-wrap items-center gap-2 sm:justify-end"
      >
        <slot name="actions" />
      </div>
    </div>
    <div
      v-if="$slots.tabs"
      class="ui-page-header__tabs mt-3 -mb-2 border-b border-subtle"
    >
      <slot name="tabs" />
    </div>
  </header>
</template>
