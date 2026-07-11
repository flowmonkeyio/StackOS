<template>
  <div class="workbench" aria-label="StackOS local workflow execution preview">
    <div class="workbench__bar">
      <div class="window-dots" aria-hidden="true"><i /><i /><i /></div>
      <code>stackos / fall collection</code>
      <span><i class="status-dot" /> Live</span>
    </div>

    <div class="workbench__body">
      <aside class="workbench__rail" aria-hidden="true">
        <img src="/images/stackos-icon.png" alt="" width="34" height="34" />
        <span class="rail-icon is-active">⌁</span>
        <span class="rail-icon">□</span>
        <span class="rail-icon">↗</span>
        <span class="rail-icon rail-icon--bottom">···</span>
      </aside>

      <div class="workbench__main">
        <div class="workbench__context">
          <div>
            <span class="micro-label">Active work</span>
            <strong>Launch the fall collection</strong>
          </div>
          <span class="status-pill status-pill--running"><i /> Running</span>
        </div>

        <div class="execution-path">
          <article class="execution-card execution-card--agent">
            <div class="execution-card__icon execution-card__icon--brand">
              <img src="/images/claude.webp" alt="" width="34" height="34" />
            </div>
            <div>
              <span>Claude Code · your request</span>
              <strong>Launch our fall collection</strong>
              <code>conversation connected</code>
            </div>
            <b>01</b>
          </article>

          <div class="execution-connector"><span>turns into a plan</span></div>

          <article class="execution-card execution-card--plan">
            <div class="execution-card__icon">S</div>
            <div>
              <span>Plan ready for review</span>
              <strong>7 clear steps</strong>
              <code>people · apps · checks · approvals</code>
            </div>
            <b>02</b>
          </article>

          <div class="execution-connector"><span>starts step by step</span></div>

          <article class="execution-card execution-card--action">
            <div class="execution-card__icon">↗</div>
            <div>
              <span>Current step · working</span>
              <strong>Build the Shopify collection</strong>
              <code>products · images · inventory</code>
            </div>
            <b>03</b>
          </article>
        </div>

        <div class="workbench__footer">
          <div class="audit-stamp">
            <span class="audit-stamp__check">✓</span>
            <div>
              <strong>4 of 7 steps finished</strong>
              <small>Images ready · products added · progress saved</small>
            </div>
          </div>
          <div class="credential-chip">
            <span>KEY</span>
            <div>
              <small>Connected app</small>
              <strong>Shopify ready</strong>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="workbench__float workbench__float--top">
      <img src="/images/claude.webp" alt="" width="20" height="20" /> Claude Code connected
    </div>
    <div class="workbench__float workbench__float--bottom">
      <span>Progress</span>
      <strong>waiting · working · done</strong>
    </div>
  </div>
</template>

<style scoped>
.workbench {
  position: relative;
  min-width: 650px;
  overflow: visible;
  color: var(--paper);
  background: #0d1017;
  border: 1px solid rgb(255 255 255 / 12%);
  border-radius: 24px;
  box-shadow: 0 70px 140px rgb(0 0 0 / 48%), 0 0 0 8px rgb(255 255 255 / 2%);
  transform: perspective(1400px) rotateY(-4deg) rotateX(1deg);
  transform-origin: center;
}

.workbench::after {
  position: absolute;
  inset: 0;
  pointer-events: none;
  content: '';
  border-radius: inherit;
  box-shadow: inset 0 1px 0 rgb(255 255 255 / 8%);
}

.workbench__bar {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  min-height: 46px;
  padding: 0 16px;
  color: var(--ink-muted);
  background: rgb(255 255 255 / 2%);
  border-bottom: 1px solid rgb(255 255 255 / 7%);
}

.workbench__bar code {
  font-size: 10px;
}

.workbench__bar > span {
  display: flex;
  align-items: center;
  justify-self: end;
  gap: 7px;
  color: #9aefbd;
  font-family: var(--font-mono);
  font-size: 10px;
  text-transform: uppercase;
}

.window-dots {
  display: flex;
  gap: 5px;
}

.window-dots i {
  width: 7px;
  height: 7px;
  background: #303642;
  border-radius: 50%;
}

.workbench__body {
  display: grid;
  grid-template-columns: 58px 1fr;
}

.workbench__rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 14px 10px;
  background: #090b10;
  border-right: 1px solid rgb(255 255 255 / 7%);
  border-radius: 0 0 0 24px;
}

.workbench__rail img {
  margin-bottom: 8px;
  border-radius: 9px;
}

.rail-icon {
  display: grid;
  width: 30px;
  height: 30px;
  place-items: center;
  color: #848d9d;
  font-family: var(--font-mono);
  font-size: 12px;
  border: 1px solid transparent;
  border-radius: 8px;
}

.rail-icon.is-active {
  color: var(--paper);
  background: rgb(91 124 255 / 17%);
  border-color: rgb(91 124 255 / 28%);
}

.rail-icon--bottom {
  margin-top: auto;
}

.workbench__main {
  padding: 20px;
}

.workbench__context {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 17px;
}

.workbench__context strong {
  display: block;
  margin-top: 5px;
  font-size: 14px;
  letter-spacing: -0.02em;
}

.micro-label {
  color: var(--ink-muted);
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.09em;
  text-transform: uppercase;
}

.execution-path {
  padding: 14px;
  background:
    linear-gradient(rgb(255 255 255 / 2%) 1px, transparent 1px),
    linear-gradient(90deg, rgb(255 255 255 / 2%) 1px, transparent 1px),
    #0a0d13;
  background-size: 24px 24px;
  border: 1px solid rgb(255 255 255 / 7%);
  border-radius: 14px;
}

