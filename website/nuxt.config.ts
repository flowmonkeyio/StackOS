import { integrationConsolidations, providerIntegrationPath } from './shared/utils/integrationRoutePolicy'

function requiredPublicEnv(name: string) {
  const value = process.env[name]
  if (!value) throw new Error(`${name} is required`)
  return value
}

const siteUrl = requiredPublicEnv('NUXT_PUBLIC_SITE_URL')
const gaMeasurementId = requiredPublicEnv('NUXT_PUBLIC_GA_MEASUREMENT_ID')
const downloadUrl = 'https://stackos.flowmonkey.io/StackOS/stackos-latest-mac-arm64.dmg'
const integrationRedirectRules = Object.fromEntries(
  Object.entries(integrationConsolidations).map(([pluginSlug, providerSlug]) => [
    `/library/integrations/plugins/${pluginSlug}/`,
    { redirect: { to: providerIntegrationPath(providerSlug), statusCode: 301 } },
  ]),
)
const consolidatedPluginPaths = Object.keys(integrationConsolidations)
  .map((pluginSlug) => `/library/integrations/plugins/${pluginSlug}/`)

export default defineNuxtConfig({
  compatibilityDate: '2026-07-09',
  devtools: { enabled: false },
  features: {
    inlineStyles: false,
  },
  experimental: {
    defaults: {
      nuxtLink: {
        trailingSlash: 'append',
      },
    },
  },
  modules: ['@nuxt/content', '@nuxt/image', '@nuxtjs/seo', '@nuxt/scripts'],
  ogImage: {
    security: {
      // A cold static build queues the full library image set. Keep a finite
      // failure budget while allowing queued Satori renders to complete.
      renderTimeout: 60_000,
    },
  },
  content: {
    build: {
      markdown: {
        // Shiki emits route-specific inline token CSS. Code blocks retain the
        // site's external pre/code styling without syntax highlighting.
        highlight: false,
      },
    },
  },
  site: {
    url: siteUrl,
    name: 'StackOS',
    description: 'The place where AI-powered work becomes clear, connected, and accountable.',
    defaultLocale: 'en',
    trailingSlash: true,
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
    '~/assets/css/main.css',
    '~/assets/css/library.css',
  ],
  app: {
    head: {
      htmlAttrs: { lang: 'en' },
      title: 'The local operating layer for AI agents',
      titleTemplate: '%s | StackOS',
      meta: [
        {
          name: 'description',
          content:
            'Connect your tools once. Let AI agents run real business work through local, credential-safe workflows with durable state and audit.',
        },
        { name: 'theme-color', content: '#090b10' },
        { property: 'og:type', content: 'website' },
        { property: 'og:title', content: 'The local operating layer for AI agents | StackOS' },
        {
          property: 'og:description',
          content:
            'A local operating layer for agent-run business work. Explicit workflows, scoped permissions, safe credentials, and proof.',
        },
        { property: 'og:image', content: `${siteUrl}/images/plugins.png` },
        { name: 'twitter:card', content: 'summary_large_image' },
        { name: 'twitter:title', content: 'The local operating layer for AI agents | StackOS' },
        {
          name: 'twitter:description',
          content:
            'Connect your tools once. Let AI agents run real business work through local, credential-safe workflows with durable state and audit.',
        },
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
      routes: [
        '/',
        '/getting-started/',
        '/getting-started.md',
        '/library/',
        '/library/integrations/plugins/linear/',
        '/feed.xml',
      ],
      // Production serves these historical URLs through the exact 301 rules in
      // public/.htaccess. Do not also emit indexable redirect HTML artifacts.
      ignore: consolidatedPluginPaths,
    },
    hooks: {
      'prerender:generate'(route) {
        // Nuxt intentionally emits 404.html as an empty client fallback. The
        // production static host needs the branded error page in the initial
        // response, so skip that fallback and write the shared error component
        // rendered through the private helper route to the expected filename.
        if (route.route === '/404.html') route.skip = true
        if (route.route === '/__static-404') {
          route.fileName = '404.html'
          route.contents = route.contents?.replace(
            /<link\b(?=[^>]*\brel=(["'])canonical\1)[^>]*>/gi,
            '',
          )
        }
      },
    },
  },
  routeRules: {
    '/getting-started/': { prerender: true },
    '/getting-started.md': {
      prerender: true,
      headers: {
        'Content-Type': 'text/markdown; charset=utf-8',
        'X-Robots-Tag': 'noindex',
        Link: `<${siteUrl}/getting-started/>; rel="canonical"`,
      },
    },
    '/feed.xml': {
      prerender: true,
      headers: {
        'X-Robots-Tag': 'noindex',
      },
    },
    '/library/**': { prerender: true },
    ...integrationRedirectRules,
  },
  sitemap: {
    autoLastmod: false,
    excludeAppSources: true,
    sources: ['/api/__sitemap__/urls'],
  },
  linkChecker: {
    enabled: true,
  },
  typescript: {
    strict: true,
    typeCheck: true,
  },
})
