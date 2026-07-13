# StackOS Library content operations

The Library uses a hybrid content model:

- Nuxt Content Markdown stores semantic article prose.
- Typed frontmatter stores search intent, authorship, dates, visual mode, and explicit relationships.
- MDC components place interactive workflows and concept visuals inside the article body.
- Generated JSON exposes a sanitized public catalog of StackOS workflows, agents, and orchestrators.

The public workflow catalog is generated directly from each workflow's
`experience` and `public` contracts. Agent cards use each preset's explicit
`role_class`. Do not add manual description maps, hard-coded inventory counts,
or global jargon replacements in the website generator. Improve the owning
workflow or preset when the public copy is weak.

## Create an article

Use `branding.brand-foundation-setup` first when the project has no current,
retrievable voice profile and voice-guide artifact. Then use
`branding.content-production` for the editorial run. Its interview mode is
`auto`, `required`, or `skip`; auto should interview only when first-hand
judgment or experience would materially improve the piece. The final website
packet writes one Markdown file to `content/articles/` with these required fields:

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

Generated editorial images are optional. When the content workflow selects them, require an image plan with placement, aspect ratio, alt text, acceptance criteria, and a within-budget check before calling OpenAI Images. Store the final public path and dimensions in `heroImage`; never put a credential or provider response in the article.

The StackOS OpenAI Images budget is capped at $5/month with an 80% warning. Prefer deterministic workflow visuals and generated social cards when they communicate the idea without paid generation.

## Cross-linking

Use stable public slugs in the three related arrays. `pnpm content:sync` fails if an article points to a missing article, workflow, agent, or embedded workflow visual. Article pages render these relationships automatically.

## SEO and publishing

Before publishing a new cluster:

1. Filter the project's reviewed 500-keyword opportunity library first. Run
   `seo.keyword-research` only when that library has no relevant current fit;
   authorize paid research separately when needed.
2. Use the selected opportunity as input to `branding.content-production`.
3. Run independent claim, voice, and disclosure review; repair blockers and
   finalize the canonical website packet.
4. Run `pnpm content:sync`, `pnpm typecheck`, and `pnpm build`.
5. Deploy only when `publication_intent` is `stage` or `publish`, and only to
   the named target channels and destinations.
6. After deployment, submit the sitemap in Search Console and monitor GA4 and search performance.
7. Use `seo.content-refresh` when evidence or performance shows the article needs an update.

The canonical site is controlled by `NUXT_PUBLIC_SITE_URL` and currently defaults to `https://stackos.flowmonkey.io`. GA4 uses the explicit measurement ID in `nuxt.config.ts` and loads only after analytics consent.
