<script setup lang="ts">
const downloadUrl = useDownloadUrl()
</script>

<template>
  <footer class="site-footer">
    <div class="shell site-footer__grid">
      <NuxtLink class="footer-brand" to="/">
        <img src="/images/stackos-icon.png" alt="" width="42" height="42" loading="lazy" decoding="async" />
        <div><strong>StackOS</strong><span>Keep AI-powered work clear, connected, and moving.</span></div>
      </NuxtLink>

      <div class="site-footer__links">
        <div>
          <span>Explore</span>
          <NuxtLink to="/#why">Why StackOS</NuxtLink>
          <NuxtLink to="/getting-started">Getting started</NuxtLink>
          <NuxtLink to="/library">Library</NuxtLink>
          <NuxtLink to="/library/workflows">Workflows</NuxtLink>
          <NuxtLink to="/library/articles">Articles</NuxtLink>
        </div>
        <div>
          <span>Build</span>
          <NuxtLink to="/#security">Security</NuxtLink>
          <a :href="downloadUrl" data-download="stackos-mac">Download</a>
          <a href="https://github.com/flowmonkeyio/StackOS" target="_blank" rel="noreferrer">GitHub ↗</a>
        </div>
      </div>

      <div class="site-footer__status">
        <span class="status-dot" />
        <div><strong>Runs on your Mac</strong><small>Your work. Your apps. Your private logins.</small></div>
      </div>
    </div>
    <div class="shell site-footer__bottom">
      <span>StackOS · Your AI tool works. StackOS keeps the work organized.</span>
      <span>Runs locally.</span>
    </div>
  </footer>
</template>

<style scoped>
.site-footer {
  padding: 64px 0 24px;
  color: var(--paper);
  background: var(--ink);
  border-top: 1px solid rgb(255 255 255 / 8%);
}

.site-footer__grid {
  display: grid;
  grid-template-columns: 1.2fr 1fr 0.8fr;
  gap: 60px;
}

.footer-brand {
  display: flex;
  align-items: center;
  gap: 13px;
  color: var(--paper);
  text-decoration: none;
}

.footer-brand img {
  border-radius: 12px;
}

.footer-brand strong,
.footer-brand span {
  display: block;
}

.footer-brand strong {
  margin-bottom: 4px;
  font-size: 14px;
}

.footer-brand span {
  color: var(--ink-muted);
  font-size: 13px;
}

.site-footer__links {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 30px;
}

.site-footer__links div {
  display: grid;
  align-content: start;
  gap: 10px;
}

.site-footer__links span {
  margin-bottom: 4px;
  color: var(--ink-muted);
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.site-footer__links a {
  color: var(--ink-soft);
  font-size: 13px;
  text-decoration: none;
}

.site-footer__links a:hover {
  color: var(--paper);
}

.site-footer__status {
  display: flex;
  align-items: center;
  gap: 10px;
  align-self: start;
  padding: 13px;
  background: rgb(255 255 255 / 3%);
  border: 1px solid var(--border-strong);
  border-radius: 10px;
}

.site-footer__status strong,
.site-footer__status small {
  display: block;
}

.site-footer__status strong {
  font-size: 12px;
}

.site-footer__status small {
  margin-top: 3px;
  color: var(--ink-muted);
  font-size: 11px;
}

.site-footer__bottom {
  display: flex;
  justify-content: space-between;
  margin-top: 62px;
  padding-top: 20px;
  color: #8790a0;
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  border-top: 1px solid rgb(255 255 255 / 7%);
}

@media (max-width: 760px) {
  .site-footer__grid {
    grid-template-columns: 1fr;
  }

  .site-footer__status {
    width: fit-content;
  }
}
</style>
