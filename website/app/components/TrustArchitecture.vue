<template>
  <section id="security" class="trust section section--ink" aria-labelledby="trust-title">
    <div class="shell trust__grid">
      <div v-reveal class="trust__copy">
        <p class="eyebrow eyebrow--dark">Your saved logins stay private</p>
        <h2 id="trust-title">Your AI can use your accounts.<br /><em>It never sees the password.</em></h2>
        <p>
          StackOS keeps saved logins inside the app on your Mac. Your AI tool can ask StackOS to
          use Slack, WordPress, Shopify, or another connected app without receiving the private login.
        </p>

      </div>

      <div v-reveal="110" class="trust-visual" aria-label="How StackOS keeps saved logins private">
        <div class="trust-visual__topline">
          <span>Runs on your Mac</span>
          <code>Your private workspace</code>
        </div>

        <div class="trust-actor">
          <div class="trust-node trust-node--agent">
            <div class="trust-agent-main">
              <div class="trust-ai-logos" aria-hidden="true">
                <img src="/images/openai.webp" alt="" width="38" height="38" />
                <img src="/images/claude.webp" alt="" width="38" height="38" />
                <img src="/images/gemini.webp" alt="" width="38" height="38" />
              </div>
              <div><span>Your AI tool</span><strong>Codex / Claude / Gemini</strong></div>
            </div>
            <code>asks StackOS to use a connected app</code>
          </div>
        </div>

        <div class="trust-arrow"><span>request</span></div>

        <div class="daemon-boundary">
          <div class="daemon-boundary__label"><span>StackOS on your Mac</span><i>private workspace</i></div>
          <div class="trust-node trust-node--daemon">
            <div class="daemon-icon"><img src="/images/stackos-icon.png" alt="" /></div>
            <div>
              <span>Saved login</span>
              <strong>••••••••••••</strong>
              <code>never sent to the AI tool</code>
            </div>
            <b>PRIVATE</b>
          </div>
          <div class="daemon-steps">
            <span><i>1</i> check plan</span>
            <span><i>2</i> unlock login</span>
            <span><i>3</i> use app</span>
            <span><i>4</i> hide private data</span>
            <span><i>5</i> save result</span>
          </div>
        </div>

        <div class="trust-arrow trust-arrow--out"><span>uses the connected app</span></div>

        <div class="provider-row">
          <div class="provider-chip"><span>SL</span>Slack</div>
          <div class="provider-chip"><span>WP</span>WordPress</div>
          <div class="provider-chip"><span>SF</span>Shopify</div>
        </div>

        <div class="trust-receipt">
          <span class="status-dot" />
          <div><strong>Only the safe result comes back</strong><small>What happened and when is saved on your Mac</small></div>
          <code>DONE</code>
        </div>
      </div>

      <div class="trust-principles">
        <div>
          <span>01</span>
          <strong>Your AI decides</strong>
          <p>Codex, Claude Code, or Gemini works from your request and the current plan.</p>
        </div>
        <div>
          <span>02</span>
          <strong>StackOS checks</strong>
          <p>Only the right step can use the right connected app, and the login stays private.</p>
        </div>
        <div>
          <span>03</span>
          <strong>Your app works</strong>
          <p>Shopify, Slack, WordPress, or another tool completes the requested action.</p>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.trust {
  padding-top: 105px;
  overflow: hidden;
}

.trust__grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 48px;
  align-items: center;
}

.trust__copy {
  display: grid;
  grid-template-columns: 1.12fr 0.88fr;
  gap: 0 80px;
  align-items: end;
}

.trust__copy > .eyebrow,
.trust__copy h2 {
  grid-column: 1;
}

.trust__copy > p:not(.eyebrow) {
  grid-column: 2;
  grid-row: 1 / 3;
  align-self: end;
}

.trust__copy h2 {
  margin: 16px 0 26px;
  font-size: clamp(48px, 5.4vw, 78px);
  line-height: 0.98;
  letter-spacing: -0.065em;
}

.trust__copy h2 em {
  color: var(--signal);
  font-style: normal;
}

.trust__copy > p:not(.eyebrow) {
  max-width: 600px;
  margin: 0;
  color: var(--ink-soft);
  font-size: 15px;
  line-height: 1.75;
}

.trust-principles {
  display: grid;
  grid-column: 1 / -1;
  grid-template-columns: repeat(3, 1fr);
  gap: 22px;
  margin-top: 42px;
  border-top: 1px solid var(--border-strong);
}

.trust-principles > div {
  display: grid;
  grid-template-columns: 26px 1fr;
  gap: 12px;
  align-items: baseline;
  padding: 18px 0;
  border-bottom: 1px solid var(--border-strong);
}

.trust-principles span {
  color: var(--cobalt-soft);
  font-family: var(--font-mono);
  font-size: 9px;
}

.trust-principles strong {
  font-size: 12px;
}

.trust-principles p {
  grid-column: 2;
  margin: 0;
  color: var(--ink-muted);
  font-size: 11px;
  line-height: 1.6;
}

.trust-visual {
  width: 100%;
  padding: 30px;
  background: #0b0e14;
  border: 1px solid var(--border-strong);
  border-radius: 22px;
  box-shadow: 0 60px 110px rgb(0 0 0 / 38%);
}