.execution-card {
  position: relative;
  display: grid;
  grid-template-columns: 34px 1fr auto;
  gap: 12px;
  align-items: center;
  padding: 12px;
  overflow: hidden;
  background: rgb(18 22 32 / 94%);
  border: 1px solid rgb(255 255 255 / 10%);
  border-left: 2px solid var(--cobalt-soft);
  border-radius: 10px;
  box-shadow: 0 12px 30px rgb(0 0 0 / 22%);
}

.execution-card--agent {
  border-left-color: var(--cyan);
}

.execution-card--action {
  border-left-color: var(--signal);
}

.execution-card__icon {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  color: var(--paper);
  font-family: var(--font-mono);
  font-size: 11px;
  background: #202637;
  border: 1px solid rgb(255 255 255 / 9%);
  border-radius: 9px;
}

.execution-card__icon--brand {
  padding: 0;
  overflow: hidden;
}

.execution-card__icon--brand img,
.workbench__float--top > img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.execution-card span,
.execution-card code {
  display: block;
  color: var(--ink-muted);
  font-family: var(--font-mono);
  font-size: 9px;
}

.execution-card strong {
  display: block;
  margin: 3px 0 5px;
  font-size: 12px;
}

.execution-card b {
  color: #484f5e;
  font-family: var(--font-mono);
  font-size: 11px;
}

.execution-connector {
  position: relative;
  height: 26px;
  margin-left: 31px;
  border-left: 1px dashed #3f4a61;
}

.execution-connector span {
  position: absolute;
  top: 8px;
  left: 12px;
  color: #5d6676;
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.workbench__footer {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 10px;
  margin-top: 13px;
}

.audit-stamp,
.credential-chip {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 49px;
  padding: 9px 12px;
  background: rgb(255 255 255 / 3%);
  border: 1px solid rgb(255 255 255 / 7%);
  border-radius: 10px;
}

.audit-stamp__check {
  display: grid;
  width: 25px;
  height: 25px;
  place-items: center;
  color: #9aefbd;
  background: rgb(70 193 123 / 10%);
  border: 1px solid rgb(70 193 123 / 20%);
  border-radius: 50%;
}

.audit-stamp strong,
.credential-chip strong {
  display: block;
  font-size: 10px;
}

.audit-stamp small,
.credential-chip small {
  display: block;
  margin-top: 3px;
  color: var(--ink-muted);
  font-family: var(--font-mono);
  font-size: 9px;
}

.credential-chip > span {
  display: grid;
  width: 25px;
  height: 25px;
  place-items: center;
  color: var(--signal);
  font-family: var(--font-mono);
  font-size: 8px;
  background: rgb(217 255 99 / 8%);
  border: 1px solid rgb(217 255 99 / 15%);
  border-radius: 7px;
}

.workbench__float {
  position: absolute;
  z-index: 5;
  padding: 10px 13px;
  color: var(--paper);
  background: rgb(20 25 35 / 90%);
  border: 1px solid rgb(255 255 255 / 12%);
  border-radius: 10px;
  box-shadow: 0 16px 50px rgb(0 0 0 / 35%);
  backdrop-filter: blur(12px);
}

.workbench__float--top {
  top: 72px;
  right: 14px;
  display: flex;
  align-items: center;
  gap: 8px;
  font-family: var(--font-mono);
  font-size: 9px;
  text-transform: uppercase;
}

.workbench__float--top > img {
  width: 20px;
  height: 20px;
  border-radius: 6px;
}

.workbench__float--bottom {
  bottom: 40px;
  left: -50px;
}

.workbench__float--bottom span,
.workbench__float--bottom strong {
  display: block;
}

.workbench__float--bottom span {
  margin-bottom: 4px;
  color: var(--ink-muted);
  font-family: var(--font-mono);
  font-size: 9px;
  text-transform: uppercase;
}

.workbench__float--bottom strong {
  font-size: 10px;
}

@media (prefers-reduced-motion: no-preference) {
  .execution-card--plan::after {
    position: absolute;
    top: -1px;
    bottom: -1px;
    width: 80px;
    pointer-events: none;
    content: '';
    background: linear-gradient(90deg, transparent, rgb(91 124 255 / 11%), transparent);
    animation: permission-scan 3.8s ease-in-out infinite;
  }

  .workbench__float--top {
    animation: float-card 4s ease-in-out infinite;
  }

  @keyframes permission-scan {
    from {
      left: -100px;
    }
    to {
      left: calc(100% + 20px);
    }
  }

  @keyframes float-card {
    50% {
      transform: translateY(-6px);
    }
  }
}

@media (max-width: 1120px) {
  .workbench {
    min-width: 610px;
    transform: none;
  }

  .workbench__float--top {
    right: -20px;
  }

  .workbench__float--bottom {
    left: -20px;
  }
}

@media (max-width: 700px) {
  .workbench {
    min-width: 0;
    border-radius: 18px;
  }

  .workbench__bar {
    grid-template-columns: 1fr auto;
  }

  .workbench__bar code {
    display: none;
  }

  .workbench__body {
    grid-template-columns: 1fr;
  }

  .workbench__rail,
  .workbench__float {
    display: none;
  }

  .workbench__main {
    padding: 12px;
  }

  .workbench__context {
    padding: 3px;
  }

  .execution-path {
    padding: 9px;
  }

  .execution-card {
    grid-template-columns: 30px 1fr;
  }

  .execution-card b {
    display: none;
  }

  .workbench__footer {
    grid-template-columns: 1fr;
  }

  .credential-chip {
    display: none;
  }
}
</style>
