<script setup lang="ts">
interface BreadcrumbItem {
  label: string
  to: string
}

defineProps<{
  items: BreadcrumbItem[]
}>()
</script>

<template>
  <nav class="library-breadcrumbs" aria-label="Breadcrumb">
    <div class="shell">
      <ol>
        <li v-for="(item, index) in items" :key="item.to">
          <NuxtLink
            :to="item.to"
            :aria-current="index === items.length - 1 ? 'page' : undefined"
          >
            {{ item.label }}
          </NuxtLink>
        </li>
      </ol>
    </div>
  </nav>
</template>

<style scoped>
.library-breadcrumbs {
  position: absolute;
  inset: 0 0 auto;
  z-index: 3;
  color: var(--paper);
}

.library-breadcrumbs .shell {
  overflow-x: auto;
  scrollbar-width: none;
}

.library-breadcrumbs .shell::-webkit-scrollbar {
  display: none;
}

.library-breadcrumbs ol {
  display: flex;
  width: max-content;
  min-width: 100%;
  align-items: center;
  gap: 9px;
  margin: 0;
  padding: 36px 0 0;
  list-style: none;
}

.library-breadcrumbs li {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 9px;
}

.library-breadcrumbs li + li::before {
  color: var(--ink-muted);
  content: '\203A';
  font-size: 12px;
}

.library-breadcrumbs a {
  display: block;
  max-width: min(42vw, 520px);
  overflow: hidden;
  color: var(--ink-soft);
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 550;
  line-height: 1.4;
  text-decoration: none;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.library-breadcrumbs a:hover,
.library-breadcrumbs a[aria-current='page'] {
  color: var(--paper);
}

.library-breadcrumbs a:focus-visible {
  border-radius: 3px;
  outline: 2px solid var(--signal);
  outline-offset: 3px;
}

@media (max-width: 640px) {
  .library-breadcrumbs ol {
    width: 100%;
    min-width: 0;
    padding-top: 34px;
  }

  .library-breadcrumbs li:not(:last-child) {
    flex: 0 0 auto;
  }

  .library-breadcrumbs li:last-child {
    flex: 1 1 auto;
  }

  .library-breadcrumbs li:last-child a {
    max-width: 100%;
  }
}
</style>
