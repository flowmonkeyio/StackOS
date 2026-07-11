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
