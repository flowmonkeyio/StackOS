# StackOS Library content operations

The Library uses a hybrid content model:

- Nuxt Content Markdown stores semantic article prose.
- Typed frontmatter stores search intent, authorship, dates, visual mode, and explicit relationships.
- MDC components place interactive workflows and concept visuals inside the article body.
- Generated JSON exposes a sanitized public catalog of StackOS workflows, agents, and orchestrators.

The public workflow catalog is generated directly from each workflow's
`experience` and `public` contracts. Agent cards use each preset's explicit
`role_class`. Agent detail pages expose only the public allowlist from
`prompt_contract`: `mission`, `responsibilities`, `must_do`, `must_not_do`,
`handoff_inputs`, `handoff_outputs`, and `success_criteria`. Do not expose raw
prompts, credentials, authentication data, hidden workflow context, or
unreviewed fields. Do not add manual description maps, hard-coded inventory
counts, or global jargon replacements in the website generator. Improve the
owning workflow or preset when the public copy is weak.

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

Orient the reader quickly and make the article's answer discoverable, but do not
force every opening into a direct-answer template. The selected angle, evidence,
and active voice guide decide whether the piece should open with the answer, a
real tension, an observed constraint, or an operating moment. The rest of the
piece should add first-hand product evidence, examples, limitations, and useful
next actions. Do not create FAQ schema; ordinary question headings are enough.

## Add visuals

Visuals are editorially optional. The required `visual` frontmatter records the
selected page mode; it does not require every article to contain an embedded
workflow or concept visual. Use an embedded workflow when it materially
clarifies a real process:

```mdc
::article-workflow-visual{workflow="branding-content-production" title="From request to published result"}
::
```

Use a concept visual when it materially clarifies relationships that are not one
workflow:

```mdc
::article-concept-visual{mode="connections" title="Keep the tools you use" caption="StackOS connects the conversation to the work."}
::
```

Generated editorial images are optional. When the content workflow selects them, require an image plan with placement, aspect ratio, alt text, acceptance criteria, and a within-budget check before calling OpenAI Images. Store the final public path and dimensions in `heroImage`; never put a credential or provider response in the article.

The StackOS OpenAI Images budget is capped at $5/month with an 80% warning. Prefer deterministic workflow visuals and generated social cards when they communicate the idea without paid generation.

## Cross-linking

Use stable public slugs in the three related arrays. `pnpm content:sync` fails if an article points to a missing article, workflow, agent, or embedded workflow visual. Article pages render these relationships automatically.

## SEO and publishing

Page callers provide an unbranded subject title to `useSiteSeo`. That one
composable owns the canonical URL and aligned document, Open Graph, and Twitter
metadata; the global title template appends `| StackOS` once. Public HTML uses
trailing-slash canonical paths. Never hand-author a slashless canonical, social
URL, sitemap URL, RSS article URL, or internal HTML link.

`updatedAt` is a source date, not a build stamp. Change it only when the
article or guide receives a meaningful editorial update. Static and generated
catalog routes use the reviewed records in `content/sitemap-lastmod.json`;
family defaults cover shared template/catalog changes and item overrides cover
one materially changed entry. Every record needs a defensible evidence note.
Do not use generation time, deployment time, crawl time, file modification
time, or the generated catalog timestamp as `lastmod`.

Integration and agent pages must add source-backed value rather than generic
word-count padding. Integration details may render provider setup, auth type,
capabilities, actions, risk boundaries, and official links from the generated
catalog. Agent pages may render only the public contract fields listed above.
The integrations index must expose every provider as a server-rendered direct
link. `core`, `linear`, `shopify`, and `trackbooth` plugin detail routes are
consolidated to their provider pages.

Before publishing a new cluster:

1. Filter the project's reviewed 500-keyword opportunity library, then reconcile
   it with current StackOS `content-piece` records and live site articles.
   Classify each candidate as new, strengthen, refresh, or hold. Run
   `seo.keyword-research` only when the library has no relevant current fit;
   authorize paid research separately when needed.
2. Use the selected opportunity as input to `branding.content-production`.
3. Run independent claim, voice, and disclosure review; repair blockers and
   finalize the canonical website packet.
4. Run `pnpm --dir website content:sync`,
   `pnpm --dir website typecheck`, `pnpm --dir website generate`, and
   `pnpm --dir website test:seo:generated`.
5. Deploy only when `publication_intent` is `stage` or `publish`, and only to
   the named target channels and destinations.
6. After an authorized deployment, validate the production canonical, header,
   sitemap, and feed matrix. Submit the sitemap or request indexing in Search
   Console only with explicit operator authorization. IndexNow is also an
   explicit post-deploy action; dry-run the payload first and never invoke it
   from content sync, build, generate, or tests.
7. Use `seo.content-refresh` when evidence or performance shows the article needs an update.

Use lifecycle labels literally:

- `working`: research, angle, or draft is still changing;
- `review-ready`: the packet is ready for operator review but is not approved;
- `operator-approved`: the operator accepted the content, but it may not be
  integrated into the site;
- `site-native`: the canonical Markdown and relationships are integrated and
  pass the repository checks;
- `published`: the named deployment or external destination succeeded and has a
  recorded result.

Do not call an article published merely because a Markdown file exists, and do
not treat approval as permission to deploy unless publication intent says so.

The canonical site is controlled by `NUXT_PUBLIC_SITE_URL` and currently defaults to `https://stackos.flowmonkey.io`. GA4 uses the explicit measurement ID in `nuxt.config.ts` and loads only after analytics consent.
