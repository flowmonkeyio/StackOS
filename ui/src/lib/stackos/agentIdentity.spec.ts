// @vitest-environment node

import { describe, expect, it } from 'vitest'
import { resolveAgentHostLogo, resolveAgentOwnerLogo } from './agentIdentity'

describe('shared agent identity marks', () => {
  it.each([
    ['codex', '/images/openai.webp'],
    ['claude-code', '/images/claude-code.webp'],
    ['claude-desktop', '/images/claude.webp'],
    ['gemini-cli', '/images/gemini.webp'],
    ['hermes', '/images/hermes.webp'],
  ])('uses the same %s asset for canonical hosts and task owners', (hostKey, logo) => {
    expect(resolveAgentHostLogo(hostKey)).toBe(logo)
    expect(resolveAgentOwnerLogo(hostKey)).toBe(logo)
  })

  it.each([
    [' Codex ', '/images/openai.webp'],
    ['ChatGPT', '/images/openai.webp'],
    ['ChatGPT / Codex', '/images/openai.webp'],
    ['codex-main', '/images/openai.webp'],
    ['codex.agent', '/images/openai.webp'],
    ['codex_worker_2', '/images/openai.webp'],
    ['codex-main-agent', '/images/openai.webp'],
    ['codex-main-orchestrator', '/images/openai.webp'],
    ['codex:/root/article_context_sufficiency', '/images/openai.webp'],
    ['codex:article-mcp-server', '/images/openai.webp'],
    ['codex:article-coding-scope', '/images/openai.webp'],
    ['codex-manual-smoke', '/images/openai.webp'],
    ['codex-audit-probe', '/images/openai.webp'],
    ['Claude', '/images/claude.webp'],
    ['CLAUDE   CODE', '/images/claude-code.webp'],
    ['claude_code', '/images/claude-code.webp'],
    ['claude-code-reviewer', '/images/claude-code.webp'],
    ['claude-code-main-agent', '/images/claude-code.webp'],
    ['claude-main-orchestrator', '/images/claude.webp'],
    ['Claude Desktop', '/images/claude.webp'],
    ['Gemini', '/images/gemini.webp'],
    ['Gemini CLI', '/images/gemini.webp'],
    ['Hermes', '/images/hermes.webp'],
  ])('recognizes owner %s', (owner, logo) => {
    expect(resolveAgentOwnerLogo(owner)).toBe(logo)
  })

  it.each([
    null, undefined, '', '   ', 'Sergey Rura', 'Claude Monet', 'Claude.Monet',
    'delivery.agent', 'not-codex', 'codexx', 'Claude Code Team', 'codex-', 'codex:',
    'codex Alice', 'codex- Alice', 'codex@example.com', 'constructor', '__proto__',
  ])('keeps the initials fallback for %s', (owner) => {
    expect(resolveAgentOwnerLogo(owner)).toBeNull()
  })

  it('leaves fallback choice to the host caller and does not infer host aliases', () => {
    expect(resolveAgentHostLogo('unknown-host')).toBeNull()
    expect(resolveAgentHostLogo('Claude')).toBeNull()
    expect(resolveAgentHostLogo('codex-main')).toBeNull()
  })
})
