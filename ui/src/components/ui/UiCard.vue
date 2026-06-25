<!--
  UiCard — surface for real panels and content groups. Do NOT use for
  every page section. Cards should not be nested.

  Use `padded={false}` when inner content (like a DataTable) should bleed
  to the edges, with its own padding handling.
-->
<script setup lang="ts">
withDefaults(defineProps<{
  /** Show resting elevation (default true). */
  elevated?: boolean;
  /** Apply default body padding (default true). */
  padded?: boolean;
  /** Compact density — tighter padding. */
  density?: 'compact' | 'comfortable';
  /** Polite — quieter background, no shadow. */
  variant?: 'default' | 'subtle';
  /** Render as <section> with aria-labelledby pointing at slot[header]. */
  section?: boolean;
  ariaLabel?: string;
}>(), {
  elevated: true,
  padded: true,
  density: 'compact',
  variant: 'default',
  section: false,
  ariaLabel: undefined,
});
</script>

<template>
  <component
    :is="section ? 'section' : 'div'"
    :aria-label="ariaLabel"
    :class="[
      'ui-card rounded-lg border bg-bg-surface',
      variant === 'subtle' ? 'border-subtle bg-bg-surface-alt' : 'border-default',
      elevated && 'shadow-xs',
    ]"
  >
    <header
      v-if="$slots.header"
      :class="[
        'ui-card__header flex items-center justify-between gap-3 border-b border-subtle',
        density === 'comfortable' ? 'px-5 py-4' : 'px-4 py-3',
      ]"
    >
      <slot name="header" />
    </header>
    <div
      :class="[
        'ui-card__body min-w-0 flex-1',
        padded && (density === 'comfortable' ? 'p-5' : 'p-4'),
      ]"
    >
      <slot />
    </div>
    <footer
      v-if="$slots.footer"
      :class="[
        'ui-card__footer flex items-center justify-end gap-2 border-t border-subtle',
        density === 'comfortable' ? 'px-5 py-4' : 'px-4 py-3',
      ]"
    >
      <slot name="footer" />
    </footer>
  </component>
</template>
