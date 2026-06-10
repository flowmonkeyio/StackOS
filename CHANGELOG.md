# Changelog

## Unreleased

- Added the `marketing` plugin: `marketing.campaign-production` workflow
  template (brief intake, campaign workspace with `campaign.md`, planned media
  manifest, operator plan approval, media production, landing page variants,
  visual signoff, local gallery, closeout), `brand-profile` /
  `campaign-brief` / `campaign-evidence` resources, five campaign agent
  presets, and the campaign production orchestrator skill preset.
- Added `utils.image.edit`: GPT Image edits with input reference images from
  generated assets for product-faithful marketing shots, including
  `input_fidelity` handling for supported GPT Image 1.x models. OpenAI image
  actions now expose capability metadata, enforce documented prompt/input-image
  limits, keep gpt-image-2 custom sizes deferred until budget modeling lands,
  and register persisted outputs as generic image artifacts during
  repository-backed execution.
- Added the provider-neutral `video-generation` provider with credential
  wiring and the deferred `utils.video.generate` action contract
  (`deferred-video-backend-selection`); execution becomes available once a
  supported vendor backend connector lands.
- Added xAI Imagine actions `utils.xai.image.generate`, `utils.xai.image.edit`,
  and `utils.xai.video.generate` with latest Grok models, provider-specific
  capability metadata, generated-assets persistence, generic media artifact
  registration, run-plan grant coverage, official-doc-based pre-call budget
  estimates, and actual-cost reconciliation from xAI usage ticks when present.
  Alibaba WAN remains skipped for v1 until public executable API docs are
  sufficient.

## 1.0.0 - 2026-05-26

- Pivoted the product architecture to StackOS: project-scoped plugins,
  workflow templates, run plans, generic resources/artifacts, no-secret auth
  references, context, learnings, experiments, decisions, and action execution.
- Reframed SEO as a first-party plugin domain rather than the core product
  shape.
- Simplified the UI direction around generic StackOS renderers.
- Updated documentation to describe the current clean-cut architecture.
