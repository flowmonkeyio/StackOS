<template>
  <section class="guide-system-map" aria-labelledby="guide-system-map-title">
    <header>
      <div><p>Private by design</p><h3 id="guide-system-map-title">Your request moves. Your login stays on your Mac.</h3></div>
      <span>HOW A CONNECTION WORKS</span>
    </header>

    <div class="guide-system-map__flow">
      <div class="guide-system-map__node guide-system-map__node--client">
        <div class="guide-system-map__logos" aria-hidden="true">
          <img src="/images/openai.webp" alt="" width="32" height="32" />
          <img src="/images/claude.webp" alt="" width="32" height="32" />
          <img src="/images/gemini.webp" alt="" width="32" height="32" />
        </div>
        <span>Your AI tool</span><strong>Asks for the work</strong><code>NO PRIVATE LOGIN</code>
      </div>

      <div class="guide-system-map__arrow"><span>request</span></div>

      <div class="guide-system-map__boundary">
        <div class="guide-system-map__boundary-label"><span>StackOS on your Mac</span><i>private boundary</i></div>
        <div class="guide-system-map__local-row">
          <div class="guide-system-map__node">
            <img src="/images/stackos-icon.png" alt="" width="38" height="38" />
            <span>Your project</span><strong>Checks the plan</strong><code>RIGHT STEP</code>
          </div>
          <div class="guide-system-map__mini-arrow" aria-hidden="true">→</div>
          <div class="guide-system-map__node guide-system-map__node--secret">
            <div class="guide-system-map__lock" aria-hidden="true">•••</div>
            <span>Connected app</span><strong>Uses the saved login</strong><code>STAYS PRIVATE</code>
          </div>
        </div>
      </div>

      <div class="guide-system-map__arrow"><span>planned action</span></div>

      <div class="guide-system-map__providers" aria-label="Example connected services">
        <span><img src="/images/integrations/slack-icon.png" alt="" width="22" height="22" /><small>Slack</small></span>
        <span><img src="/images/integrations/shopify-icon.png" alt="" width="22" height="22" /><small>Shopify</small></span>
        <span><img src="/images/integrations/wordpress-icon.png" alt="" width="22" height="22" /><small>WordPress</small></span>
      </div>
    </div>

    <footer>
      <span class="status-dot" />
      <div><strong>Only the safe result comes back</strong><small>StackOS keeps a clear record of what happened.</small></div>
      <code>RESULT SAVED</code>
    </footer>
  </section>
</template>

<style scoped>
.guide-system-map {
  width: min(960px, calc(100vw - 48px));
  margin: 48px 50%;
  padding: 30px;
  color: var(--paper);
  background: #0b0e14;
  border: 1px solid #1d2330;
  border-radius: 20px;
  box-shadow: 0 28px 70px rgb(9 11 16 / 16%);
  transform: translateX(-50%);
}

