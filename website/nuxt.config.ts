function requiredPublicEnv(name: string) {
  const value = process.env[name]
  if (!value) throw new Error(`${name} is required`)
  return value
}

const siteUrl = requiredPublicEnv('NUXT_PUBLIC_SITE_URL')
const gaMeasurementId = requiredPublicEnv('NUXT_PUBLIC_GA_MEASUREMENT_ID')
const downloadUrl = 'https://stackos.flowmonkey.io/StackOS/stackos-latest-mac-arm64.dmg'

export default defineNuxtConfig({
  compatibilityDate: '2026-07-09',
  devtools: { enabled: false },
  features: {
    inlineStyles: true,
  },
  modules: ['@nuxt/content', '@nuxt/image', '@nuxtjs/seo', '@nuxt/scripts'],
  site: {
    url: siteUrl,
    name: 'StackOS',
    description: 'The place where AI-powered work becomes clear, connected, and accountable.',
    defaultLocale: 'en',
  },
  runtimeConfig: {
    public: {
      siteUrl,
      gaMeasurementId,
      downloadUrl,
    },
  },
  scripts: {
    registry: {
      googleAnalytics: {
        id: gaMeasurementId,
        // Hostinger serves a static export, so collection goes directly to GA.
        bundle: false,
      },
    },
  },
  css: [
    '@vue-flow/core/dist/style.css',
    '@vue-flow/core/dist/theme-default.css',
    '~/assets/css/main.css',
    '~/assets/css/library.css',
  ],
  app: {
    head: {
      htmlAttrs: { lang: 'en' },
      title: 'StackOS — The local operating layer for AI agents',
      meta: [
        {
          name: 'description',
          content:
            'Connect your tools once. Let AI agents run real business work through local, credential-safe workflows with durable state and audit.',
        },
        { name: 'theme-color', content: '#090b10' },
        { property: 'og:type', content: 'website' },
        { property: 'og:title', content: 'StackOS — Give your agents a place to work' },
        {
          property: 'og:description',
          content:
            'A local operating layer for agent-run business work. Explicit workflows, scoped permissions, safe credentials, and proof.',
        },
        { property: 'og:image', content: `${siteUrl}/images/plugins.png` },
        { name: 'twitter:card', content: 'summary_large_image' },
      ],
      link: [
        { rel: 'preload', href: '/fonts/manrope-latin-variable.woff2', as: 'font', type: 'font/woff2', crossorigin: 'anonymous' },
        { rel: 'icon', type: 'image/png', href: '/images/stackos-icon.png' },
        { rel: 'apple-touch-icon', href: '/images/stackos-icon.png' },
        { rel: 'alternate', type: 'application/rss+xml', title: 'StackOS Library', href: '/feed.xml' },
      ],
    },
  },
  nitro: {
    // Hostinger's LiteSpeed server compresses responses. Precompressed sidecar
    // files can outlive index.html during FTP updates and serve stale releases.
    compressPublicAssets: false,
    prerender: {
      crawlLinks: true,
      routes: ['/', '/getting-started', '/getting-started.md', '/library', '/feed.xml'],
    },
  },
  routeRules: {
    '/getting-started': { prerender: true },
    '/getting-started.md': {
      prerender: true,
      headers: {
        'Content-Type': 'text/markdown; charset=utf-8',
        'X-Robots-Tag': 'noindex',
        Link: `<${siteUrl}/getting-started>; rel="canonical"`,
      },
    },
    '/library/**': { prerender: true },
  },
  sitemap: {
    autoLastmod: true,
    sources: ['/api/__sitemap__/urls'],
  },
  schemaOrg: {
    identity: {
      type: 'Organization',
      name: 'StackOS',
      url: siteUrl,
      logo: `${siteUrl}/images/stackos-icon.png`,
    },
  },
  linkChecker: {
    enabled: true,
  },
  typescript: {
    strict: true,
    typeCheck: true,
  },
})
