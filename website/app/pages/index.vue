<script setup lang="ts">
const downloadUrl = useDownloadUrl()
const { workflows } = useLibraryCatalog()
const homepageDescription = 'Keep working in Codex, Claude Code, Gemini, and the business tools you already use. StackOS turns requests into clear, trackable workflows on your Mac.'
const homepageFaq = [
  {
    question: 'What does StackOS do?',
    answer: 'StackOS turns a request from your AI tool into a visible workflow with clear steps, connected tool actions, durable status, and recorded results.',
  },
  {
    question: 'Does StackOS replace Codex, Claude Code, or Gemini?',
    answer: 'No. You keep working in your preferred AI tool. StackOS gives that tool an organized, permission-aware way to plan the job, use connected apps, and keep the result.',
  },
  {
    question: 'Where does StackOS run?',
    answer: 'StackOS runs locally on your Mac. It keeps workflow state and credential references local while approved connected providers perform their bounded actions.',
  },
] as const

useSiteSeo({
  title: 'Keep AI-powered work organized from start to finish',
  description: homepageDescription,
})

useSchemaOrg([
  defineWebPage({
    '@type': 'FAQPage',
    name: 'StackOS — Keep AI-powered work organized from start to finish',
    description: homepageDescription,
  }),
  defineSoftwareApp({
    name: 'StackOS',
    description: homepageDescription,
    applicationCategory: 'BusinessApplication',
    operatingSystem: 'macOS',
    downloadUrl,
    offers: [],
  }),
  ...homepageFaq.map((item) => defineQuestion({
    question: item.question,
    answer: item.answer,
  })),
])
</script>

<template>
  <div id="top" class="site">
    <a class="skip-link" href="#main">Skip to content</a>
    <SiteHeader />

    <main id="main">
      <section class="hero section--ink">
        <div class="hero__grid shell">
          <div class="hero__copy">
            <div class="announcement">
              <span class="status-dot" />
              Works with your AI tools
              <b>Runs on your Mac</b>
            </div>
            <h1>Your AI can do the work.<br /><em>StackOS keeps it on track.</em></h1>
            <p class="hero__lede">
              Keep working in Codex, Claude Code, Gemini, or another AI tool. StackOS turns each
              request into a clear plan, moves it through your existing apps, and remembers every step.
            </p>
            <div class="hero__actions">
              <a
                class="button button--signal"
                :href="downloadUrl"
                data-download="stackos-mac"
              >
                <img class="button__logo button__logo--apple" src="/images/apple.webp" alt="" width="20" height="20" />
                Download for Mac
                <span aria-hidden="true">↓</span>
              </a>
              <a class="button button--ghost" href="#workflow">
                See how it works
                <span aria-hidden="true">↓</span>
              </a>
            </div>
            <ul class="hero__proof" aria-label="StackOS product traits">
              <li><span>01</span> Keep your current AI tool</li>
              <li><span>02</span> Connect the apps you already use</li>
              <li><span>03</span> See every step and result</li>
            </ul>
          </div>

          <div class="hero__visual" aria-hidden="false">
            <HeroWorkbench />
          </div>
        </div>

        <div class="hero__wash" aria-hidden="true" />
      </section>

      <section class="proof-rail" aria-label="Current StackOS proof points">
        <div class="shell proof-rail__grid">
          <div>
            <strong>Local</strong>
            <span>runs on your Mac</span>
          </div>
          <div>
            <strong>{{ workflows.length }}</strong>
            <span>ready-made workflows</span>
          </div>
          <div>
            <strong>Private</strong>
            <span>your logins stay yours</span>
          </div>
          <div>
            <strong>Complete</strong>
            <span>every step remembered</span>
          </div>
        </div>
      </section>

      <LazyProblemComparison hydrate-on-visible />
      <LazyProductWorkflow hydrate-on-visible />
      <LazyExecutionStory hydrate-on-visible />
      <LazyDomainConstellation hydrate-on-visible />
      <LazyProductGallery hydrate-on-visible />
      <LazyTrustArchitecture hydrate-on-visible />
      <section class="home-faq section" aria-labelledby="home-faq-title">
        <div class="shell home-faq__grid">
          <div>
            <p class="eyebrow">Direct answers</p>
            <h2 id="home-faq-title">What to know before you start.</h2>
          </div>
          <div class="home-faq__items">
            <details v-for="item in homepageFaq" :key="item.question">
              <summary data-faq-question>{{ item.question }}</summary>
              <p data-faq-answer>{{ item.answer }}</p>
            </details>
          </div>
        </div>
      </section>
      <LazyInstallCta hydrate-on-visible />
    </main>

    <LazySiteFooter hydrate-never />
  </div>
</template>

<style scoped>
.hero {
  position: relative;
  min-height: 820px;
  padding: 146px 0 72px;
  overflow: hidden;
}

.hero__grid {
  position: relative;
  z-index: 2;
  display: grid;
  grid-template-columns: minmax(420px, 0.82fr) minmax(0, 1.18fr);
  gap: 48px;
  align-items: center;
}

.hero__copy {
  position: relative;
  z-index: 3;
}

