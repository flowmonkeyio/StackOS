<script setup lang="ts">
import BrandedErrorPage from '~/error.vue'

useSeoMeta({
  ogUrl: null,
})
</script>

<template>
  <BrandedErrorPage
    :error="{
      statusCode: 404,
      statusMessage: 'Page not found',
    }"
  />
</template>
