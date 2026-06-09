<!--
  UiPageHeader — top of every page. Title, optional breadcrumbs / metadata,
  primary action(s) on the right. Stays out of the way; not a hero.
-->
<script setup lang="ts">
defineProps<{
  title: string;
  /** Eyebrow / overline text above the title. Use sparingly. */
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
      'ui-page-header w-full',
      sticky && 'sticky top-0 z-sticky border-b border-subtle bg-bg-app/95 backdrop-blur-sm',
      compact ? 'pb-1' : 'pb-2',
    ]"
  >
    <div class="flex flex-col gap-3 sm:flex-row sm:items-start">
      <div class="min-w-0 flex-1">
        <nav
          v-if="showBreadcrumbs !== false && $slots.breadcrumbs"
          aria-label="Breadcrumb"
          class="mb-1.5 text-xs text-fg-muted"
        >
          <slot name="breadcrumbs" />
        </nav>
        <p
          v-if="eyebrow"
          class="t-overline mb-1 text-fg-subtle"
        >
          {{ eyebrow }}
        </p>
        <div class="flex flex-wrap items-center gap-x-3 gap-y-1">
          <h1 class="t-h1 truncate text-fg-strong">
            {{ title }}
          </h1>
          <slot name="titleMeta" />
        </div>
        <p
          v-if="description"
          class="mt-1 max-w-prose text-balance text-sm text-fg-muted"
        >
          {{ description }}
        </p>
        <div
          v-if="$slots.meta"
          class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs text-fg-muted"
        >
          <slot name="meta" />
        </div>
      </div>
      <div
        v-if="$slots.actions"
        class="flex shrink-0 flex-wrap items-center gap-2 sm:justify-end sm:pt-0.5"
      >
        <slot name="actions" />
      </div>
    </div>
    <div
      v-if="$slots.tabs"
      class="ui-page-header__tabs -mb-2 mt-3 border-b border-subtle"
    >
      <slot name="tabs" />
    </div>
  </header>
</template>
