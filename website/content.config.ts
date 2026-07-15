import { defineCollection, defineContentConfig, z } from '@nuxt/content'

const catalogBase = {
  key: z.string(),
  slug: z.string(),
  name: z.string(),
  description: z.string(),
  domain: z.string(),
  audience: z.string(),
  color: z.string(),
  featured: z.boolean().default(false),
}

export default defineContentConfig({
  collections: {
    guides: defineCollection({
      type: 'page',
      source: 'guides/*.md',
      schema: z.object({
        title: z.string(),
        headline: z.string(),
        description: z.string(),
        seoTitle: z.string(),
        publishedAt: z.string(),
        updatedAt: z.string(),
        author: z.string(),
        readingTime: z.string(),
        estimatedTime: z.string(),
        canonicalUrl: z.string().url(),
        markdownUrl: z.string().url(),
      }),
    }),
    articles: defineCollection({
      type: 'page',
      source: 'articles/**/*.md',
      schema: z.object({
        title: z.string(),
        description: z.string(),
        publishedAt: z.string(),
        updatedAt: z.string(),
        author: z.string(),
        category: z.string(),
        topics: z.array(z.string()),
        readingTime: z.string(),
        featured: z.boolean().default(false),
        visual: z.string(),
        searchIntent: z.string(),
        relatedWorkflows: z.array(z.string()).default([]),
        relatedAgents: z.array(z.string()).default([]),
        relatedArticles: z.array(z.string()).default([]),
        heroImage: z.object({
          src: z.string(),
          alt: z.string(),
          width: z.number(),
          height: z.number(),
        }).optional(),
      }),
    }),
    workflows: defineCollection({
      type: 'data',
      source: 'catalog/workflows/*.json',
      schema: z.object({
        ...catalogBase,
        whenToUse: z.array(z.string()),
        stages: z.array(z.object({ id: z.string(), title: z.string(), summary: z.string() })),
        agentNames: z.array(z.string()),
        agentRefs: z.array(z.object({ key: z.string(), slug: z.string(), name: z.string() })),
        integrations: z.array(z.string()),
      }),
    }),
    agents: defineCollection({
      type: 'data',
      source: 'catalog/agents/*.json',
      schema: z.object({
        ...catalogBase,
        role: z.string(),
        workflowKeys: z.array(z.string()),
      }),
    }),
    orchestrators: defineCollection({
      type: 'data',
      source: 'catalog/orchestrators/*.json',
      schema: z.object({
        ...catalogBase,
        workflowKeys: z.array(z.string()),
        coordinates: z.array(z.string()),
        agentRefs: z.array(z.object({ key: z.string(), slug: z.string(), name: z.string() })),
      }),
    }),
  },
})
