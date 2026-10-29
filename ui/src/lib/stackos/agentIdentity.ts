/** The same packaged agent marks are used in Overview and work ownership. */
const AGENT_HOST_LOGOS = new Map([
  ['codex', '/images/openai.webp'],
  ['claude-code', '/images/claude-code.webp'],
  ['claude-desktop', '/images/claude.webp'],
  ['gemini-cli', '/images/gemini.webp'],
  ['hermes', '/images/hermes.webp'],
])

const OWNER_HOST_KEYS = new Map([
  ['codex', 'codex'],
  ['chatgpt', 'codex'],
  ['chatgpt / codex', 'codex'],
  ['claude', 'claude-desktop'],
  ['claude code', 'claude-code'],
  ['claude-code', 'claude-code'],
  ['claude desktop', 'claude-desktop'],
  ['claude-desktop', 'claude-desktop'],
  ['gemini', 'gemini-cli'],
  ['gemini cli', 'gemini-cli'],
  ['gemini-cli', 'gemini-cli'],
  ['hermes', 'hermes'],
])

/** Host keys are canonical; an unknown host keeps the caller's own fallback. */
export function resolveAgentHostLogo(hostKey: string): string | null {
  return AGENT_HOST_LOGOS.get(hostKey) ?? null
}

/** Recognize agent names without turning arbitrary human owners into agents. */
export function resolveAgentOwnerLogo(owner: string | null | undefined): string | null {
  const normalized = owner?.trim().toLowerCase().replace(/\s+/g, ' ').replace(/[._]/g, '-')
  if (!normalized) return null

  // Codex records machine owners such as codex-main-agent and codex:/root/task.
  // Match that namespace, never arbitrary substrings or whitespace names.
  if (/^codex[-:][a-z0-9/][a-z0-9:/-]*$/.test(normalized)) {
    return resolveAgentHostLogo('codex')
  }

  // Other agent role suffixes stay explicit. Claude Monet / Claude.Monet
  // must retain the human/unknown fallback.
  const identity = normalized.replace(/(?:-(?:main|agent|worker|reviewer|orchestrator)(?:-\d+)?)+$/, '')
  const hostKey = OWNER_HOST_KEYS.get(identity)
  return hostKey ? resolveAgentHostLogo(hostKey) : null
}
