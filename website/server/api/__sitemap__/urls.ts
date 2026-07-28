import { queryCollection } from '@nuxt/content/server'
import { isConsolidatedPlugin } from '#shared/utils/integrationRoutePolicy'
import { canonicalPath } from '#shared/utils/siteSeo'
import catalog from '../../../app/data/library-catalog.generated.json'
import integrationCatalog from '../../../app/data/integration-catalog.generated.json'

export default defineEventHandler(async (event) => {
  const articles = await queryCollection(event, 'articles').select('stem', 'updatedAt').all()
  const gettingStarted = await queryCollection(event, 'guides').select('updatedAt').first()
  const { workflows, agents, orchestrators } = catalog

  return [
    { loc: '/', priority: 1.0 },
    { loc: canonicalPath('/getting-started'), lastmod: gettingStarted?.updatedAt, priority: 1.0 },
    { loc: canonicalPath('/library'), priority: 0.9 },
    { loc: canonicalPath('/library/articles'), priority: 0.8 },
    { loc: canonicalPath('/library/workflows'), priority: 0.8 },
    { loc: canonicalPath('/library/agents'), priority: 0.7 },
    { loc: canonicalPath('/library/orchestrators'), priority: 0.7 },
    { loc: canonicalPath('/library/integrations'), priority: 0.8 },
    ...articles.map((article) => ({
      loc: canonicalPath(`/library/articles/${article.stem.split('/').at(-1)}`),
      lastmod: article.updatedAt,
      priority: 0.8,
    })),
    ...workflows.map((item) => ({ loc: canonicalPath(`/library/workflows/${item.slug}`), priority: 0.7 })),
    ...agents.map((item) => ({ loc: canonicalPath(`/library/agents/${item.slug}`), priority: 0.6 })),
    ...orchestrators.map((item) => ({ loc: canonicalPath(`/library/orchestrators/${item.slug}`), priority: 0.7 })),
    ...integrationCatalog.providers.map((item) => ({ loc: canonicalPath(`/library/integrations/${item.slug}`), priority: 0.7 })),
    ...integrationCatalog.plugins
      .filter((item) => !isConsolidatedPlugin(item.slug))
      .map((item) => ({ loc: canonicalPath(`/library/integrations/plugins/${item.slug}`), priority: 0.6 })),
  ]
})