.guide-system-map header { display: flex; justify-content: space-between; gap: 30px; align-items: start; margin-bottom: 28px; }
.guide-system-map header p { margin: 0 0 8px; color: var(--cobalt-soft); font-family: var(--font-mono); font-size: 10px; letter-spacing: .1em; text-transform: uppercase; }
.guide-system-map h3 { max-width: 640px; margin: 0; color: var(--paper); font-size: clamp(27px, 3.3vw, 42px); line-height: 1.05; letter-spacing: -.05em; }
.guide-system-map header > span { flex: 0 0 auto; padding: 7px 9px; color: #929bab; font-family: var(--font-mono); font-size: 9px; border: 1px solid rgb(255 255 255 / 9%); border-radius: 6px; }

.guide-system-map__flow { display: grid; grid-template-columns: minmax(145px, .8fr) 54px minmax(340px, 1.8fr) 54px minmax(120px, .7fr); align-items: center; }
.guide-system-map__node { display: grid; grid-template-columns: 42px 1fr; gap: 2px 11px; align-items: center; min-width: 0; padding: 14px; background: #141923; border: 1px solid rgb(255 255 255 / 9%); border-radius: 10px; }
.guide-system-map__node > img,
.guide-system-map__lock { grid-row: 1 / 4; }
.guide-system-map__node span { color: #929bab; font-family: var(--font-mono); font-size: 9px; letter-spacing: .06em; text-transform: uppercase; }
.guide-system-map__node strong { overflow: hidden; color: var(--paper); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.guide-system-map__node code { color: #8ce7b3; font-size: 9px; }
.guide-system-map__node--client { display: block; text-align: center; }
.guide-system-map__node--client span,
.guide-system-map__node--client strong,
.guide-system-map__node--client code { display: block; }
.guide-system-map__node--client span { margin-top: 10px; }
.guide-system-map__node--client strong { margin: 4px 0 8px; }
.guide-system-map__logos { display: flex; justify-content: center; }
.guide-system-map__logos img { width: 32px; height: 32px; object-fit: cover; border: 2px solid #141923; border-radius: 8px; }
.guide-system-map__logos img + img { margin-left: -7px; }

.guide-system-map__arrow { position: relative; height: 1px; background: #3a4353; }
.guide-system-map__arrow::after { position: absolute; top: -2px; right: 0; width: 5px; height: 5px; content: ''; background: var(--cobalt-soft); border-radius: 50%; }
.guide-system-map__arrow span { position: absolute; bottom: 9px; left: 50%; color: #8c95a5; font-family: var(--font-mono); font-size: 8px; text-transform: uppercase; white-space: nowrap; transform: translateX(-50%); }

.guide-system-map__boundary { padding: 12px; background: rgb(91 124 255 / 5%); border: 1px solid rgb(91 124 255 / 24%); border-radius: 14px; }
.guide-system-map__boundary-label { display: flex; justify-content: space-between; padding: 2px 3px 10px; color: var(--cobalt-soft); font-family: var(--font-mono); font-size: 9px; text-transform: uppercase; }
.guide-system-map__boundary-label i { color: #7c8594; font-style: normal; }
.guide-system-map__local-row { display: grid; grid-template-columns: 1fr 24px 1fr; align-items: center; }
.guide-system-map__mini-arrow { color: #677184; text-align: center; }
.guide-system-map__lock { display: grid; width: 42px; height: 42px; place-items: center; color: var(--signal); font-family: var(--font-mono); font-size: 11px; letter-spacing: 2px; background: rgb(217 255 99 / 7%); border: 1px solid rgb(217 255 99 / 15%); border-radius: 8px; }
.guide-system-map__node--secret code { color: var(--signal); }

.guide-system-map__providers { display: grid; gap: 6px; }
.guide-system-map__providers > span { display: grid; grid-template-columns: 28px 1fr; gap: 8px; align-items: center; padding: 8px; color: #9da7b6; font-family: var(--font-mono); font-size: 8px; background: #141923; border: 1px solid rgb(255 255 255 / 8%); border-radius: 8px; }
.guide-system-map__providers img { width: 22px; height: 22px; object-fit: contain; }
.guide-system-map__providers small { font-family: var(--font-sans); font-size: 11px; }

.guide-system-map footer { display: grid; grid-template-columns: auto 1fr auto; gap: 11px; align-items: center; margin-top: 18px; padding: 15px; background: rgb(126 226 173 / 5%); border: 1px solid rgb(126 226 173 / 15%); border-radius: 10px; }
.guide-system-map footer strong,
.guide-system-map footer small { display: block; }
.guide-system-map footer strong { color: var(--paper); font-size: 13px; }
.guide-system-map footer small { margin-top: 3px; color: #9aa3b2; font-size: 10px; }
.guide-system-map footer code { color: #8ce7b3; font-size: 9px; }

@media (max-width: 900px) {
  .guide-system-map__flow { grid-template-columns: 1fr; gap: 0; }
  .guide-system-map__arrow { width: 1px; height: 36px; margin: 0 auto; }
  .guide-system-map__arrow span { top: 50%; bottom: auto; left: 12px; transform: translateY(-50%); }
  .guide-system-map__arrow::after { top: auto; right: -2px; bottom: 0; }
  .guide-system-map__providers { grid-template-columns: repeat(3, 1fr); }
}

@media (max-width: 620px) {
  .guide-system-map { padding: 18px; }
  .guide-system-map header { display: block; }
  .guide-system-map header > span { display: inline-block; margin-top: 14px; }
  .guide-system-map__local-row { grid-template-columns: 1fr; gap: 7px; }
  .guide-system-map__mini-arrow { transform: rotate(90deg); }
  .guide-system-map__providers { grid-template-columns: 1fr; }
  .guide-system-map footer { grid-template-columns: auto 1fr; }
  .guide-system-map footer > code { grid-column: 2; }
}
</style>
