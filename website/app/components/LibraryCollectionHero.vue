<script setup lang="ts">
defineProps<{
  kicker: string
  title: string
  accent: string
  description: string
}>()

const slots = useSlots()
</script>

<template>
  <section class="catalog-hero library-collection-hero" :class="{ 'has-aside': slots.aside }">
    <div class="shell library-collection-hero__layout">
      <div class="library-collection-hero__copy">
        <p class="library-kicker">{{ kicker }}</p>
        <h1>{{ title }} <em>{{ accent }}</em></h1>
        <p class="catalog-hero__lede">{{ description }}</p>
        <div v-if="slots.meta" class="catalog-hero__meta">
          <slot name="meta" />
        </div>
      </div>

      <div v-if="slots.aside" class="library-collection-hero__aside">
        <slot name="aside" />
      </div>
    </div>
  </section>
</template>

<style scoped>
.library-collection-hero__layout {
  min-width: 0;
}

.library-collection-hero__copy,
.library-collection-hero__aside {
  min-width: 0;
}

.library-collection-hero.has-aside .library-collection-hero__layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(440px, 0.78fr);
  gap: 70px;
  align-items: center;
}

@media (max-width: 1020px) {
  .library-collection-hero.has-aside .library-collection-hero__layout {
    grid-template-columns: 1fr;
  }
}
</style>
