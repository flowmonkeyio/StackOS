import { queryCollection } from '@nuxt/content/server'
import { isConsolidatedPlugin } from '#shared/utils/integrationRoutePolicy'
import { canonicalPath } from '#shared/utils/siteSeo'
import catalog from '../../../app/data/library-catalog.generated.json'
import integrationCatalog from '../../../app/data/integration-catalog.generated.json'
import sitemapLastmodSource from '../../../content/sitemap-lastmod.json'

interface LastmodSource {
  updatedAt: string
  evidence: string
}

interface LastmodFamily {
  default: LastmodSource
  items: Record<string, LastmodSource>
}

interface SitemapLastmodSource {
  schemaVersion: 'stackos.sitemap-lastmod.v1'
  routes: Record<string, LastmodSource>
  catalogFamilies: Record<string, LastmodFamily>
}

const sitemapLastmod = sitemapLastmodSource as SitemapLastmodSource

function routeLastmod(route: string) {
  return sitemapLastmod.routes[route]?.updatedAt
}

function catalogLastmod(family: string, slug: string) {
  const source = sitemapLastmod.catalogFamilies[family]
  return (source?.items?.[slug] || source?.default)?.updatedAt
}

export default defineEventHandler(async (event) => {
  const articles = await queryCollection(event, 'articles').select('stem', 'updatedAt').all()
  const gettingStarted = await queryCollection(event, 'guides').select('updatedAt').first()
  const { workflows, agents, orchestrators } = catalog

  return [
    { loc: '/', lastmod: routeLastmod('/'), priority: 1.0 },
    { loc: canonicalPath('/getting-started'), lastmod: gettingStarted?.updatedAt, priority: 1.0 },
    { loc: canonicalPath('/library'), lastmod: routeLastmod('/library/'), priority: 0.9 },
    { loc: canonicalPath('/library/articles'), lastmod: routeLastmod('/library/articles/'), priority: 0.8 },
    { loc: canonicalPath('/library/workflows'), lastmod: routeLastmod('/library/workflows/'), priority: 0.8 },
    { loc: canonicalPath('/library/agents'), lastmod: routeLastmod('/library/agents/'), priority: 0.7 },
    { loc: canonicalPath('/library/orchestrators'), lastmod: routeLastmod('/library/orchestrators/'), priority: 0.7 },
    { loc: canonicalPath('/library/integrations'), lastmod: routeLastmod('/library/integrations/'), priority: 0.8 },
    ...articles.map((article) => ({
      loc: canonicalPath(`/library/articles/${article.stem.split('/').at(-1)}`),
      lastmod: article.updatedAt,
      priority: 0.8,
    })),
    ...workflows.map((item) => ({ loc: canonicalPath(`/library/workflows/${item.slug}`), lastmod: catalogLastmod('workflows', item.slug), priority: 0.7 })),
    ...agents.map((item) => ({ loc: canonicalPath(`/library/agents/${item.slug}`), lastmod: catalogLastmod('agents', item.slug), priority: 0.6 })),
    ...orchestrators.map((item) => ({ loc: canonicalPath(`/library/orchestrators/${item.slug}`), lastmod: catalogLastmod('orchestrators', item.slug), priority: 0.7 })),
    ...integrationCatalog.providers.map((item) => ({ loc: canonicalPath(`/library/integrations/${item.slug}`), lastmod: catalogLastmod('integrationProviders', item.slug), priority: 0.7 })),
    ...integrationCatalog.plugins
      .filter((item) => !isConsolidatedPlugin(item.slug))
      .map((item) => ({ loc: canonicalPath(`/library/integrations/plugins/${item.slug}`), lastmod: catalogLastmod('integrationPlugins', item.slug), priority: 0.6 })),
  ]
})
