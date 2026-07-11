# StackOS Library content operations

The Library uses a hybrid content model:

- Nuxt Content Markdown stores semantic article prose.
- Typed frontmatter stores search intent, authorship, dates, visual mode, and explicit relationships.
- MDC components place interactive workflows and concept visuals inside the article body.
- Generated JSON exposes a sanitized public catalog of StackOS workflows, agents, and orchestrators.

## Create an article

Use `branding.content-production` for the editorial run. The approved website packet writes one Markdown file to `content/articles/` with these required fields:

- `title`, `description`, `publishedAt`, `updatedAt`, `author`, and `category`
- `topics`, `readingTime`, `featured`, and `searchIntent`
- `visual`
- `relatedWorkflows`, `relatedAgents`, and `relatedArticles`

The opening paragraph must answer the article's main question directly. The rest of the piece should add first-hand product evidence, examples, limitations, and useful next actions. Do not create FAQ schema; ordinary question headings are enough.

## Add visuals

Use an embedded workflow when the article explains a real process:

```mdc
::article-workflow-visual{workflow="branding-content-production" title="From request to published result"}
::
```

Use a concept visual for relationships that are not one workflow:

```mdc
::article-concept-visual{mode="connections" title="Keep the tools you use" caption="StackOS connects the conversation to the work."}
::
```

Generated editorial images are optional. When the content workflow selects them, require an image plan with placement, aspect ratio, alt text, acceptance criteria, and spend approval before calling OpenAI Images. Store the final public path and dimensions in `heroImage`; never put a credential or provider response in the article.

The StackOS OpenAI Images budget is capped at $5/month with an 80% warning. Prefer deterministic workflow visuals and generated social cards when they communicate the idea without paid generation.

## Cross-linking

Use stable public slugs in the three related arrays. `pnpm content:sync` fails if an article points to a missing article, workflow, agent, or embedded workflow visual. Article pages render these relationships automatically.

## SEO and publishing

Before publishing a new cluster:

1. Run `seo.keyword-research` with the business goal, audience, and candidate topic.
2. Use the approved opportunity as input to `branding.content-production`.
3. Review claims, voice, disclosure, image plan, and the final website packet.
4. Run `pnpm content:sync`, `pnpm typecheck`, and `pnpm build`.
5. After deployment, submit the sitemap in Search Console and monitor GA4 and search performance.
6. Use `seo.content-refresh` when evidence or performance shows the article needs an update.

The canonical site is controlled by `NUXT_PUBLIC_SITE_URL` and currently defaults to `https://stackos.flowmonkey.io`. GA4 uses the explicit measurement ID in `nuxt.config.ts` and loads only after analytics consent.
