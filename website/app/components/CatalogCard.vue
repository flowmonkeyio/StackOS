<script setup lang="ts">
const props = defineProps<{
  kind: 'workflows' | 'agents' | 'orchestrators'
  slug: string
  name: string
  description: string
  domain: string
  audience: string
  color: string
  meta?: string
}>()

const singular = computed(() => props.kind.slice(0, -1))
</script>

<template>
  <NuxtLink class="catalog-card" :to="`/library/${kind}/${slug}`" :style="{ '--card-color': color }">
    <div class="catalog-card__visual" aria-hidden="true">
      <template v-if="kind === 'workflows'">
        <span /><i /><span /><i /><span />
      </template>
      <template v-else-if="kind === 'agents'">
        <span class="catalog-card__agent">A</span>
        <i class="catalog-card__orbit" />
      </template>
      <template v-else>
        <span class="catalog-card__hub">O</span>
        <i class="catalog-card__branch catalog-card__branch--one" />
        <i class="catalog-card__branch catalog-card__branch--two" />
        <i class="catalog-card__branch catalog-card__branch--three" />
      </template>
    </div>

    <div class="catalog-card__meta">
      <span>{{ singular }}</span>
      <b>{{ domain.replace('-', ' ') }}</b>
    </div>
    <h3>{{ name }}</h3>
    <p>{{ description }}</p>
    <div class="catalog-card__footer">
      <span>{{ meta || audience }}</span>
      <b aria-hidden="true">↗</b>
    </div>
  </NuxtLink>
</template>

<style scoped>
.catalog-card {
  position: relative;
  display: flex;
  min-width: 0;
  min-height: 390px;
  flex-direction: column;
  padding: 24px;
  overflow: hidden;
  color: var(--paper);
  text-decoration: none;
  background: #10131b;
  border: 1px solid rgb(255 255 255 / 9%);
  border-radius: 18px;
  transition: transform 220ms ease, border-color 220ms ease, box-shadow 220ms ease;
}

.catalog-card:hover {
  border-color: color-mix(in srgb, var(--card-color) 45%, transparent);
  box-shadow: 0 22px 60px rgb(0 0 0 / 24%);
  transform: translateY(-5px);
}

.catalog-card__visual {
  position: relative;
  display: flex;
  height: 112px;
  align-items: center;
  justify-content: center;
  margin: -4px -4px 24px;
  background:
    radial-gradient(circle, color-mix(in srgb, var(--card-color) 15%, transparent), transparent 64%),
    linear-gradient(rgb(255 255 255 / 4%) 1px, transparent 1px),
    linear-gradient(90deg, rgb(255 255 255 / 4%) 1px, transparent 1px);
  background-size: auto, 22px 22px, 22px 22px;
  border-radius: 12px;
}

.catalog-card__visual > span:not(.catalog-card__agent, .catalog-card__hub) {
  width: 29px;
  height: 29px;
  background: #171d29;
  border: 1px solid var(--card-color);
  border-radius: 8px;
}

.catalog-card__visual > i:not(.catalog-card__orbit, .catalog-card__branch) {
  width: 36px;
  height: 1px;
  background: linear-gradient(90deg, var(--card-color), rgb(255 255 255 / 12%));
}

.catalog-card__agent,
.catalog-card__hub {
  display: grid;
  width: 50px;
  height: 50px;
  place-items: center;
  color: var(--ink);
  font-family: var(--font-mono);
  font-weight: 800;
  background: var(--card-color);
  border-radius: 15px;
}

.catalog-card__orbit {
  position: absolute;
  width: 82px;
  height: 82px;
  border: 1px dashed color-mix(in srgb, var(--card-color) 55%, transparent);
  border-radius: 50%;
  animation: card-orbit 9s linear infinite;
}

.catalog-card__branch {
  position: absolute;
  left: 50%;
  width: 60px;
  height: 1px;
  background: var(--card-color);
  transform-origin: left center;
}

.catalog-card__branch--one { transform: rotate(0deg); }
.catalog-card__branch--two { transform: rotate(120deg); }
.catalog-card__branch--three { transform: rotate(240deg); }

.catalog-card__meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-family: var(--font-mono);
  font-size: 12px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.catalog-card__meta span { color: var(--card-color); }
.catalog-card__meta b { color: var(--ink-muted); font-weight: 500; }

.catalog-card h3 {
  margin: 16px 0 10px;
  font-size: 22px;
  line-height: 1.13;
  letter-spacing: -0.035em;
}

.catalog-card p {
  margin: 0;
  color: var(--ink-soft);
  font-size: 16px;
  line-height: 1.65;
}

.catalog-card__footer {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 16px;
  margin-top: auto;
  padding-top: 25px;
  color: var(--ink-muted);
  font-size: 14px;
}

.catalog-card__footer b {
  color: var(--card-color);
  font-size: 18px;
}

@keyframes card-orbit { to { transform: rotate(360deg); } }
</style>
