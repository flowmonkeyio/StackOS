<script setup lang="ts">
const route = useRoute()
const config = useRuntimeConfig()

const { data: guide } = await useAsyncData('getting-started-guide', () =>
  queryCollection('guides').where('stem', '=', 'guides/getting-started').first(),
)

if (!guide.value) throw createError({ statusCode: 404, statusMessage: 'Getting-started guide not found' })

useLibrarySeo({
  title: guide.value.seoTitle,
  description: guide.value.description,
  type: 'article',
  publishedAt: guide.value.publishedAt,
  updatedAt: guide.value.updatedAt,
})

useSchemaOrg([
  defineArticle({
    headline: `${guide.value.title} ${guide.value.headline}`,
    description: guide.value.description,
    datePublished: guide.value.publishedAt,
    dateModified: guide.value.updatedAt,
    author: { '@type': 'Organization', name: guide.value.author },
    image: `${config.public.siteUrl}/images/plugins.png`,
  }),
  defineBreadcrumb({
    itemListElement: [
      { name: 'Home', item: '/' },
      { name: 'Getting started', item: route.path },
    ],
  }),
])

const guideSections = [
  { href: '#before-you-start', label: 'Before you start' },
  { href: '#_1-open-stackos-once', label: 'Open StackOS' },
  { href: '#_2-reopen-the-ai-tool-you-already-use', label: 'Reopen your AI tool' },
  { href: '#_3-tell-it-which-project-you-are-working-on', label: 'Choose the project' },
  { href: '#_4-start-with-one-job-not-a-long-setup-list', label: 'Choose one job' },
  { href: '#_5-connect-only-what-this-job-needs', label: 'Add one connection' },
  { href: '#_6-review-the-plan-then-say-when-to-start', label: 'Review the plan' },
  { href: '#_7-watch-the-work-stay-organized', label: 'Watch the work' },
  { href: '#if-something-does-not-work', label: 'Get unstuck' },
]

function displayDate(date: string) {
  return new Intl.DateTimeFormat('en-US', { dateStyle: 'long', timeZone: 'UTC' }).format(new Date(`${date}T12:00:00Z`))
}
</script>

<template>
  <div v-if="guide" id="top" class="site guide-page">
    <a class="skip-link" href="#guide-content">Skip to guide</a>
    <SiteHeader />

    <main id="main">
      <header class="guide-hero">
        <div class="shell guide-hero__inner">
          <p class="guide-hero__kicker"><span class="status-dot" /> Getting started · After installation</p>
          <h1>{{ guide.title }}<em>{{ guide.headline }}</em></h1>
          <p class="guide-hero__description">{{ guide.description }}</p>

          <div class="guide-hero__actions">
            <a class="button button--signal" href="#guide-content">Show me what to do <span aria-hidden="true">↓</span></a>
          </div>

          <div class="guide-hero__meta">
            <span>{{ guide.estimatedTime }}</span>
            <span>{{ guide.readingTime }}</span>
            <time :datetime="guide.updatedAt">Updated {{ displayDate(guide.updatedAt) }}</time>
          </div>
        </div>
        <div class="guide-hero__glow" aria-hidden="true" />
      </header>

      <article id="guide-content" class="article-body guide-body">
        <div class="shell guide-layout">
          <aside class="guide-nav guide-nav--desktop" aria-label="Getting-started sections">
            <p>In this guide</p>
            <ol>
              <li v-for="(section, index) in guideSections" :key="section.href">
                <a :href="section.href"><span>{{ String(index + 1).padStart(2, '0') }}</span>{{ section.label }}</a>
              </li>
            </ol>
            <a class="guide-nav__plain" :href="guide.markdownUrl">Plain-text version <span aria-hidden="true">↗</span></a>
          </aside>

          <details class="guide-nav guide-nav--mobile">
            <summary>
              <span><strong>In this guide</strong><small>9 short sections</small></span>
              <i aria-hidden="true">+</i>
            </summary>
            <div class="guide-nav__mobile-body">
              <ol>
                <li v-for="(section, index) in guideSections" :key="`mobile-${section.href}`">
                  <a :href="section.href"><span>{{ String(index + 1).padStart(2, '0') }}</span>{{ section.label }}</a>
                </li>
              </ol>
              <a class="guide-nav__plain" :href="guide.markdownUrl">Plain-text version <span aria-hidden="true">↗</span></a>
            </div>
          </details>

          <div class="article-prose guide-prose">
            <ContentRenderer :value="guide" />
          </div>
        </div>
      </article>

      <section class="guide-next" aria-labelledby="guide-next-title">
        <div class="shell guide-next__inner">
          <div>
            <p class="eyebrow eyebrow--dark">One clear job is enough</p>
            <h2 id="guide-next-title">Open your AI tool.<br /><em>Ask StackOS to keep the work together.</em></h2>
          </div>
          <a class="button button--signal" href="#_3-tell-it-which-project-you-are-working-on">Use the first message <span aria-hidden="true">↑</span></a>
        </div>
      </section>
    </main>

    <SiteFooter />
  </div>
