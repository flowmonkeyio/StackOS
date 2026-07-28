<script setup lang="ts">
const config = useRuntimeConfig()
const siteUrl = String(config.public.siteUrl).replace(/\/+$/, '')

useSchemaOrg([
  defineOrganization({
    '@id': `${siteUrl}/#identity`,
    name: 'StackOS',
    url: `${siteUrl}/`,
    description: 'The local operating layer for clear, connected, and accountable AI-powered work.',
    image: `${siteUrl}/images/stackos-icon.png`,
    sameAs: ['https://github.com/flowmonkeyio/StackOS'],
  }),
])
</script>

<template>
  <NuxtPage />
  <AnalyticsConsent />
</template>
