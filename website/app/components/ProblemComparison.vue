<template>
  <section id="why" class="comparison section section--paper" aria-labelledby="why-title">
    <div class="shell">
      <div v-reveal class="section-heading">
        <div>
          <p class="eyebrow">The gap after a great prompt</p>
          <h2 id="why-title">A conversation can start work.<br /><em>It cannot organize all of it.</em></h2>
        </div>
        <p>
          Real work has steps, people, approvals, apps, and loose ends. StackOS keeps them together
          after the conversation moves on.
        </p>
      </div>

      <div class="comparison-grid">
        <article v-reveal="80" class="comparison-card comparison-card--before">
          <div class="comparison-card__header">
            <span class="comparison-icon" aria-hidden="true">×</span>
            <div>
              <small>AI chat on its own</small>
              <h3>A great start.<br />A missing middle.</h3>
            </div>
          </div>
          <div class="prompt-window" aria-label="Unsafe ad hoc prompt example">
            <div><i /><i /><i /><span>agent-chat.txt</span></div>
            <code>
              <span>&gt;</span> Here is the API key…<br />
              <span>&gt;</span> Update the campaign.<br />
              <span>&gt;</span> Remember what you changed.
            </code>
          </div>
          <ul>
            <li><span>01</span>The plan disappears into a long conversation</li>
            <li><span>02</span>People cannot see what is waiting or blocked</li>
            <li><span>03</span>Each new chat rebuilds the same setup</li>
            <li><span>04</span>Results are scattered across different apps</li>
          </ul>
        </article>

        <article v-reveal="150" class="comparison-card comparison-card--after">
          <div class="comparison-card__header">
            <span class="comparison-icon" aria-hidden="true">✓</span>
            <div>
              <small>With StackOS</small>
              <h3>One clear plan.<br />Work that keeps moving.</h3>
            </div>
          </div>
          <div class="control-window" aria-label="StackOS controlled execution example">
            <div class="control-row">
              <span class="control-number">01</span>
              <strong>Work plan</strong>
              <code>Launch the fall campaign</code>
              <i class="status-pill status-pill--ready">Ready</i>
            </div>
            <div class="control-row">
              <span class="control-number">02</span>
              <strong>Current step</strong>
              <code>Create three ad directions</code>
              <i class="status-pill status-pill--running">Working</i>
            </div>
            <div class="control-row">
              <span class="control-number">03</span>
              <strong>Connected apps</strong>
              <code>Canva · Meta Ads</code>
              <i class="status-pill status-pill--ready">Ready</i>
            </div>
          </div>
          <ul>
            <li><span>01</span>Keep using Codex, Claude Code, or Gemini</li>
            <li><span>02</span>See the whole plan before work starts</li>
            <li><span>03</span>Watch each step move from waiting to done</li>
            <li><span>04</span>Saved logins stay out of the conversation</li>
          </ul>
        </article>
      </div>
    </div>
  </section>
</template>

<style scoped>
.comparison {
  overflow: hidden;
}

.comparison-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 18px;
  margin-top: 64px;
}

.comparison-card {
  position: relative;
  min-width: 0;
  padding: 34px;
  overflow: hidden;
  background: #e9e7df;
  border: 1px solid var(--paper-border);
  border-radius: 22px;
}

.comparison-card--after {
  color: var(--paper);
  background: var(--ink);
  border-color: #1d2330;
}

.comparison-card__header {
  display: flex;
  align-items: flex-start;
  gap: 18px;
}

.comparison-icon {
  display: grid;
  flex: 0 0 auto;
  width: 40px;
  height: 40px;
  place-items: center;
  color: #9a4f45;
  font-family: var(--font-mono);
  background: #f4d8d3;
  border: 1px solid #e9c4bd;
  border-radius: 50%;
}

.comparison-card--after .comparison-icon {
  color: var(--ink);
  background: var(--signal);
  border-color: var(--signal);
}

.comparison-card small {
  color: var(--muted-on-paper);
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.comparison-card--after small {
  color: var(--ink-muted);
}

.comparison-card h3 {
  margin: 8px 0 0;
  font-size: clamp(30px, 3vw, 46px);
  line-height: 1.02;
  letter-spacing: -0.055em;
}

.prompt-window,
.control-window {
  min-height: 178px;
  margin: 34px 0 28px;
  background: #dedbd1;
  border: 1px solid #ccc8bc;
  border-radius: 14px;
}

.prompt-window > div {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 11px 13px;
  border-bottom: 1px solid #ccc8bc;
}

.prompt-window i {
  width: 6px;
  height: 6px;
  background: #aaa598;
  border-radius: 50%;
}

.prompt-window div span {
  margin-left: auto;
  color: #5f5a52;
  font-family: var(--font-mono);
  font-size: 9px;
}

.prompt-window > code {
  display: block;
  padding: 24px;
  color: #56524b;
  font-size: 12px;
  line-height: 2.2;
}

.prompt-window > code span {
  color: #aa5b4f;
}

.control-window {
  display: grid;
  min-height: 178px;
  padding: 10px;
  background: #0d1017;
  border-color: #242a36;
}

.control-row {
  display: grid;
  grid-template-columns: 22px 0.8fr 1.2fr auto;
  gap: 10px;
  align-items: center;
  min-width: 0;
  padding: 10px;
  border-bottom: 1px solid rgb(255 255 255 / 7%);
}

.control-row:last-child {
  border: 0;
}

.control-number {
  color: #8790a0;
  font-family: var(--font-mono);
  font-size: 9px;
}

.control-row strong {
  font-size: 11px;
}

.control-row code {
  overflow: hidden;
  color: var(--ink-muted);
  font-size: 9px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.control-row i {
  font-size: 9px;
  font-style: normal;
}

.comparison-card ul {
  display: grid;
  margin: 0;
  padding: 0;
  list-style: none;
}

.comparison-card li {
  display: flex;
  gap: 12px;
  padding: 13px 0;
  color: #5d5a54;
  font-size: 12px;
  font-weight: 650;
  border-bottom: 1px solid #d4d0c6;
}

.comparison-card--after li {
  color: var(--ink-soft);
  border-color: rgb(255 255 255 / 7%);
}

.comparison-card li span {
  color: #676258;
  font-family: var(--font-mono);
  font-size: 9px;
}

.comparison-card--after li span {
  color: var(--cobalt-soft);
}

@media (max-width: 860px) {
  .comparison-grid {
    grid-template-columns: 1fr;
    margin-top: 42px;
  }
}

@media (max-width: 520px) {
  .comparison-card {
    padding: 24px 18px;
  }

  .control-row {
    grid-template-columns: 20px 1fr auto;
  }

  .control-row code {
    display: none;
  }
}
</style>
