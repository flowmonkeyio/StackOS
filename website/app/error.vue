<script setup lang="ts">
const props = defineProps<{
  error: {
    statusCode?: number
    statusMessage?: string
  }
}>()

const statusCode = computed(() => props.error?.statusCode || 500)
const isNotFound = computed(() => statusCode.value === 404)
const pageTitle = computed(() =>
  isNotFound.value ? 'Page not found | StackOS' : 'Something went wrong | StackOS',
)
const headline = computed(() =>
  isNotFound.value
    ? 'This page isn’t here. The rest of StackOS is.'
    : 'Something got in the way. Let’s get you back on track.',
)
const description = computed(() =>
  isNotFound.value
    ? 'The link may be old, or the address may be slightly off. Choose a clear place to continue.'
    : 'Try the page again, or start from a familiar place. Your StackOS setup is not affected.',
)

useSeoMeta({
  title: pageTitle,
  description,
  robots: 'noindex, nofollow',
})
</script>

<template>
  <div id="top" class="site error-site">
    <a class="skip-link" href="#error-content">Skip to content</a>
    <SiteHeader />

    <main id="error-content" class="error-main">
      <div class="shell error-layout">
        <section class="error-copy" aria-labelledby="error-title">
          <p class="error-kicker"><span class="status-dot" /> {{ statusCode }} · {{ isNotFound ? 'Page not found' : 'Unexpected error' }}</p>
          <h1 id="error-title">{{ headline }}</h1>
          <p class="error-description">{{ description }}</p>

          <div class="error-actions">
            <a class="button button--signal" href="/">
              Go to StackOS home
              <span aria-hidden="true">→</span>
            </a>
            <a class="button button--ghost" href="/getting-started">
              Open getting started
              <span aria-hidden="true">→</span>
            </a>
          </div>
        </section>

        <aside class="error-map" aria-labelledby="error-map-title">
          <div class="error-map__header">
            <div>
              <span>STACKOS / ROUTE CHECK</span>
              <strong id="error-map-title">Choose where to go next</strong>
            </div>
            <code>{{ statusCode }}</code>
          </div>

          <div class="error-map__path">
            <div class="error-map__node error-map__node--current">
              <img src="/images/stackos-icon.png" alt="" width="38" height="38" />
              <div><span>Requested page</span><strong>No matching destination</strong></div>
              <small>NOT FOUND</small>
            </div>

            <div class="error-map__connector" aria-hidden="true"><span /></div>

            <nav class="error-map__routes" aria-label="Useful destinations">
              <a href="/">
                <span>01</span>
                <strong>Home</strong>
                <small>See what StackOS does</small>
              </a>
              <a href="/getting-started">
                <span>02</span>
                <strong>Getting started</strong>
                <small>Take the next step after install</small>
              </a>
              <a href="/library">
                <span>03</span>
                <strong>Library</strong>
                <small>Explore workflows and guides</small>
              </a>
            </nav>
          </div>

          <p class="error-map__status"><span class="status-dot" /> Everything else is available</p>
        </aside>
      </div>
      <div class="error-glow" aria-hidden="true" />
    </main>

    <SiteFooter />
  </div>
</template>

<style scoped>
.error-site {
  min-height: 100vh;
  color: var(--paper);
  background: var(--ink);
}

.error-main {
  position: relative;
  display: grid;
  min-height: 760px;
  padding: 152px 0 92px;
  overflow: hidden;
  background:
    linear-gradient(120deg, rgb(49 83 214 / 8%), transparent 44%),
    var(--ink);
}

.error-layout {
  position: relative;
  z-index: 2;
  display: grid;
  grid-template-columns: minmax(0, 0.86fr) minmax(480px, 1.14fr);
  gap: clamp(52px, 7vw, 110px);
  align-items: center;
}

.error-copy {
  max-width: 650px;
}

.error-kicker {
  display: flex;
  align-items: center;
  gap: 9px;
  margin: 0 0 25px;
  color: var(--signal);
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 650;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}

.error-copy h1 {
  max-width: 760px;
  margin: 0;
  font-size: clamp(56px, 7vw, 104px);
  font-weight: 630;
  line-height: 0.95;
  letter-spacing: -0.072em;
}

.error-description {
  max-width: 610px;
  margin: 30px 0 0;
  color: var(--ink-soft);
  font-size: clamp(17px, 1.55vw, 21px);
  line-height: 1.7;
}

.error-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 36px;
}

.error-map {
  position: relative;
  padding: 22px;
  background: rgb(255 255 255 / 3%);
  border: 1px solid rgb(255 255 255 / 10%);
  border-radius: 24px;
  box-shadow: 0 40px 100px rgb(0 0 0 / 36%);
}

