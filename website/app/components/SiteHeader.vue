<script setup lang="ts">
const mobileMenu = ref<HTMLDetailsElement | null>(null)
const downloadUrl = useDownloadUrl()

function closeMenu() {
  mobileMenu.value?.removeAttribute('open')
}
</script>

<template>
  <header class="site-header">
    <div class="shell site-header__inner">
      <NuxtLink class="brand" to="/" aria-label="StackOS home" @click="closeMenu">
        <img src="/images/stackos-icon.png" alt="" width="36" height="36" />
        <span>StackOS</span>
        <small>Work, organized</small>
      </NuxtLink>

      <nav class="site-nav site-nav--desktop" aria-label="Primary">
        <NuxtLink to="/#why" @click="closeMenu">Why StackOS</NuxtLink>
        <NuxtLink to="/#workflow" @click="closeMenu">Workflows</NuxtLink>
        <NuxtLink to="/#proof" @click="closeMenu">Product</NuxtLink>
        <NuxtLink to="/getting-started" @click="closeMenu">Get started</NuxtLink>
        <NuxtLink to="/library" @click="closeMenu">Library</NuxtLink>
        <NuxtLink to="/#security" @click="closeMenu">Security</NuxtLink>
        <a
          class="button button--header"
          :href="downloadUrl"
          data-download="stackos-mac"
          @click="closeMenu"
        >
          <img class="button__logo button__logo--apple" src="/images/apple.webp" alt="" width="18" height="18" />
          Download for Mac
          <span aria-hidden="true">↓</span>
        </a>
      </nav>

      <details ref="mobileMenu" class="mobile-menu">
        <summary role="button" aria-label="Toggle navigation" aria-controls="site-navigation">
          <span />
          <span />
        </summary>
        <nav id="site-navigation" class="site-nav site-nav--mobile" aria-label="Primary">
          <NuxtLink to="/#why" @click="closeMenu">Why StackOS</NuxtLink>
          <NuxtLink to="/#workflow" @click="closeMenu">Workflows</NuxtLink>
          <NuxtLink to="/#proof" @click="closeMenu">Product</NuxtLink>
          <NuxtLink to="/getting-started" @click="closeMenu">Get started</NuxtLink>
          <NuxtLink to="/library" @click="closeMenu">Library</NuxtLink>
          <NuxtLink to="/#security" @click="closeMenu">Security</NuxtLink>
          <a
            class="button button--header"
            :href="downloadUrl"
            data-download="stackos-mac"
            @click="closeMenu"
          >
            <img class="button__logo button__logo--apple" src="/images/apple.webp" alt="" width="18" height="18" />
            Download for Mac
            <span aria-hidden="true">↓</span>
          </a>
        </nav>
      </details>
    </div>
  </header>
</template>