.announcement {
  display: inline-flex;
  align-items: center;
  gap: 9px;
  margin-bottom: 28px;
  padding: 8px 10px;
  color: var(--ink-soft);
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.045em;
  text-transform: uppercase;
  background: rgb(255 255 255 / 4%);
  border: 1px solid rgb(255 255 255 / 9%);
  border-radius: 999px;
}

.announcement b {
  padding-left: 8px;
  color: var(--signal);
  font-weight: 600;
  border-left: 1px solid rgb(255 255 255 / 12%);
}

.hero h1 {
  max-width: 720px;
  margin: 0;
  font-size: clamp(55px, 5.2vw, 86px);
  font-weight: 660;
  line-height: 0.96;
  letter-spacing: -0.07em;
}

.hero h1 em {
  color: var(--signal);
  font-style: normal;
}

.hero__lede {
  max-width: 590px;
  margin: 30px 0 0;
  color: var(--ink-soft);
  font-size: clamp(16px, 1.3vw, 19px);
  line-height: 1.65;
}

.hero__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 34px;
}

.hero__proof {
  display: flex;
  flex-wrap: wrap;
  gap: 11px 22px;
  margin: 38px 0 0;
  padding: 0;
  color: var(--ink-muted);
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.05em;
  list-style: none;
  text-transform: uppercase;
}

.hero__proof li {
  display: flex;
  gap: 7px;
}

.hero__proof span {
  color: var(--cobalt-soft);
}

.hero__visual {
  position: relative;
  width: min(750px, 100%);
  min-width: 0;
}

.hero__wash {
  position: absolute;
  top: 120px;
  right: -9vw;
  width: 60vw;
  height: 700px;
  pointer-events: none;
  background: radial-gradient(circle, rgb(91 124 255 / 16%), transparent 66%);
  filter: blur(16px);
}

.proof-rail {
  color: var(--ink);
  background: var(--signal);
  border-top: 1px solid #c8ed52;
  border-bottom: 1px solid #c8ed52;
}

.proof-rail__grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
}

.proof-rail__grid > div {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-items: center;
  gap: 12px;
  min-height: 102px;
  padding: 28px 26px;
  border-left: 1px solid rgb(9 11 16 / 15%);
}

.proof-rail__grid > div:last-child {
  border-right: 1px solid rgb(9 11 16 / 15%);
}

.proof-rail strong {
  font-size: clamp(24px, 2.2vw, 30px);
  line-height: 1;
  letter-spacing: -0.06em;
}

.proof-rail span {
  max-width: 130px;
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 600;
  line-height: 1.35;
  letter-spacing: 0.03em;
  text-transform: uppercase;
}

.home-faq {
  color: var(--ink);
  background: var(--paper);
}

.home-faq__grid {
  display: grid;
  grid-template-columns: minmax(280px, 0.72fr) minmax(0, 1.28fr);
  gap: 72px;
}

.home-faq h2 {
  max-width: 540px;
  margin: 12px 0 0;
  font-size: clamp(42px, 5vw, 70px);
  line-height: 0.98;
  letter-spacing: -0.065em;
}

.home-faq__items {
  border-top: 1px solid var(--paper-border);
}

.home-faq details {
  border-bottom: 1px solid var(--paper-border);
}

.home-faq summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 24px 0;
  font-size: 19px;
  font-weight: 700;
  cursor: pointer;
  list-style: none;
}

.home-faq summary::after {
  color: var(--cobalt);
  content: '+';
  font-family: var(--font-mono);
}

.home-faq details[open] summary::after {
  content: '−';
}

.home-faq summary::-webkit-details-marker {
  display: none;
}

.home-faq details p {
  max-width: 720px;
  margin: -5px 0 24px;
  color: var(--muted-on-paper);
  font-size: 16px;
  line-height: 1.7;
}

@media (max-width: 1220px) {
  .hero {
    min-height: 0;
  }

  .hero__grid {
    grid-template-columns: 1fr;
  }

  .hero__copy {
    max-width: 760px;
  }

  .hero__visual {
    width: min(100%, 760px);
    margin: 30px auto 0;
  }
}

@media (max-width: 760px) {
  .hero {
    padding: 124px 0 66px;
  }

  .hero__grid {
    gap: 46px;
  }

  .hero h1 {
    font-size: clamp(46px, 14vw, 64px);
  }

  .hero__actions {
    align-items: stretch;
  }

  .hero__actions .button {
    flex: 1 1 100%;
  }

  .hero__proof {
    display: grid;
  }

  .proof-rail__grid {
    grid-template-columns: repeat(2, 1fr);
  }

  .proof-rail__grid > div {
    grid-template-columns: 1fr;
    align-content: center;
    gap: 5px;
    min-height: 92px;
    padding: 22px 16px;
    border-bottom: 1px solid rgb(9 11 16 / 15%);
  }

  .proof-rail strong {
    font-size: 25px;
  }

  .proof-rail span {
    font-size: 9px;
  }

  .home-faq__grid {
    grid-template-columns: 1fr;
    gap: 42px;
  }
}
</style>
