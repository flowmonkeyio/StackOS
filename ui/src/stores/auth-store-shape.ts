// Minimal shape consumed by `lib/client.ts` so it can pull the bearer
// token without depending on Pinia at module-load time. The real Pinia
// store satisfies this shape.

export interface AuthStoreLike {
  readonly token: string | null
}
