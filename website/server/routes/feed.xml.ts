import { queryCollection } from '@nuxt/content/server'

function escapeXml(value: string) {
  return value.replace(/[<>&'\"]/g, (character) => ({
    '<': '&lt;',
    '>': '&gt;',
    '&': '&amp;',
    "'": '&apos;',
    '"': '&quot;',
  })[character] || character)
}

export default defineEventHandler(async (event) => {
  const config = useRuntimeConfig(event)
  const siteUrl = config.public.siteUrl as string
  const articles = await queryCollection(event, 'articles').order('publishedAt', 'DESC').all()

  const items = articles.map((article) => {
    const slug = article.stem.split('/').at(-1)
    const url = `${siteUrl}/library/articles/${slug}`
    return `<item>
      <title>${escapeXml(article.title)}</title>
      <link>${url}</link>
      <guid isPermaLink="true">${url}</guid>
      <description>${escapeXml(article.description)}</description>
      <pubDate>${new Date(`${article.publishedAt}T12:00:00Z`).toUTCString()}</pubDate>
    </item>`
  }).join('\n')

  setHeader(event, 'content-type', 'application/rss+xml; charset=utf-8')
  return `<?xml version="1.0" encoding="UTF-8" ?>
  <rss version="2.0">
    <channel>
      <title>StackOS Library</title>
      <link>${siteUrl}/library</link>
      <description>Practical guides to AI agents, agentic workflows, and connected work.</description>
      <language>en-us</language>
      ${items}
    </channel>
  </rss>`
})