.trust-visual__topline {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 2px 16px;
  color: var(--ink-muted);
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.trust-node {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 19px 21px;
  background: var(--surface-raised);
  border: 1px solid var(--border-strong);
  border-radius: 12px;
}

.trust-node span,
.trust-node strong,
.trust-node code {
  display: block;
}

.trust-node span {
  margin-bottom: 4px;
  color: var(--ink-muted);
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.07em;
  text-transform: uppercase;
}

.trust-node strong {
  font-size: 15px;
}

.trust-node code {
  color: var(--cyan);
  font-size: 11px;
}

.trust-agent-main,
.trust-ai-logos {
  display: flex;
  align-items: center;
}

.trust-agent-main {
  gap: 16px;
}

.trust-ai-logos img {
  width: 38px;
  height: 38px;
  object-fit: cover;
  border: 2px solid var(--surface-raised);
  border-radius: 10px;
}

.trust-ai-logos img + img {
  margin-left: -8px;
}

.trust-arrow {
  position: relative;
  height: 42px;
  margin-left: 50px;
  border-left: 1px dashed #3b4558;
}

.trust-arrow::after {
  position: absolute;
  bottom: -2px;
  left: -3px;
  width: 5px;
  height: 5px;
  content: '';
  background: var(--cobalt-soft);
  border-radius: 50%;
}

.trust-arrow span {
  position: absolute;
  top: 10px;
  left: 13px;
  color: #8790a0;
  font-family: var(--font-mono);
  font-size: 9px;
  text-transform: uppercase;
}

.daemon-boundary {
  padding: 14px;
  background: rgb(91 124 255 / 5%);
  border: 1px solid rgb(91 124 255 / 23%);
  border-radius: 16px;
}

.daemon-boundary__label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 5px 11px;
  color: var(--cobalt-soft);
  font-family: var(--font-mono);
  font-size: 10px;
  text-transform: uppercase;
}

.daemon-boundary__label i {
  color: #8790a0;
  font-style: normal;
}

.trust-node--daemon {
  display: grid;
  grid-template-columns: 48px 1fr auto;
  background: #10141d;
}

.daemon-icon img {
  display: block;
  width: 48px;
  height: 48px;
  border-radius: 10px;
}

.trust-node--daemon b {
  padding: 5px 7px;
  color: var(--signal);
  font-family: var(--font-mono);
  font-size: 9px;
  background: rgb(217 255 99 / 7%);
  border: 1px solid rgb(217 255 99 / 15%);
  border-radius: 6px;
}

.daemon-steps {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 5px;
  margin-top: 8px;
}

.daemon-steps span {
  padding: 11px 7px;
  color: #7b8595;
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 650;
  text-align: center;
  text-transform: uppercase;
  background: rgb(255 255 255 / 3%);
  border-radius: 6px;
}

.daemon-steps i {
  display: block;
  margin-bottom: 3px;
  color: var(--cobalt-soft);
  font-style: normal;
}

.provider-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 7px;
}

.provider-chip {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px;
  color: var(--ink-soft);
  font-size: 12px;
  background: var(--surface-raised);
  border: 1px solid var(--border-strong);
  border-radius: 9px;
}

.provider-chip span {
  display: grid;
  width: 28px;
  height: 28px;
  place-items: center;
  color: var(--paper);
  font-family: var(--font-mono);
  font-size: 8px;
  background: #272e3c;
  border-radius: 6px;
}

.trust-receipt {
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: 10px;
  align-items: center;
  margin-top: 14px;
  padding: 16px;
  background: rgb(126 226 173 / 5%);
  border: 1px solid rgb(126 226 173 / 16%);
  border-radius: 10px;
}

.trust-receipt strong,
.trust-receipt small {
  display: block;
}

.trust-receipt strong {
  font-size: 13px;
}

.trust-receipt small {
  margin-top: 3px;
  color: var(--ink-muted);
  font-family: var(--font-mono);
  font-size: 9px;
}

.trust-receipt code {
  color: #8ce7b3;
  font-size: 9px;
}

@media (max-width: 1080px) {
  .trust__copy {
    grid-template-columns: 1fr;
  }

  .trust__copy > p:not(.eyebrow) {
    grid-column: 1;
    grid-row: auto;
    margin-top: 22px;
  }
}

@media (max-width: 560px) {
  .trust__grid {
    gap: 55px;
  }

  .trust-principles {
    grid-template-columns: 1fr;
  }

  .trust-principles > div {
    grid-template-columns: 25px 1fr;
  }

  .trust-principles p {
    grid-column: 2;
  }

  .trust-visual {
    padding: 12px;
  }

  .trust-node--agent {
    align-items: stretch;
    flex-direction: column;
  }

  .trust-agent-main {
    align-items: flex-start;
    flex-direction: column;
  }

  .trust-ai-logos img {
    width: 34px;
    height: 34px;
  }

  .trust-node--daemon {
    grid-template-columns: 48px 1fr;
  }

  .trust-node--daemon b {
    grid-column: 1 / -1;
    width: fit-content;
  }

  .daemon-steps {
    grid-template-columns: repeat(3, 1fr);
  }

  .provider-row {
    grid-template-columns: 1fr;
  }
}
</style>
