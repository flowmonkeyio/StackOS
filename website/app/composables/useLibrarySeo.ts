interface LibrarySeoOptions {
  title: string
  description: string
  type?: 'website' | 'article'
  publishedAt?: string
  updatedAt?: string
}

export function useLibrarySeo(options: LibrarySeoOptions) {
  const route = useRoute()
  const config = useRuntimeConfig()
  const canonical = computed(() => new URL(route.path, config.public.siteUrl as string).toString())

  useHead({
    link: [{ rel: 'canonical', href: canonical }],
  })

  useSeoMeta({
    title: options.title,
    description: options.description,
    ogTitle: options.title,
    ogDescription: options.description,
    ogType: options.type || 'website',
    ogUrl: canonical,
    twitterCard: 'summary_large_image',
    twitterTitle: options.title,
    twitterDescription: options.description,
    articlePublishedTime: options.publishedAt,
    articleModifiedTime: options.updatedAt,
  })

  defineOgImageComponent('StackOS' as any, {
    title: options.title,
    description: options.description,
    colorMode: 'dark',
  })

  return { canonical }
}
