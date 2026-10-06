# StackOS website

The public-facing StackOS business website. It is intentionally isolated from
the operational Vue/Vite console in `../ui`.

```bash
pnpm install
pnpm dev
pnpm typecheck
pnpm build
pnpm test:e2e
```

Production and Hostinger deployment instructions are documented in
[`DEPLOYMENT.md`](./DEPLOYMENT.md).

The page content is server-rendered by Nuxt. Vue Flow is a client-side visual
enhancement with a semantic workflow summary beside it, so the product story
remains useful before hydration and with reduced motion.

Article content uses native Nuxt Content components. Use `ArticleEvidenceRow`
for narrative records with an evidence passage and a decision: its `label`,
`record-heading`, `evidence-label`, and `decision-label` props retain the original
labels, with Markdown in the named `evidence` and `decision` slots. Keep actual
comparisons and reference data as Markdown tables. The article-only
`article-prose--reading` treatment does not change the getting-started guide.

After content-rendering changes, run `pnpm typecheck`, `pnpm generate`, and
`pnpm test:seo:generated`. The generated check verifies that all opted-in evidence
records and decisions remain in semantic server-rendered HTML.
