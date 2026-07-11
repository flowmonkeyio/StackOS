<script setup lang="ts">
const screens = [
  {
    key: 'plugins',
    label: 'Business areas',
    image: '/images/plugins.png',
    alt: 'StackOS plugins page showing engineering, support, communications, sales, marketing, branding, media buying, and publishing business areas.',
    title: 'Built for more than one department.',
    description:
      'Add the parts your business needs—from communications and marketing to Shopify, publishing, SEO, and your own internal tools.',
    stat: '12 areas in this project',
  },
  {
    key: 'work',
    label: 'A large piece of work',
    image: '/images/work-map.png',
    alt: 'StackOS Work page showing a large website project with steps, status, blockers, and dependency relationships.',
    title: 'A big job, without the mystery.',
    description:
      'This website project is real StackOS work. Every step, relationship, blocker, completed item, and next move remains visible.',
    stat: '23 steps · 30 relationships',
  },
]

const selectedKey = ref(screens[0]?.key ?? '')
const selected = computed(() => screens.find((screen) => screen.key === selectedKey.value) ?? screens[0]!)
</script>

<template>
  <section id="proof" class="gallery section section--paper" aria-labelledby="gallery-title">
    <div class="shell">
      <div v-reveal class="section-heading section-heading--wide">
        <div>
          <p class="eyebrow">Real screens from the product</p>
          <h2 id="gallery-title">See the whole business.<br /><em>See the whole job.</em></h2>
        </div>
        <p>
          StackOS gives you one place to see what is connected, what is moving, what is blocked,
          and how a complicated piece of work fits together.
        </p>
      </div>

      <div v-reveal="100" class="gallery-shell">
        <div class="gallery-shell__bar">
          <div class="gallery-tabs" role="tablist" aria-label="Product screenshots">
            <button
              v-for="screen in screens"
              :id="`screen-tab-${screen.key}`"
              :key="screen.key"
              type="button"
              role="tab"
              :aria-selected="screen.key === selectedKey"
              :aria-controls="`screen-panel-${screen.key}`"
              :class="{ 'is-active': screen.key === selectedKey }"
              @click="selectedKey = screen.key"
            >
              {{ screen.label }}
            </button>
          </div>
          <div class="gallery-shell__local"><span class="status-dot" /> Runs on your Mac</div>
        </div>

        <div
          :id="`screen-panel-${selected.key}`"
          :key="selected.key"
          class="gallery-shell__body"
          role="tabpanel"
          :aria-labelledby="`screen-tab-${selected.key}`"
        >
          <div class="gallery-image">
            <img
              :src="selected.image"
              :alt="selected.alt"
              width="1280"
              height="720"
              loading="lazy"
              decoding="async"
            />
          </div>
          <aside>
            <p class="eyebrow eyebrow--dark">Captured from this StackOS project</p>
            <h3>{{ selected.title }}</h3>
            <p>{{ selected.description }}</p>
            <div class="gallery-stat">
              <span>{{ selected.stat }}</span>
              <code>real product data</code>
            </div>
            <ul>
              <li><span>✓</span>Your connected work stays visible</li>
              <li><span>✓</span>Relationships and blockers stay clear</li>
              <li><span>✓</span>Results stay attached to the job</li>
            </ul>
          </aside>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.gallery-shell {
  margin-top: 64px;
  overflow: hidden;
  color: var(--paper);
  background: var(--ink);
  border: 1px solid #202633;
  border-radius: 24px;
  box-shadow: 0 55px 110px rgb(25 28 38 / 24%);
}

.gallery-shell__bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 58px;
  padding: 9px 13px;
  border-bottom: 1px solid rgb(255 255 255 / 8%);
}

.gallery-tabs {
  display: flex;
  gap: 4px;
}

.gallery-tabs button {
  padding: 10px 13px;
  color: var(--ink-muted);
  font-family: var(--font-mono);
  font-size: 10px;
  background: transparent;
  border: 0;
  border-radius: 8px;
  cursor: pointer;
}

.gallery-tabs button.is-active {
  color: var(--paper);
  background: rgb(255 255 255 / 7%);
}

.gallery-shell__local {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--ink-soft);
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}

.gallery-shell__body {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  min-height: 550px;
}

.gallery-image {
  display: grid;
  padding: 24px;
  place-items: center;
  overflow: hidden;
  background:
    linear-gradient(rgb(255 255 255 / 2%) 1px, transparent 1px),
    linear-gradient(90deg, rgb(255 255 255 / 2%) 1px, transparent 1px),
    #0b0e14;
  background-size: 28px 28px;
}

.gallery-image img {
  width: 100%;
  height: auto;
  border: 1px solid rgb(255 255 255 / 13%);
  border-radius: 12px;
  box-shadow: 0 35px 70px rgb(0 0 0 / 40%);
}

.gallery-shell aside {
  display: flex;
  flex-direction: column;
  padding: 32px;
  background: var(--surface-raised);
  border-left: 1px solid rgb(255 255 255 / 8%);
}

.gallery-shell aside h3 {
  margin: 16px 0 13px;
  font-size: 30px;
  line-height: 1.05;
  letter-spacing: -0.05em;
}

.gallery-shell aside > p:not(.eyebrow) {
  margin: 0;
  color: var(--ink-soft);
  font-size: 12px;
  line-height: 1.7;
}

.gallery-stat {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 28px 0 16px;
  padding: 15px 0;
  border-top: 1px solid rgb(255 255 255 / 8%);
  border-bottom: 1px solid rgb(255 255 255 / 8%);
}

.gallery-stat span {
  color: var(--signal);
  font-weight: 720;
}

.gallery-stat code {
  color: var(--ink-muted);
  font-size: 9px;
}

.gallery-shell ul {
  display: grid;
  gap: 12px;
  margin: auto 0 0;
  padding: 0;
  color: var(--ink-soft);
  font-size: 11px;
  list-style: none;
}

.gallery-shell li {
  display: flex;
  gap: 9px;
}

.gallery-shell li span {
  color: var(--signal);
}

@media (max-width: 980px) {
  .gallery-shell__body {
    grid-template-columns: 1fr;
  }

  .gallery-shell aside {
    border-top: 1px solid rgb(255 255 255 / 8%);
    border-left: 0;
  }

  .gallery-shell ul {
    margin-top: 22px;
  }
}

@media (max-width: 600px) {
  .gallery-shell__bar {
    align-items: stretch;
  }

  .gallery-tabs {
    width: 100%;
  }

  .gallery-tabs button {
    flex: 1;
  }

  .gallery-shell__local {
    display: none;
  }

  .gallery-shell__body {
    min-height: 0;
  }

  .gallery-image {
    padding: 10px;
  }

  .gallery-image img {
    min-width: 680px;
    transform: translateX(19%);
  }

  .gallery-shell aside {
    padding: 26px 22px;
  }
}
</style>
