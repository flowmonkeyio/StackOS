<script setup lang="ts">
type ClientPath = 'folder' | 'desktop'

const selectedPath = ref<ClientPath>('folder')
const isInteractive = ref(false)

onMounted(() => {
  isInteractive.value = true
})
</script>

<template>
  <section class="guide-client-paths" aria-labelledby="guide-client-paths-title">
    <header>
      <p>Choose the way you normally work</p>
      <h3 id="guide-client-paths-title">Your AI tool just needs to know which project you mean.</h3>
    </header>

    <div class="guide-client-paths__source">
      <img src="/images/stackos-icon.png" alt="" width="42" height="42" />
      <div><span>StackOS connection</span><strong>Ready on this Mac</strong></div>
      <code>READY</code>
    </div>

    <div class="guide-client-paths__fork" aria-hidden="true"><i /><i /><i /></div>

    <div class="guide-client-paths__picker">
      <p>How do you open your AI tool?</p>
      <div role="group" aria-label="Choose how you open your AI tool">
        <button
          type="button"
          :disabled="!isInteractive"
          :class="{ 'is-active': selectedPath === 'folder' }"
          :aria-pressed="selectedPath === 'folder'"
          aria-controls="guide-client-path-folder"
          @click="selectedPath = 'folder'"
        >
          <span class="guide-client-paths__picker-logos" aria-hidden="true">
            <img src="/images/openai.webp" alt="" width="22" height="22" />
            <img src="/images/claude.webp" alt="" width="22" height="22" />
            <img src="/images/gemini.webp" alt="" width="22" height="22" />
          </span>
          <strong>From a project folder</strong>
        </button>
        <button
          type="button"
          :disabled="!isInteractive"
          :class="{ 'is-active': selectedPath === 'desktop' }"
          :aria-pressed="selectedPath === 'desktop'"
          aria-controls="guide-client-path-desktop"
          @click="selectedPath = 'desktop'"
        >
          <img src="/images/claude.webp" alt="" width="26" height="26" />
          <strong>Claude Desktop</strong>
        </button>
      </div>
    </div>

    <div class="guide-client-paths__routes">
      <article id="guide-client-path-folder" :class="{ 'is-active': selectedPath === 'folder' }">
        <div class="guide-client-paths__route-head">
          <div class="guide-client-paths__logos" aria-hidden="true">
            <img src="/images/openai.webp" alt="" width="34" height="34" />
            <img src="/images/claude.webp" alt="" width="34" height="34" />
            <img src="/images/gemini.webp" alt="" width="34" height="34" />
          </div>
          <div><span>Work from a project folder</span><h4>Codex CLI, Claude Code, Gemini CLI</h4></div>
        </div>
        <ol>
          <li><i>1</i><span>Open the real project folder</span></li>
          <li><i>2</i><span>Start a fresh AI session there</span></li>
          <li><i>3</i><span>Ask it to use StackOS for this project</span></li>
        </ol>
        <footer><span class="status-dot" /><strong>StackOS remembers the folder</strong></footer>
      </article>

      <article id="guide-client-path-desktop" :class="{ 'is-active': selectedPath === 'desktop' }">
        <div class="guide-client-paths__route-head">
          <img class="guide-client-paths__desktop-logo" src="/images/claude.webp" alt="" width="42" height="42" />
          <div><span>Work without a project folder</span><h4>Claude Desktop</h4></div>
        </div>
        <ol>
          <li><i>1</i><span>Ask to see your StackOS projects</span></li>
          <li><i>2</i><span>Choose the project by name</span></li>
          <li><i>3</i><span>Ask Claude to confirm before starting</span></li>
        </ol>
        <footer><span class="status-dot" /><strong>You stay in control of the choice</strong></footer>
      </article>
    </div>
  </section>
</template>

<style scoped>
.guide-client-paths {
  width: min(960px, calc(100vw - 48px));
  margin: 48px 50%;
  padding: 30px;
  color: var(--paper);
  background: var(--ink);
  border: 1px solid #1d2330;
  border-radius: 20px;
  box-shadow: 0 28px 70px rgb(9 11 16 / 16%);
  transform: translateX(-50%);
}

.guide-client-paths header {
  display: grid;
  grid-template-columns: .7fr 1.3fr;
  gap: 40px;
  align-items: end;
  margin-bottom: 28px;
}

.guide-client-paths header p {
  margin: 0;
  color: var(--cobalt-soft);
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 650;
  letter-spacing: .1em;
  text-transform: uppercase;
}

.guide-client-paths h3 {
  margin: 0;
  color: var(--paper);
  font-size: clamp(25px, 3vw, 38px);
  line-height: 1.08;
  letter-spacing: -.045em;
}

.guide-client-paths__source {
  display: grid;
  grid-template-columns: 42px 1fr auto;
  gap: 13px;
  align-items: center;
  width: min(520px, 100%);
  margin: 0 auto;
  padding: 15px;
  background: #121722;
  border: 1px solid rgb(120 146 255 / 28%);
  border-radius: 12px;
}

