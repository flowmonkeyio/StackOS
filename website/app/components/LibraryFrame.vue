<script setup lang="ts">
const props = defineProps<{
  breadcrumbCurrentLabel?: string
}>()

const route = useRoute()
const sectionLabels: Record<string, string> = {
  workflows: 'Workflows',
  agents: 'Agents',
  orchestrators: 'Orchestrators',
  integrations: 'Integrations',
  articles: 'Articles',
}

const breadcrumbs = computed(() => {
  const path = route.path.replace(/\/+$/, '') || '/'
  const parts = path.split('/').filter(Boolean)
  if (parts[0] !== 'library') return []

  const items = [
    { label: 'Home', to: '/' },
    { label: 'Library', to: '/library' },
  ]
  const section = parts[1]
  if (!section) return items

  const sectionPath = `/library/${section}`
  items.push({ label: sectionLabels[section] || section, to: sectionPath })
  if (path === sectionPath) return items

  const fallbackLabel = decodeURIComponent(parts.at(-1) || '')
    .replace(/-/g, ' ')
    .replace(/\b\w/g, character => character.toUpperCase())
  items.push({ label: props.breadcrumbCurrentLabel || fallbackLabel, to: path })
  return items
})
</script>

<template>
  <div id="top" class="site library-site">
    <a class="skip-link" href="#main">Skip to content</a>
    <SiteHeader />

    <div class="library-nav" aria-label="Library sections">
      <div class="shell library-nav__inner">
        <NuxtLink to="/library" exact-active-class="is-active">Library</NuxtLink>
        <NuxtLink to="/library/workflows">Workflows</NuxtLink>
        <NuxtLink to="/library/agents">Agents</NuxtLink>
        <NuxtLink to="/library/orchestrators">Orchestrators</NuxtLink>
        <NuxtLink to="/library/integrations" active-class="is-active">Integrations</NuxtLink>
        <NuxtLink to="/library/articles">Articles</NuxtLink>
      </div>
    </div>

    <main id="main" class="library-main">
      <LibraryBreadcrumbs :items="breadcrumbs" />
      <slot />
    </main>

    <LazySiteFooter hydrate-never />
  </div>
</template>

<style scoped>
.library-site {
  min-height: 100vh;
  background: var(--paper);
}

.library-main {
  position: relative;
}

.library-nav {
  position: sticky;
  top: 72px;
  z-index: 35;
  margin-top: 72px;
  color: var(--paper);
  background: var(--ink);
  border-bottom: 1px solid rgb(255 255 255 / 8%);
}

.library-nav__inner {
  display: flex;
  gap: 4px;
  padding-block: 6px;
  overflow-x: auto;
  scrollbar-width: none;
}

.library-nav__inner::-webkit-scrollbar {
  display: none;
}

.library-nav a {
  flex: 0 0 auto;
  padding: 7px 11px;
  color: var(--ink-soft);
  font-size: 13px;
  font-weight: 650;
  text-decoration: none;
  border-radius: 8px;
}

.library-nav a:hover,
.library-nav a.router-link-exact-active,
.library-nav a.is-active {
  color: var(--paper);
  background: rgb(255 255 255 / 7%);
}

@media (max-width: 820px) {
  .library-nav {
    top: 64px;
    margin-top: 64px;
  }
}
</style>
