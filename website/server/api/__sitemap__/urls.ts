import { queryCollection } from '@nuxt/content/server'
import catalog from '../../../app/data/library-catalog.generated.json'
import integrationCatalog from '../../../app/data/integration-catalog.generated.json'

export default defineEventHandler(async (event) => {
  const articles = await queryCollection(event, 'articles').select('stem', 'updatedAt').all()
  const gettingStarted = await queryCollection(event, 'guides').select('updatedAt').first()
  const { workflows, agents, orchestrators } = catalog

  return [
    { loc: '/getting-started', lastmod: gettingStarted?.updatedAt, changefreq: 'monthly', priority: 1.0 },
    { loc: '/library', changefreq: 'weekly', priority: 0.9 },
    { loc: '/library/articles', changefreq: 'weekly', priority: 0.8 },
    { loc: '/library/workflows', changefreq: 'weekly', priority: 0.8 },
    { loc: '/library/agents', changefreq: 'monthly', priority: 0.7 },
    { loc: '/library/orchestrators', changefreq: 'monthly', priority: 0.7 },
    { loc: '/library/integrations', changefreq: 'weekly', priority: 0.8 },
    ...articles.map((article) => ({
      loc: `/library/articles/${article.stem.split('/').at(-1)}`,
      lastmod: article.updatedAt,
      changefreq: 'monthly',
      priority: 0.8,
    })),
    ...workflows.map((item) => ({ loc: `/library/workflows/${item.slug}`, changefreq: 'monthly', priority: 0.7 })),
    ...agents.map((item) => ({ loc: `/library/agents/${item.slug}`, changefreq: 'monthly', priority: 0.6 })),
    ...orchestrators.map((item) => ({ loc: `/library/orchestrators/${item.slug}`, changefreq: 'monthly', priority: 0.7 })),
    ...integrationCatalog.providers.map((item) => ({ loc: `/library/integrations/${item.slug}`, changefreq: 'monthly', priority: 0.7 })),
    ...integrationCatalog.plugins.map((item) => ({ loc: `/library/integrations/plugins/${item.slug}`, changefreq: 'monthly', priority: 0.6 })),
  ]
})
