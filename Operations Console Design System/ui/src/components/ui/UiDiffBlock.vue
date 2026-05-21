<!--
  UiDiffBlock — side-by-side or unified diff display.
  Each line carries a kind: 'add' | 'remove' | 'context' | 'meta'.
-->
<script setup lang="ts">
import { computed } from 'vue';

export interface DiffLine {
  kind: 'add' | 'remove' | 'context' | 'meta';
  /** Old line number, if applicable. */
  oldNumber?: number | null;
  /** New line number, if applicable. */
  newNumber?: number | null;
  text: string;
}

defineProps<{
  lines: DiffLine[];
  /** Hide line numbers. */
  noNumbers?: boolean;
  /** Title shown above the diff (e.g. "template.yaml"). */
  filename?: string;
  /** Wrap long lines. */
  wrap?: boolean;
  maxHeight?: string;
  ariaLabel?: string;
}>();

function rowClass(kind: DiffLine['kind']) {
  switch (kind) {
    case 'add':    return 'bg-success-subtle text-success-fg';
    case 'remove': return 'bg-danger-subtle text-danger-fg';
    case 'meta':   return 'bg-bg-sunken text-fg-subtle italic';
    default:       return '';
  }
}
function gutter(kind: DiffLine['kind']) {
  return kind === 'add' ? '+' : kind === 'remove' ? '−' : kind === 'meta' ? '@' : ' ';
}
</script>

<template>
  <div
    :aria-label="ariaLabel"
    class="ui-diffblock rounded-md border border-subtle bg-bg-surface font-mono text-xs overflow-hidden"
  >
    <div v-if="filename" class="px-3 py-1.5 border-b border-subtle text-2xs font-sans font-semibold uppercase tracking-wider text-fg-subtle bg-bg-surface-alt">
      {{ filename }}
    </div>
    <div class="overflow-auto" :style="{ maxHeight }">
      <table class="w-full border-collapse">
        <tbody>
          <tr v-for="(line, i) in lines" :key="i" :class="rowClass(line.kind)">
            <td v-if="!noNumbers" class="select-none text-fg-disabled tabular-nums px-2 text-right border-r border-subtle" style="width: 2.75em">{{ line.oldNumber ?? '' }}</td>
            <td v-if="!noNumbers" class="select-none text-fg-disabled tabular-nums px-2 text-right border-r border-subtle" style="width: 2.75em">{{ line.newNumber ?? '' }}</td>
            <td class="select-none px-1.5 text-fg-subtle text-center" style="width: 1.5em">{{ gutter(line.kind) }}</td>
            <td :class="['px-2 py-px', wrap ? 'whitespace-pre-wrap break-all' : 'whitespace-pre']">{{ line.text }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
