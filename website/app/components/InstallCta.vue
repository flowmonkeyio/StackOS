<script setup lang="ts">
import { useDownloadVersion } from '~/composables/useDownloadVersion'

const downloadUrl = useDownloadUrl()
const downloadVersion = useDownloadVersion()
</script>

<template>
  <section id="install" class="install section section--signal" aria-labelledby="install-title">
    <div class="shell install__grid">
      <div v-reveal>
        <p class="eyebrow">Start with the tools you already use</p>
        <h2 id="install-title">Bring the next job.<br /><em>StackOS brings the plan.</em></h2>
      </div>
      <div v-reveal="100" class="install__action">
        <p>
          Install StackOS on your Mac, connect your AI tool, then add the business apps you
          already rely on. Your first workflow can start from the same conversation you use today.
        </p>
        <a
          class="download-card"
          :href="downloadUrl"
          data-download="stackos-mac"
        >
          <span class="download-card__icon" aria-hidden="true">
            <img src="/images/apple.webp" alt="" width="28" height="28" />
          </span>
          <span>
            <small>StackOS {{ downloadVersion }} · Apple silicon</small>
            <strong>Download for Mac</strong>
          </span>
          <b>DMG</b>
        </a>
        <div class="install__links">
          <a class="button button--ink" href="#workflow">
            See the workflow again <span aria-hidden="true">↑</span>
          </a>
          <a href="https://github.com/flowmonkeyio/StackOS" target="_blank" rel="noreferrer">
            View on GitHub <span aria-hidden="true">↗</span>
          </a>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.install {
  color: var(--ink);
  background: var(--signal);
}

.install__grid {
  display: grid;
  grid-template-columns: 1.08fr 0.92fr;
  gap: 100px;
  align-items: end;
}

.install h2 {
  margin: 16px 0 0;
  font-size: clamp(56px, 7.5vw, 108px);
  line-height: 0.88;
  letter-spacing: -0.075em;
}

.install h2 em {
  color: transparent;
  font-style: normal;
  -webkit-text-stroke: 1.5px var(--ink);
}

.install__action > p {
  max-width: 550px;
  margin: 0 0 26px;
  font-size: 15px;
  line-height: 1.7;
}

.download-card {
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: 15px;
  align-items: center;
  min-height: 94px;
  padding: 15px 18px;
  color: var(--paper);
  text-decoration: none;
  background: var(--ink);
  border: 1px solid #1b202b;
  border-radius: 15px;
  box-shadow: 0 25px 55px rgb(75 89 20 / 15%);
  transition: transform 180ms ease, box-shadow 180ms ease;
}

.download-card:hover {
  box-shadow: 0 30px 65px rgb(75 89 20 / 22%);
  transform: translateY(-2px);
}

.download-card__icon {
  display: grid;
  width: 46px;
  height: 46px;
  place-items: center;
  color: var(--ink);
  background: var(--signal);
  border-radius: 12px;
}

.download-card__icon img {
  width: 27px;
  height: 27px;
  object-fit: cover;
  border-radius: 7px;
  filter: invert(1);
}

.download-card small,
.download-card strong {
  display: block;
}

.download-card small {
  margin-bottom: 5px;
  color: var(--ink-muted);
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}

.download-card strong {
  font-size: 18px;
  letter-spacing: -0.03em;
}

.download-card b {
  color: var(--signal);
  font-family: var(--font-mono);
  font-size: 10px;
}

.install__links {
  display: flex;
  flex-wrap: wrap;
  gap: 18px;
  align-items: center;
  margin-top: 18px;
}

.install__links > a:not(.button) {
  color: var(--ink);
  font-size: 11px;
  font-weight: 720;
  text-decoration: none;
  border-bottom: 1px solid rgb(9 11 16 / 40%);
}

@media (max-width: 900px) {
  .install__grid {
    grid-template-columns: 1fr;
    gap: 60px;
  }
}

@media (max-width: 520px) {
  .install h2 {
    font-size: 17vw;
  }

  .install__links .button {
    width: 100%;
  }
}
</style>