</template>

<style scoped>
.guide-hero {
  position: relative;
  min-height: 720px;
  padding: 168px 0 104px;
  overflow: hidden;
  color: var(--paper);
  background:
    linear-gradient(90deg, rgb(255 255 255 / 2%) 1px, transparent 1px) 0 0 / 88px 88px,
    linear-gradient(rgb(255 255 255 / 2%) 1px, transparent 1px) 0 0 / 88px 88px,
    var(--ink);
}

.guide-hero__inner { position: relative; z-index: 2; }

.guide-hero__kicker {
  display: flex;
  gap: 10px;
  align-items: center;
  width: fit-content;
  margin: 0 0 32px;
  padding: 8px 11px;
  color: var(--ink-soft);
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: .08em;
  text-transform: uppercase;
  background: rgb(255 255 255 / 4%);
  border: 1px solid rgb(255 255 255 / 9%);
  border-radius: 999px;
}

.guide-hero h1 {
  max-width: 1120px;
  margin: 0;
  font-size: clamp(58px, 7.4vw, 108px);
  font-weight: 650;
  line-height: .92;
  letter-spacing: -.072em;
}

.guide-hero h1 em {
  display: block;
  max-width: 1000px;
  margin-top: 12px;
  color: var(--signal);
  font-style: normal;
}

.guide-hero__description {
  max-width: 720px;
  margin: 34px 0 0;
  color: var(--ink-soft);
  font-size: clamp(17px, 1.5vw, 20px);
  line-height: 1.7;
}

.guide-hero__actions { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 34px; }

.guide-hero__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 9px 25px;
  margin-top: 44px;
  padding-top: 20px;
  color: var(--ink-muted);
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: .07em;
  text-transform: uppercase;
  border-top: 1px solid rgb(255 255 255 / 8%);
}

.guide-hero__glow {
  position: absolute;
  right: -15vw;
  bottom: -55%;
  width: 70vw;
  height: 760px;
  background: radial-gradient(circle, rgb(91 124 255 / 22%), transparent 65%);
  filter: blur(20px);
}

.guide-body { padding: 90px 0 120px; }

.guide-layout {
  display: grid;
  grid-template-columns: 230px minmax(0, 860px);
  gap: clamp(54px, 7vw, 105px);
  justify-content: center;
  align-items: start;
}

.guide-nav {
  position: sticky;
  top: 102px;
  padding: 19px;
  background: #e9e7de;
  border: 1px solid var(--paper-border);
  border-radius: 13px;
}

.guide-nav--mobile { display: none; }

.guide-nav > p {
  margin: 0 0 13px;
  color: var(--cobalt);
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 700;
  letter-spacing: .1em;
  text-transform: uppercase;
}

