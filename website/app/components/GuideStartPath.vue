<script setup lang="ts">
const steps = [
  { number: '01', label: 'Open', detail: 'StackOS app', state: 'Running' },
  { number: '02', label: 'Reopen', detail: 'Your AI tool', state: 'Connected' },
  { number: '03', label: 'Choose', detail: 'Right project', state: 'Confirmed' },
  { number: '04', label: 'Ask', detail: 'One useful job', state: 'Planned' },
  { number: '05', label: 'Review', detail: 'Start when ready', state: 'Your call' },
]
</script>

<template>
  <section class="guide-start-path" aria-labelledby="guide-start-path-title">
    <div class="guide-start-path__bar">
      <span class="guide-start-path__lights" aria-hidden="true"><i /><i /><i /></span>
      <code>your-first-session</code>
      <span>About 10 min</span>
    </div>

    <div class="guide-start-path__heading">
      <div>
        <span>What happens next</span>
        <h2 id="guide-start-path-title">From installed to one useful job.</h2>
      </div>
      <strong>~10 min</strong>
    </div>

    <ol>
      <li v-for="step in steps" :key="step.number">
        <div class="guide-start-path__node"><span>{{ step.number }}</span></div>
        <div class="guide-start-path__card">
          <small>{{ step.detail }}</small>
          <b>{{ step.label }}</b>
          <code><i />{{ step.state }}</code>
        </div>
      </li>
    </ol>

    <div class="guide-start-path__receipt">
      <span class="status-dot" />
      <div><strong>The work stays with the project</strong><small>You can come back, see what happened, and continue.</small></div>
      <code>KEPT ORGANIZED</code>
    </div>
  </section>
</template>

<style scoped>
.guide-start-path {
  width: min(860px, calc(100vw - 48px));
  margin: 30px 50% 50px;
  padding: 14px;
  color: var(--paper);
  background: #0b0e14;
  border: 1px solid rgb(255 255 255 / 11%);
  border-radius: 22px;
  box-shadow: 0 54px 100px rgb(0 0 0 / 38%);
  transform: translateX(-50%);
}

.guide-start-path__bar {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  padding: 7px 8px 15px;
  color: #778091;
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: .08em;
  text-transform: uppercase;
}

.guide-start-path__bar > span:last-child { justify-self: end; }

.guide-start-path__lights { display: flex; gap: 5px; }
.guide-start-path__lights i { width: 6px; height: 6px; background: #343b49; border-radius: 50%; }
.guide-start-path__lights i:first-child { background: var(--cobalt-soft); }

.guide-start-path__heading {
  display: flex;
  align-items: start;
  justify-content: space-between;
  gap: 24px;
  padding: 23px 23px 25px;
  background: #11151e;
  border: 1px solid rgb(255 255 255 / 8%);
  border-radius: 13px 13px 4px 4px;
}

.guide-start-path__heading span {
  display: block;
  margin-bottom: 8px;
  color: var(--cobalt-soft);
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: .1em;
  text-transform: uppercase;
}

.guide-start-path__heading h2 {
  max-width: 430px;
  margin: 0;
  padding: 0;
  color: var(--paper);
  font-size: clamp(21px, 2vw, 29px);
  font-weight: 620;
  line-height: 1.12;
  letter-spacing: -.04em;
  border: 0;
}

.guide-start-path__heading > strong {
  flex: 0 0 auto;
  padding: 7px 9px;
  color: var(--signal);
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: .06em;
  text-transform: uppercase;
  background: rgb(217 255 99 / 7%);
  border: 1px solid rgb(217 255 99 / 17%);
  border-radius: 7px;
}

.guide-start-path ol {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  margin: 0;
  padding: 33px 12px 20px;
  list-style: none;
}

.guide-start-path li { position: relative; min-width: 0; }
.guide-start-path li + li { margin-top: 0; }

.guide-start-path li::after {
  position: absolute;
  top: 13px;
  left: calc(50% + 13px);
  width: calc(100% - 26px);
  height: 1px;
  content: '';
  background: linear-gradient(90deg, var(--cobalt-soft), #313a4a);
}

.guide-start-path li:last-child::after { display: none; }

.guide-start-path__node {
  position: relative;
  z-index: 1;
  display: grid;
  width: 27px;
  height: 27px;
  margin: 0 auto 12px;
  place-items: center;
  color: var(--paper);
  font-family: var(--font-mono);
  font-size: 9px;
  background: #1d2638;
  border: 1px solid var(--cobalt-soft);
  border-radius: 50%;
  box-shadow: 0 0 0 6px #0b0e14;
}

.guide-start-path li:last-child .guide-start-path__node {
  color: var(--ink);
  background: var(--signal);
  border-color: var(--signal);
}

.guide-start-path__card {
  display: grid;
  min-height: 96px;
  margin: 0 4px;
  padding: 11px;
  background: rgb(255 255 255 / 3%);
  border: 1px solid rgb(255 255 255 / 7%);
  border-radius: 9px;
}

.guide-start-path__card small {
  overflow: hidden;
  color: #768091;
  font-family: var(--font-mono);
  font-size: 9px;
  text-overflow: ellipsis;
  text-transform: uppercase;
  white-space: nowrap;
}

.guide-start-path__card b { align-self: center; font-size: 13px; }

.guide-start-path li .guide-start-path__card code {
  display: flex;
  gap: 5px;
  align-items: center;
  align-self: end;
  padding: 0;
  color: #86dca9;
  font-size: 9px;
  text-transform: uppercase;
  background: transparent;
  border-radius: 0;
}

.guide-start-path__card code i {
  width: 5px;
  height: 5px;
  background: #7ee2ad;
  border-radius: 50%;
}

.guide-start-path__receipt {
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: 11px;
  align-items: center;
  margin: 5px;
  padding: 15px 16px;
  background: rgb(126 226 173 / 5%);
  border: 1px solid rgb(126 226 173 / 15%);
  border-radius: 10px;
}

.guide-start-path__receipt strong,
.guide-start-path__receipt small { display: block; }
.guide-start-path__receipt strong { color: var(--paper); font-size: 13px; }
.guide-start-path__receipt small { margin-top: 2px; color: #9aa3b2; font-size: 10px; }
.guide-start-path__receipt code { color: #8ce7b3; font-size: 9px; }

@media (max-width: 700px) {
  .guide-start-path ol { grid-template-columns: 1fr; gap: 0; padding: 24px 10px; }
  .guide-start-path li { display: grid; grid-template-columns: 31px 1fr; gap: 11px; }
  .guide-start-path li::after { top: 27px; left: 15px; width: 1px; height: calc(100% - 3px); background: #364155; }
  .guide-start-path__node { margin: 0 auto; }
  .guide-start-path__card { min-height: 72px; margin: 0 0 11px; }
  .guide-start-path__receipt { grid-template-columns: auto 1fr; }
  .guide-start-path__receipt > code { grid-column: 2; }
}
</style>