.error-map::before {
  position: absolute;
  inset: 7px;
  pointer-events: none;
  content: '';
  border: 1px solid rgb(255 255 255 / 4%);
  border-radius: 18px;
}

.error-map__header {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 3px 3px 20px;
  border-bottom: 1px solid rgb(255 255 255 / 8%);
}

.error-map__header span,
.error-map__header strong {
  display: block;
}

.error-map__header span {
  color: var(--cobalt-soft);
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.11em;
}

.error-map__header strong {
  margin-top: 7px;
  font-size: 17px;
}

.error-map__header code {
  padding: 7px 10px;
  color: var(--signal);
  font-family: var(--font-mono);
  font-size: 11px;
  background: rgb(217 255 99 / 7%);
  border: 1px solid rgb(217 255 99 / 16%);
  border-radius: 999px;
}

.error-map__path {
  position: relative;
  z-index: 1;
  padding-top: 20px;
}

.error-map__node {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 13px;
  align-items: center;
  min-height: 82px;
  padding: 15px;
  background: var(--surface-raised);
  border: 1px solid rgb(120 146 255 / 26%);
  border-radius: 14px;
}

.error-map__node img {
  border-radius: 10px;
  box-shadow: 0 8px 24px rgb(0 0 0 / 36%);
}

.error-map__node span,
.error-map__node strong {
  display: block;
}

.error-map__node span {
  color: var(--ink-muted);
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.error-map__node strong {
  margin-top: 5px;
  font-size: 14px;
}

.error-map__node small {
  color: #ff9f73;
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.06em;
}

.error-map__connector {
  display: grid;
  place-items: center;
  height: 31px;
}

.error-map__connector span {
  display: block;
  width: 1px;
  height: 31px;
  background: linear-gradient(var(--cobalt-soft), rgb(120 146 255 / 15%));
}

.error-map__routes {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 9px;
}

.error-map__routes a {
  display: grid;
  align-content: start;
  min-height: 150px;
  padding: 15px;
  color: var(--paper);
  text-decoration: none;
  background: var(--surface);
  border: 1px solid rgb(255 255 255 / 8%);
  border-radius: 13px;
  transition: background 160ms ease, border-color 160ms ease, transform 160ms ease;
}

.error-map__routes a:hover {
  background: var(--surface-strong);
  border-color: rgb(217 255 99 / 28%);
  transform: translateY(-2px);
}

.error-map__routes span {
  color: var(--cobalt-soft);
  font-family: var(--font-mono);
  font-size: 9px;
}

.error-map__routes strong {
  margin-top: 23px;
  font-size: 13px;
}

.error-map__routes small {
  margin-top: 7px;
  color: var(--ink-muted);
  font-size: 11px;
  line-height: 1.45;
}

.error-map__status {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 18px 3px 1px;
  color: #82dca9;
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.error-glow {
  position: absolute;
  top: 10%;
  right: -14%;
  width: 680px;
  height: 680px;
  pointer-events: none;
  background: radial-gradient(circle, rgb(49 83 214 / 23%), transparent 68%);
  filter: blur(14px);
}

@media (max-width: 980px) {
  .error-main {
    padding-top: 132px;
  }

  .error-layout {
    grid-template-columns: 1fr;
  }

  .error-copy {
    max-width: 760px;
  }

  .error-map {
    max-width: 760px;
  }
}

@media (max-width: 620px) {
  .error-main {
    min-height: auto;
    padding: 116px 0 70px;
  }

  .error-layout {
    gap: 46px;
  }

  .error-copy h1 {
    font-size: clamp(47px, 14vw, 66px);
  }

  .error-description {
    margin-top: 23px;
    font-size: 16px;
  }

  .error-actions {
    align-items: stretch;
  }

  .error-actions .button {
    width: 100%;
  }

  .error-map {
    padding: 15px;
    border-radius: 19px;
  }

  .error-map__header {
    padding-bottom: 15px;
  }

  .error-map__routes {
    grid-template-columns: 1fr;
  }

  .error-map__routes a {
    grid-template-columns: auto minmax(0, 1fr);
    column-gap: 12px;
    min-height: 0;
  }

  .error-map__routes span {
    grid-row: 1 / 3;
  }

  .error-map__routes strong,
  .error-map__routes small {
    margin-top: 0;
  }

  .error-map__node {
    grid-template-columns: auto minmax(0, 1fr);
  }

  .error-map__node small {
    grid-column: 2;
  }
}
</style>