.guide-client-paths__source img { border-radius: 9px; }
.guide-client-paths__source span,
.guide-client-paths__source strong { display: block; }
.guide-client-paths__source span { margin-bottom: 3px; color: #929bab; font-family: var(--font-mono); font-size: 10px; text-transform: uppercase; }
.guide-client-paths__source strong { color: var(--paper); font-size: 14px; }
.guide-client-paths__source > code { color: var(--signal); font-size: 10px; }

.guide-client-paths__fork { position: relative; width: 50%; height: 55px; margin: 0 auto; }
.guide-client-paths__fork i:first-child { position: absolute; top: 0; left: 50%; width: 1px; height: 23px; background: #3b4558; }
.guide-client-paths__fork i:nth-child(2) { position: absolute; top: 22px; left: 0; width: 100%; height: 1px; background: #3b4558; }
.guide-client-paths__fork i:last-child { position: absolute; top: 22px; right: 0; left: 0; height: 33px; border-right: 1px solid #3b4558; border-left: 1px solid #3b4558; }

.guide-client-paths__picker { display: none; }

.guide-client-paths__routes { display: grid; grid-template-columns: repeat(2, 1fr); gap: 18px; }
.guide-client-paths__routes article { padding: 20px; background: #10141d; border: 1px solid rgb(255 255 255 / 9%); border-radius: 13px; }
.guide-client-paths__route-head { display: flex; gap: 13px; align-items: center; min-height: 46px; }
.guide-client-paths__route-head span { display: block; margin-bottom: 4px; color: var(--cobalt-soft); font-family: var(--font-mono); font-size: 9px; letter-spacing: .08em; text-transform: uppercase; }
.guide-client-paths h4 { margin: 0; color: var(--paper); font-size: 14px; line-height: 1.35; }
.guide-client-paths__logos { display: flex; flex: 0 0 auto; }
.guide-client-paths__logos img { width: 34px; height: 34px; object-fit: cover; border: 2px solid #10141d; border-radius: 8px; }
.guide-client-paths__logos img + img { margin-left: -8px; }
.guide-client-paths__desktop-logo { flex: 0 0 auto; width: 42px; height: 42px; object-fit: cover; border-radius: 9px; }

.guide-client-paths ol { display: grid; gap: 7px; margin: 20px 0; padding: 0; list-style: none; }
.guide-client-paths li { display: grid; grid-template-columns: 24px 1fr; gap: 9px; align-items: center; padding: 9px; color: #c2c8d2; font-size: 12px; line-height: 1.45; background: rgb(255 255 255 / 3%); border-radius: 7px; }
.guide-client-paths li + li { margin-top: 0; }
.guide-client-paths li i { display: grid; width: 22px; height: 22px; place-items: center; color: var(--cobalt-soft); font-family: var(--font-mono); font-size: 9px; font-style: normal; background: rgb(120 146 255 / 9%); border-radius: 5px; }
.guide-client-paths li code { color: var(--cyan); font-size: 10px; }
.guide-client-paths footer { display: flex; gap: 9px; align-items: center; padding-top: 14px; color: #8de4b2; font-family: var(--font-mono); font-size: 10px; border-top: 1px solid rgb(255 255 255 / 7%); text-transform: uppercase; }
.guide-client-paths footer strong { color: inherit; }

@media (max-width: 760px) {
  .guide-client-paths { padding: 18px; }
  .guide-client-paths header { grid-template-columns: 1fr; gap: 10px; }
  .guide-client-paths__routes { grid-template-columns: 1fr; }
  .guide-client-paths__fork { display: none; }
  .guide-client-paths__picker { display: block; margin: 18px 0; }
  .guide-client-paths__picker > p { margin: 0 0 9px; color: #aeb6c4; font-size: 12px; font-weight: 650; }
  .guide-client-paths__picker > div { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; padding: 5px; background: #111620; border: 1px solid rgb(255 255 255 / 8%); border-radius: 12px; }
  .guide-client-paths__picker button { display: grid; min-width: 0; min-height: 78px; align-content: center; justify-items: center; gap: 8px; padding: 10px 7px; color: #929bab; background: transparent; border: 1px solid transparent; border-radius: 8px; cursor: pointer; }
  .guide-client-paths__picker button.is-active { color: var(--paper); background: #1a2030; border-color: rgb(120 146 255 / 35%); box-shadow: 0 7px 18px rgb(0 0 0 / 18%); }
  .guide-client-paths__picker button > img { width: 26px; height: 26px; object-fit: cover; border-radius: 7px; }
  .guide-client-paths__picker button strong { color: inherit; font-size: 11px; line-height: 1.3; text-align: center; }
  .guide-client-paths__picker-logos { display: flex; justify-content: center; }
  .guide-client-paths__picker-logos img { width: 22px; height: 22px; object-fit: cover; border: 2px solid #111620; border-radius: 6px; }
  .guide-client-paths__picker-logos img + img { margin-left: -5px; }
  .guide-client-paths__picker button.is-active .guide-client-paths__picker-logos img { border-color: #1a2030; }
  .guide-client-paths__routes article { display: none; }
  .guide-client-paths__routes article.is-active { display: block; }
}
</style>
