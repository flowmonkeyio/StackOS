import { absoluteSiteUrl, brandTitle, normalizeSubjectTitle } from '#shared/utils/siteSeo'

interface SiteSeoOptions {
  title: string
  description: string
  type?: 'website' | 'article'
  publishedAt?: string
  updatedAt?: string
}

export function useSiteSeo(options: SiteSeoOptions) {
  const route = useRoute()
  const config = useRuntimeConfig()
  const subjectTitle = computed(() => normalizeSubjectTitle(options.title))
  const socialTitle = computed(() => brandTitle(subjectTitle.value))
  const canonical = computed(() => absoluteSiteUrl(config.public.siteUrl as string, route.path))

  useHead({
    link: [{ rel: 'canonical', href: canonical }],
  })

  useSeoMeta({
    title: subjectTitle,
    description: options.description,
    ogTitle: socialTitle,
    ogDescription: options.description,
    ogType: options.type || 'website',
    ogUrl: canonical,
    twitterCard: 'summary_large_image',
    twitterTitle: socialTitle,
    twitterDescription: options.description,
    articlePublishedTime: options.publishedAt,
    articleModifiedTime: options.updatedAt,
  })

  defineOgImageComponent('StackOS' as any, {
    title: subjectTitle,
    description: options.description,
    colorMode: 'dark',
  })

  return { canonical, subjectTitle, socialTitle }
}