.guide-nav ol { display: grid; gap: 2px; margin: 0; padding: 0; list-style: none; }
.guide-nav a { text-decoration: none; }
.guide-nav li a { display: grid; grid-template-columns: 22px 1fr; gap: 6px; padding: 8px 5px; color: #51514b; font-size: 11px; font-weight: 650; line-height: 1.3; border-radius: 6px; }
.guide-nav li a:hover { color: var(--ink); background: rgb(49 83 214 / 6%); }
.guide-nav li span { color: var(--cobalt); font-family: var(--font-mono); font-size: 8px; }
.guide-nav__plain { display: flex; justify-content: space-between; margin-top: 14px; padding: 12px 5px 2px; color: var(--cobalt); font-family: var(--font-mono); font-size: 9px; font-weight: 700; border-top: 1px solid var(--paper-border); text-transform: uppercase; }

.guide-nav--mobile summary {
  display: flex;
  min-height: 44px;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  cursor: pointer;
  list-style: none;
}

.guide-nav--mobile summary::-webkit-details-marker { display: none; }
.guide-nav--mobile summary > span { display: grid; gap: 4px; }
.guide-nav--mobile summary strong { color: var(--cobalt); font-family: var(--font-mono); font-size: 10px; letter-spacing: .1em; text-transform: uppercase; }
.guide-nav--mobile summary small { color: #62625c; font-size: 12px; }
.guide-nav--mobile summary i { display: grid; width: 30px; height: 30px; place-items: center; color: var(--cobalt); font-family: var(--font-mono); font-size: 18px; font-style: normal; background: rgb(49 83 214 / 7%); border-radius: 8px; transition: transform 180ms ease; }
.guide-nav--mobile[open] summary i { transform: rotate(45deg); }
.guide-nav__mobile-body { padding-top: 15px; border-top: 1px solid var(--paper-border); }
.guide-nav--mobile ol { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 3px 10px; }

.guide-prose { width: 100%; margin: 0; }
.guide-prose :deep(pre) { margin: 28px 0 36px; padding: 22px 24px; overflow-x: auto; color: #dbe2ef; background: #10141d; border: 1px solid #242b39; border-radius: 12px; box-shadow: 0 16px 35px rgb(9 11 16 / 10%); }
.guide-prose :deep(pre code) { font-family: var(--font-mono); font-size: 13px; line-height: 1.65; white-space: pre-wrap; }
.guide-prose :deep(p code),
.guide-prose :deep(li code) { padding: 2px 5px; color: #243c9e; font-family: var(--font-mono); font-size: .82em; background: #e1e3ec; border-radius: 4px; }

.guide-next { padding: 105px 0; color: var(--paper); background: #10141d; border-top: 1px solid rgb(255 255 255 / 8%); }
.guide-next__inner { display: flex; justify-content: space-between; gap: 50px; align-items: end; }
.guide-next h2 { max-width: 890px; margin: 16px 0 0; font-size: clamp(45px, 5.8vw, 78px); font-weight: 630; line-height: .98; letter-spacing: -.065em; }
.guide-next h2 em { color: var(--signal); font-style: normal; }
.guide-next .button { flex: 0 0 auto; }

@media (max-width: 1320px) {
  .guide-layout { max-width: 860px; grid-template-columns: minmax(0, 1fr); gap: 42px; }
  .guide-nav--desktop { display: none; }
  .guide-nav--mobile { position: static; display: block; }
  .guide-next__inner { align-items: start; flex-direction: column; }
}

@media (max-width: 620px) {
  .guide-hero { min-height: 0; padding: 122px 0 72px; }
  .guide-hero h1 { font-size: clamp(43px, 12vw, 50px); line-height: .95; letter-spacing: -.06em; }
  .guide-hero h1 em { margin-top: 9px; }
  .guide-hero__description { margin-top: 26px; font-size: 17px; line-height: 1.62; }
  .guide-hero__actions .button { width: 100%; justify-content: center; }
  .guide-hero__meta { margin-top: 34px; }
  .guide-body { padding-top: 48px; }
  .guide-layout { gap: 34px; }
  .guide-nav--mobile ol { grid-template-columns: 1fr; }
  .guide-prose :deep(pre) { padding: 18px; }
  .guide-prose :deep(pre code) { font-size: 11px; }
  .guide-next { padding: 76px 0; }
}
</style>
