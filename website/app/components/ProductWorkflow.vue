<template>
  <ClientOnly>
    <ProductWorkflowVisual />
    <template #fallback>
      <section id="workflow" class="workflow-fallback section section--ink" aria-labelledby="workflow-fallback-title">
        <div class="shell">
          <div class="section-heading section-heading--wide">
            <div>
              <p class="eyebrow eyebrow--dark">Keep your AI tool. Add a complete way of working.</p>
              <h2 id="workflow-fallback-title">One request becomes<br /><em>a plan you can watch.</em></h2>
            </div>
            <p>
              Ask in Codex, Claude Code, Gemini, or another AI tool. StackOS shows you the full
              plan, then keeps every step and result connected.
            </p>
          </div>
          <ol aria-label="StackOS workflow journey">
            <li><span>01</span><strong>Ask in your AI tool</strong><code>Request received</code></li>
            <li><span>02</span><strong>See the full plan</strong><code>Ready to review</code></li>
            <li><span>03</span><strong>Approve the work</strong><code>Ready to start</code></li>
            <li><span>04</span><strong>Watch each step move</strong><code>Working</code></li>
            <li><span>05</span><strong>Keep the result</strong><code>Complete</code></li>
          </ol>
        </div>
      </section>
    </template>
  </ClientOnly>
</template>

<style scoped>
.workflow-fallback ol {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  margin: 56px 0 0;
  padding: 0;
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: 20px;
  list-style: none;
}

.workflow-fallback li {
  display: grid;
  min-height: 150px;
  padding: 20px;
  border-right: 1px solid var(--border-strong);
}

.workflow-fallback li:last-child {
  border: 0;
}

.workflow-fallback li span,
.workflow-fallback li code {
  color: var(--ink-muted);
  font-family: var(--font-mono);
  font-size: 9px;
}

.workflow-fallback li strong {
  align-self: center;
  font-size: 13px;
}

.workflow-fallback li code {
  align-self: end;
  color: var(--cobalt-soft);
}

@media (max-width: 800px) {
  .workflow-fallback ol {
    grid-template-columns: 1fr;
  }

  .workflow-fallback li {
    min-height: 0;
    border-right: 0;
    border-bottom: 1px solid var(--border-strong);
  }
}
</style>
