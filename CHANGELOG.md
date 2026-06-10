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
- Added Reve image actions `utils.reve.image.generate`,
  `utils.reve.image.edit`, and `utils.reve.image.remix` with provider-specific
  capability metadata, generated-assets persistence, generic image artifact
  registration, run-plan grant coverage, official credit-based budget
  estimates, official 32M-pixel remix input preflight, and `credits_used` cost
  reconciliation. Reve `auth.test` is intentionally non-billable format-only
  because the provider does not document a free live credential probe.
- Added Google Gemini Image actions `utils.google.image.generate` and
  `utils.google.image.edit` for Gemini Nano Banana image models, with
  generated-assets persistence, generic image artifact registration, run-plan
  grant coverage, official-doc capability metadata, model-specific aspect
  ratios/image sizes/input counts, inline 20 MB request preflight, and official
  output image budget estimates. Google Gemini image `auth.test` is non-billable
  format-only because the provider does not document a free live image probe.
- Added Ideogram actions `utils.ideogram.image.generate` and
  `utils.ideogram.image.remix` for Ideogram 4.0, with multipart API execution,
  immediate temporary URL download, generated-assets persistence, generic image
  artifact registration, run-plan grant coverage, exact 23-resolution metadata,
  `FLASH` exclusion, signed JPEG/PNG/WEBP remix upload validation at the
  official 10 MB cap, and official per-output rendering-speed budget estimates
  reconciled against returned image count. Ideogram `auth.test` is non-billable
  format-only because the provider does not document a free live image probe.
- Added BytePlus Seedream actions `utils.byteplus.image.generate` and
  `utils.byteplus.image.edit` through the reusable `byteplus-ark` ModelArk
  wrapper, with generated-assets persistence, generic image artifact
  registration, run-plan grant coverage, official model/region/size/reference
  validation, priced Seedream 5 Lite / 4.5 / 4.0 budget estimates, and
  successful-output cost reconciliation. BytePlus `auth.test` is non-billable
  format-only because ModelArk does not document a free media credential probe.
- Documented the video-provider gates for the media generation program:
  BytePlus Seedance and Google Veo remain deferred/build-gated until operator
  account, billing, model activation/scope, and stored-credential checks are
  complete; Kling is explicitly deferred until official rendered docs or
  operator in-console evidence verify the executable API contract.

## 1.0.0 - 2026-05-26

- Pivoted the product architecture to StackOS: project-scoped plugins,
  workflow templates, run plans, generic resources/artifacts, no-secret auth
  references, context, learnings, experiments, decisions, and action execution.
- Reframed SEO as a first-party plugin domain rather than the core product
  shape.
- Simplified the UI direction around generic StackOS renderers.
- Updated documentation to describe the current clean-cut architecture.
