<!--
  UiCodeBlock — read-only code display with optional copy button + line numbers.
  Use UiJsonBlock for JSON-specific niceties.
-->
<script setup lang="ts">
import { ref } from 'vue';
import UiIcon from './UiIcon.vue';
import UiIconButton from './UiIconButton.vue';
import { copyTextToClipboard } from '@/lib/clipboard';

defineProps<{
  code: string;
  language?: string;
  /** Show line numbers. */
  numbered?: boolean;
  /** Wrap long lines. Default false (horizontal scroll). */
  wrap?: boolean;
  /** Show copy button. */
  copyable?: boolean;
  /** Compact: smaller font + padding. */
  density?: 'compact' | 'comfortable';
  /** Maximum visible height (CSS). */
  maxHeight?: string;
  ariaLabel?: string;
}>();

const copied = ref(false);

async function copy(code: string) {
  if (await copyTextToClipboard(code)) {
    copied.value = true;
    setTimeout(() => { copied.value = false; }, 1500);
  }
}
</script>

<template>
  <div
    :class="[
      'ui-codeblock relative rounded-lg border border-subtle bg-bg-sunken font-mono text-fg-default',
      density === 'comfortable' ? 'text-sm' : 'text-xs',
    ]"
    :aria-label="ariaLabel"
  >
    <div
      v-if="language || copyable"
      class="ui-codeblock__chrome flex items-center justify-between border-b border-subtle px-3 py-1.5 font-sans text-2xs font-medium text-fg-muted"
    >
      <span>{{ language }}</span>
      <UiIconButton
        v-if="copyable"
        :aria-label="copied ? 'Copied' : 'Copy code'"
        size="sm"
        variant="ghost"
        @click="copy(code)"
      >
        <UiIcon
          v-if="!copied"
          name="copy"
          class="h-3.5 w-3.5"
          aria-hidden="true"
        />
        <UiIcon
          v-else
          name="check"
          class="h-3.5 w-3.5"
          aria-hidden="true"
        />
      </UiIconButton>
    </div>
    <pre
      :class="[
        'ui-codeblock__pre overflow-auto m-0 leading-relaxed',
        wrap ? 'whitespace-pre-wrap break-all' : 'whitespace-pre',
        density === 'comfortable' ? 'p-4' : 'p-3',
      ]"
      :style="{ maxHeight }"
    ><code><template v-if="numbered"><span
      v-for="(line, i) in code.split('\n')"
      :key="i"
      class="ui-codeblock__line flex"
    ><span
      class="ui-codeblock__lineno select-none text-fg-disabled tabular-nums pr-3 text-right"
      style="min-width: 2.5em"
    >{{ i + 1 }}</span><span class="flex-1">{{ line || ' ' }}</span></span></template><template v-else>{{ code }}</template></code></pre>
  </div>
</template>
