# Media Generation Provider Shortlist

Status: shortlist plus first executable delivery, researched 2026-06-09 and
updated during connector delivery. `utils.image.generate` / `utils.image.edit`
(OpenAI) and provider-specific xAI Imagine actions
`utils.xai.image.generate`, `utils.xai.image.edit`, and
`utils.xai.video.generate` are executable. Provider-neutral
`utils.video.generate` remains deferred (`deferred-video-backend-selection`).

Shape of the list: the top four per category by leaderboard rank and API
maturity, plus operator-requested additions (Seedream and Grok on images,
Grok on video). Six image entries, five video entries.

Selection rules applied:

- Root providers only. The company that trains the model and runs its own
  first-party API. Aggregators (fal.ai, Replicate, Together, Krea, Freepik,
  Runway-as-reseller) are never the access path.
- Publicly accessible today. Self-serve signup and billing. Limited betas,
  waitlists, app-only products, and China-enterprise-only APIs are excluded.
- Ranked by the Artificial Analysis and LMArena leaderboards (read
  2026-06-09) plus API maturity and output size control.

## StackOS Integration Shape

Media generation uses provider-specific actions with reusable provider
modules underneath. The provider module owns the vendor API contract; the
StackOS action connector owns validation, credential resolution handoff,
budget estimate, generated-asset persistence, and action audit metadata.
Follow [`media-generation-runbook.md`](./media-generation-runbook.md) for the
delivery checklist, gate order, capability metadata requirements, and
verification signoff for every media-generation tool.

Use this split for each delivery:

- `stackos/integrations/<provider>.py`: reusable first-party API wrapper.
  It must inherit `BaseIntegration`, expose typed methods for the provider's
  actual operations, keep secrets inside daemon process memory, and record
  provider request/job ids through the existing audit path.
- `stackos/actions/<provider>.py`: thin decision-free adapter from the utils
  action manifest to the reusable wrapper. It must not choose strategy or
  silently downgrade unsupported modes.
- `stackos/plugins/manifest.py`: one provider manifest and independent action
  entries per provider operation. Agents discover what is possible from action
  schemas and capability metadata, not from hidden connector logic.
- `stackos/integrations/__init__.py` and `stackos/actions/__init__.py`: explicit
  registration only after wrapper, connector, tests, docs, and changelog are
  delivered together.

Keep the provider-neutral `video-generation` provider and
`utils.video.generate` action only as the deferred placeholder. Concrete video
delivery uses per-vendor providers/actions such as `google-veo`,
`byteplus-seedance`, `alibaba-wan`, `kling`, and `xai-imagine`.

Each media action must list `capability_metadata` in its config:

- `modalities`: text, image, video, audio inputs accepted by that action.
- `modes`: generation/editing modes such as `text-to-image`,
  `image-to-image`, `text-to-video`, `image-to-video`, `reference-to-video`,
  `video-edit`, and `video-extend`.
- `models`: model ids with per-model supported sizes, aspect ratios,
  resolutions, durations, reference limits, output formats, and pricing unit.
- `execution`: `sync` for image endpoints, `async` for video job endpoints,
  including submit/poll/download/persist requirements and output URL expiry.
- `safety`: watermark/SynthID behavior, person/face restrictions, commercial
  caveats, and moderation/error fields.
- `unsupported_provider_features`: official provider features that exist but
  this StackOS action intentionally does not expose yet.
- `docs`: official provider URLs used for the contract review.

Each provider integration is delivered as its own evidence-backed commit:
contract review, connector code, manifest/action metadata, tests,
documentation, independent verifier signoff, and tracker closeout for that
provider. Do not mix multiple providers in one delivery commit unless the
operator explicitly approves a grouped release.

## Registration Map

Seven new registrations cover the eleven shortlisted models. OpenAI is
already registered and integrated.

| Platform | Sign up / console | Billing model | Covers |
| --- | --- | --- | --- |
| OpenAI Platform | <https://platform.openai.com/> | Prepaid credits / pay-as-you-go | GPT Image 2 (already integrated) |
| Reve | <https://api.reve.com/> (console) | Prepaid credit packs (7,500 credits = $10 reported) | Reve 2.0 (image) |
| Google AI Studio + Cloud billing | <https://aistudio.google.com/> (API key); paid tier required for Veo | Pay-as-you-go through the linked Google Cloud billing account | Veo 3.1 (video) + Nano Banana 2 (image) |
| Ideogram | <https://ideogram.ai/> (API access), pricing <https://ideogram.ai/api-pricing/> | Pay-as-you-go API billing | Ideogram 4.0 (image) |
| BytePlus ModelArk | <https://console.byteplus.com/> (ModelArk product) | Pay-as-you-go; organization/real-name verification required; free trial quotas | Seedance 2.0 (video) + Seedream (image) |
| Alibaba Cloud Model Studio | <https://www.alibabacloud.com/> console, Model Studio, Singapore region for international | Pay-as-you-go; API keys are region-locked (Singapore vs Beijing) | WAN 2.7 (video); also Wan 2.7 Image (runner-up) and HappyHorse when it leaves limited beta |
| Kling Open Platform (Kuaishou) | <https://app.klingai.com/global/dev/document-api> | Separate paid API plan (resource packages), distinct from consumer credits | Kling 3.0 (video) |
| xAI Console | <https://console.x.ai/> | Prepaid credits | Grok Imagine video + Grok Imagine image |

## Image Generation — Top 4 Plus Requested Additions

### 1. GPT Image 2 — OpenAI (already integrated)

- Status: executable in StackOS today (`utils.image.generate`,
  `utils.image.edit`). #1 on both LMArena text-to-image (1385) and image-edit
  (1465) boards as of 2026-06-05, and #1 on Artificial Analysis.
- Models: `gpt-image-2` (snapshot `gpt-image-2-2026-04-21`),
  `gpt-image-1.5`, `gpt-image-1`, and `gpt-image-1-mini` (cheap drafts).
  `gpt-image-1.5`, `gpt-image-1`, and `gpt-image-1-mini` keep configurable
  `input_fidelity`.
- Provider modes: Image API generations, Image API edits with up to 16 input
  reference images, mask-based edits, streaming partial images, and Responses
  API multi-turn image generation/editing. `gpt-image-2` always uses high input
  fidelity for image inputs; omit the `input_fidelity` parameter for that
  model. No transparent background on `gpt-image-2`.
- StackOS exposed modes today: `utils.image.generate` for text-to-image and
  `utils.image.edit` for generated-asset reference image composition/editing.
  The current connector does not expose mask uploads, JSON URL/file-id image
  references, Responses API image flows, streaming partial images,
  `background`, `moderation`, or `output_compression` controls.
- Limits: prompts are capped at 32,000 characters. Edit input refs are
  daemon-local generated-assets files in png/jpg/webp format, up to 16 images
  and 50 MB per image before vendor upload.
- Size control: free `WxH` — both edges divisible by 16, max edge 3840,
  ratio at most 3:1, total pixels 655,360–8,294,400. True 9:16 / 4:5 / 16:9 /
  1.91:1 outputs. Outputs above 2K-class pixel count are marked experimental
  by OpenAI. StackOS currently exposes only `auto`, `1024x1024`,
  `1536x1024`, and `1024x1536`; custom `WxH` stays unsupported until the
  `gpt-image-2` output-token calculator is modeled for budget pre-checks.
- Output and persistence: GPT Image API returns base64 image data; StackOS
  persists generated bytes under generated assets, registers generic image
  artifacts during repository-backed action execution, and returns artifact
  URLs/ids to agents.
- Pricing: GPT Image 2 output pricing is $0.006 (low) to $0.211 (high) per
  1024x1024 image in the official calculator/table; input text and edit-image
  tokens are additional and invoices remain the source of truth.
- Documentation note: the official image guide and `gpt-image-2` model page
  document Image API generation/edit support for `gpt-image-2`; some generated
  API-reference enum snippets can lag that model listing. Treat the guide and
  model page as the source of truth for support, and re-check before changing
  StackOS defaults.
- Docs: guide <https://developers.openai.com/api/docs/guides/image-generation>,
  model page <https://developers.openai.com/api/docs/models/gpt-image-2>,
  image reference <https://developers.openai.com/api/docs/api-reference/images>.

### 2. Reve 2.0 — Reve

- Status: public beta API. #2 on LMArena text-to-image (1273), released
  2026-06-03 — layout-first architecture that plans structured layout before
  rendering. The exact API schema and pricing facts below were observed on
  official browser-rendered Reve console docs; build remains gated on
  independent verifier signoff plus operator account/billing/credential
  confirmation.
- API shape: synchronous JSON API under `https://api.reve.com/v1/image/*`,
  bearer API key. `Accept` can request `application/json` (base64 PNG image
  plus metadata) or direct `image/png`, `image/jpeg`, or `image/webp` bytes
  with metadata in `X-Reve-*` headers.
- Modes: `POST /image/create` text-to-image, `POST /image/edit` with one
  base64 reference image plus `edit_instruction`, and `POST /image/remix` with
  1-6 base64 reference images plus prompt. No mask/inpainting parameter is
  documented; transparency is available through postprocessing
  `remove_background`, not a native transparent generation mode.
- Size control: `aspect_ratio` enum `16:9, 9:16, 3:2, 2:3, 4:3, 3:4, 1:1`.
  Create defaults to `3:2`; edit defaults to the reference image's aspect
  ratio; remix lets the model choose by default. Fit-image postprocessing can
  constrain longest side/width/height up to 4096.
- Model/version controls: create supports `latest` and
  `reve-create@20250915`; edit supports `latest-fast`, `latest`,
  `reve-edit-fast@20251030`, and `reve-edit@20250915`; remix supports
  `latest-fast`, `latest`, `reve-remix-fast@20251030`, and
  `reve-remix@20250915`.
- Postprocessing: create/edit/remix accept `upscale` (2x, 3x, 4x),
  `remove_background`, free `fit_image`, and named `effect` values from
  `GET /v1/image/effect`. `test_time_scaling` accepts 1-15 and increases
  credits used.
- Output and pricing: responses include `request_id`, `content_violation`,
  `credits_used`, and `credits_remaining`. Minimum purchase is $10 for 7,500
  credits. Create costs 18 credits (~$0.024); edit/remix cost 30 credits
  (~$0.04); fast create is marked "Coming soon"; fast edit/remix cost 5
  credits (~$0.007); postprocessing is variable.
- Docs: create <https://api.reve.com/console/docs/create>, edit
  <https://api.reve.com/console/docs/edit>, remix
  <https://api.reve.com/console/docs/remix>, pricing
  <https://api.reve.com/console/pricing>, announcement
  <https://blog.reve.com/posts/announcing-reve-2.0/>.
  Evidence note: public fetch/curl can return only the Reve console app shell.
  This contract was observed with Playwright-rendered official pages and was
  independently verified against official docs on 2026-06-10. Build remains
  gated on operator account, billing, and stored-credential confirmation.

### 3. Nano Banana 2 — Google (`gemini-3.1-flash-image`)

- Status: GA on the Gemini API; #3 LMArena text-to-image. Released
  2026-02-26. Vertex AI parity is not signed off in this contract yet.
- Models: `gemini-3.1-flash-image` (Nano Banana 2),
  `gemini-3-pro-image` (Nano Banana Pro), `gemini-2.5-flash-image` (Nano
  Banana).
- API shape: synchronous Gemini `generateContent` on the Developer API
  (`x-goog-api-key`). Inline image outputs should be persisted from returned
  MIME/base64 parts; docs do not define a selectable output format.
- Modes: text-to-image, text+image editing, reference composition, Image
  Search grounding, and strong multilingual in-image text. Gemini 3 image
  models support up to 14 references; the 3.1 Flash docs split this as up to
  10 objects plus up to 4 characters. `gemini-3.1-flash-image` also supports
  video input context (YouTube URL or Files API upload) for image generation;
  expose that only if the connector implements safe video input handling.
  Audio inputs are not supported for image generation. No transparency;
  SynthID watermark on all generated images.
- Size control: Gemini REST examples place image controls under
  `generationConfig.responseFormat.image.aspectRatio` and
  `generationConfig.responseFormat.image.imageSize` (SDKs may expose helper
  aliases such as `imageConfig`). Supported aspect ratios are
  `1:1, 2:3, 3:2, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9, 1:4, 4:1, 1:8, 8:1`
  times image sizes `512 | 1K | 2K | 4K` for
  `gemini-3.1-flash-image`; `gemini-3-pro-image` supports
  `1:1, 2:3, 3:2, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9` and `1K | 2K | 4K`.
  `gemini-2.5-flash-image` returns fixed 1024-class dimensions by aspect
  ratio.
- Inputs: image inputs support PNG, JPEG, WEBP, HEIC, and HEIF. Inline
  requests must stay under 20 MB; the Files API supports larger media but files
  auto-delete after 48 hours.
- StackOS v1 scope decision: implement the Gemini API path first. Expose
  text/image-reference generation and editing; list video input and Vertex AI
  parity as unsupported until the connector implements their separate upload,
  retention, and credential behavior.
- Pricing: `gemini-3.1-flash-image` output prices are $0.045 (0.5K),
  $0.067 (1K), $0.101 (2K), and $0.151 (4K) per image, plus token-based input
  cost. `gemini-2.5-flash-image` is documented at $0.039/image standard.
- Docs: image generation <https://ai.google.dev/gemini-api/docs/image-generation>,
  image inputs <https://ai.google.dev/gemini-api/docs/image-understanding>,
  Files API <https://ai.google.dev/gemini-api/docs/files>, pricing
  <https://ai.google.dev/gemini-api/docs/pricing>.

### 4. Ideogram 4.0 — Ideogram

- Status: GA first-party API (`POST /v1/ideogram-v4/generate`), released
  2026-06-03; LMArena text-to-image top-10. First open-weight Ideogram
  (weights non-commercial; commercial use via the API).
- API shape: synchronous multipart API with `Api-Key` header. Generate v4
  returns temporary image URLs; connector must download and persist promptly.
- Modes: v4 text-to-image with either `text_prompt` or structured
  `json_prompt`, v4 remix, magic-prompt/describe helpers, upscale, remove
  background, and 3.0-specific inpaint/reframe/replace-background/
  transparent-generation endpoints. The generic `/edit` endpoint is legacy and
  only documents `V_2` / `V_2_TURBO`, so do not present it as a v4 edit path.
  Treat transparency and background removal as explicit endpoint features, not
  as a blanket v4 generation guarantee.
- Size control: `resolution` enum of documented 2K Ideogram 4.0 values from
  `2048x2048` to panoramics like `3072x1024`; `rendering_speed` allows
  `TURBO | DEFAULT | QUALITY`. The docs list `FLASH`, but note that v4
  requests with `FLASH` currently return 400.
- StackOS v1 scope decision: start with v4 generate and v4 remix as the
  executable image actions. Keep legacy edit, v3 background utilities, remove
  background, and upscale as separate endpoint-specific actions only when their
  contracts are implemented; omit `FLASH` from executable schemas until the
  provider endpoint accepts it.
- Pricing (official): $0.03 (Turbo) / $0.06 (Default) / $0.10 (Quality) per
  image for generate/remix/edit/reframe/replace-background; instructional edit
  is $0.20/image; Ideogram Upscale up to 2X is $0.06/input image, while Topaz
  Upscale is separately priced by output resolution; describe is
  $0.01/input image.
- Docs: generate v4
  <https://developer.ideogram.ai/api-reference/api-reference/generate-v4>,
  remix v4
  <https://developer.ideogram.ai/api-reference/api-reference/remix-v4>,
  legacy edit <https://developer.ideogram.ai/api-reference/api-reference/edit>,
  replace background
  <https://developer.ideogram.ai/api-reference/api-reference/replace-background-v3>,
  remove background
  <https://developer.ideogram.ai/api-reference/api-reference/remove-background>,
  upscale <https://developer.ideogram.ai/api-reference/api-reference/upscale>,
  pricing <https://ideogram.ai/api-pricing/>.

### 5. Seedream 4.5 / 5.0 Lite — ByteDance (requested include)

- Status: GA on BytePlus ModelArk (international first-party platform).
  Seedream 4.5 shipped Nov 2025; 5.0 Lite shipped early 2026; full 5.0 is
  app-only so far. Not on the current LMArena top-12 but the strongest
  reference-preserving editor of the non-Western providers.
- API shape: synchronous `POST /images/generations` on ModelArk data-plane
  base URLs such as `https://ark.ap-southeast.bytepluses.com/api/v3` and
  `https://ark.eu-west.bytepluses.com/api/v3`, bearer API-key auth. Seedream
  5 Lite / 4.5 / 4.0 can optionally stream output; StackOS v1 should either
  implement streaming explicitly or list it as unsupported.
- Models: use exact connector model ids from the rendered official ModelArk
  tables. The verified v5 ids are `seedream-5-0-260128` and the Lite alias
  `seedream-5-0-lite-260128`; `seedream-5-0-lite` is a family/page label, not
  an exact action model id. Other verified ids are `seedream-4-5-251128`,
  `seedream-4-0-250828`, `seedream-3-0-t2i-250415`, and
  `seededit-3-0-i2i-250628`.
- Modes: text-to-image, single image-to-image, multi-reference composition,
  batch/sequential generation, and dense-text/typography-oriented generation.
- Size control: `size` accepts abstract resolution or exact `WxH`. For
  Seedream 5/4.5/4.0 family, default is `2048x2048`, total pixels must be
  3,686,400-16,777,216, and aspect ratio must be 1/16-16. Shortcuts include
  `2K | 3K | 4K` for Seedream 5 Lite, with older/current variants exposing
  `1K | 2K | 4K` or `2K | 4K`.
- Inputs and outputs: prompt recommended under 600 English words. Inputs may
  be URL or base64; general image formats include JPEG/PNG, while Seedream
  5 Lite / 4.5 / 4.0 also support WEBP/BMP/TIFF/GIF/HEIC/HEIF. Images must be
  >14 px, <=30 MB, <=6000x6000; Seedream 5 Lite supports up to 14 reference
  images. Outputs can be 24-hour URLs or `b64_json`; `output_format=png|jpeg`
  is supported only for Seedream 5 Lite.
- Watermark, billing, and safety: watermark defaults true and can be disabled.
  Billing is per successfully generated output image; failed or moderated
  outputs are not billed. Official pricing lists `seedream-5-0-lite-260128`
  at $0.035/image, `seedream-4-5-251128` at $0.04/image,
  `seedream-4-0-250828` at $0.03/image, and `seededit-3-0-i2i-250628` at
  $0.03/image. Treat commercial-rights posture as governed by BytePlus/ModelArk
  terms until legal review confirms customer-facing use.
- Docs: image API <https://docs.byteplus.com/en/docs/ModelArk/1541523>,
  Seedream 4.0–5.0 tutorial <https://docs.byteplus.com/en/docs/ModelArk/1824121>,
  model list <https://docs.byteplus.com/en/docs/ModelArk/1330310>, billing
  <https://docs.byteplus.com/en/docs/ModelArk/1544106>, base URL/auth
  <https://docs.byteplus.com/en/docs/ModelArk/1298459>. Evidence note:
  rendered official BytePlus docs verify endpoint/auth shape, exact model ids,
  size/reference limits, 24-hour URL expiry, output formats, watermark
  defaults, and pricing. Build remains gated on operator account, billing, and
  stored-credential confirmation.

### 6. Grok Imagine Image — xAI (requested include)

- Status: GA on the xAI API. StackOS v1 chooses
  `grok-imagine-image-quality` for quality output. xAI pricing docs also list
  cheaper `grok-imagine-image`; it is intentionally not exposed in v1.
  `grok-imagine-image-pro` is deprecated and should not be used for new
  StackOS actions.
  LMArena text-to-image #7 (1234) — it would have been next in line for the
  merit list anyway.
- API shape: JSON requests under `https://api.x.ai/v1`, bearer auth, and
  OpenAI-compatible image generation for the generation endpoint. Image editing
  uses xAI JSON (`image.url` or base64 data URI), not OpenAI SDK multipart
  `images.edit()`.
- Modes: text-to-image (`n` batch generation), single-image editing,
  multi-turn editing by feeding the prior output URL back as input, style
  transfer, and multi-image editing with up to 3 source images. No official
  mask/inpainting or transparency support verified.
- Size control: `aspect_ratio` enum
  `1:1, 16:9, 9:16, 4:3, 3:4, 3:2, 2:3, 2:1, 1:2, 19.5:9, 9:19.5, 20:9, 9:20, auto`;
  resolution tiers `1k | 2k`.
- Output and pricing: images return temporary URLs by default, with base64
  output also supported. Official pricing lists `grok-imagine-image-quality`
  image output at $0.05 for 1k and $0.07 for 2k, with image input charged at
  $0.01/image for edit/reference inputs. Watermark policy is not verified from
  official image docs.
- StackOS v1 scope decision: executable actions use
  `grok-imagine-image-quality` only. `utils.xai.image.generate` supports
  text-to-image; `utils.xai.image.edit` supports single/multi-image JSON edits
  with generated-assets PNG/JPEG refs. Cheaper `grok-imagine-image`,
  mask/inpainting, transparency, and deprecated `grok-imagine-image-pro` are
  not exposed.
- Docs: generation
  <https://docs.x.ai/developers/model-capabilities/images/generation>, editing
  <https://docs.x.ai/developers/model-capabilities/images/editing>,
  multi-image editing
  <https://docs.x.ai/developers/model-capabilities/images/multi-image-editing>,
  models <https://docs.x.ai/developers/models>, pricing
  <https://docs.x.ai/developers/pricing>.

Image runners-up (not shortlisted): FLUX.2 [pro]/[max] (Black Forest Labs —
free WxH up to 4MP, unified gen+edit, ≤8 refs, api.bfl.ai), Wan 2.7 Image
(free WxH to 4K, bbox edits, hex `color_palette` parameter — free rider on the
Alibaba registration), Luma Uni-1.1 (reasoning-based generation, ≤9 refs),
Recraft V4.1 (vector/SVG output).

## Video Generation — Top 4 Plus Requested Addition

All five video APIs are asynchronous (submit job, poll status, download
output) — the connector design must use the polling pattern anticipated by the
deferred `utils.video.generate` contract.

### 1. Seedance 2.0 — ByteDance

- Status: public API on BytePlus ModelArk; organization/model activation and
  terms acceptance are required before production use. #1 on both arenas for
  text-to-video and #1-2 for image-to-video.
- API shape: async task API on the ap-southeast ModelArk data plane:
  `POST /contents/generations/tasks`, `GET /contents/generations/tasks/{id}`,
  list tasks, and delete/cancel task. Connector must submit, poll/retrieve,
  download `content.video_url`, persist bytes, and record task id. Task ids are
  retained 7 days; output video and optional last-frame URLs expire after
  24 hours. Default task timeout is 172,800 seconds and accepted timeout values
  are 3,600-259,200 seconds.
- Models: `dreamina-seedance-2-0-260128`,
  `dreamina-seedance-2-0-fast-260128`; docs also list
  `seedance-1-5-pro-251215`, `seedance-1-0-pro-250528`, and
  `seedance-1-0-pro-fast-251015`.
- Modes: text-to-video, first-frame image-to-video, first+last-frame
  image-to-video, Seedance 2.0 multimodal reference generation using images,
  videos, and audio plus optional prompt, video modification/editing, video
  extension, synchronized mono audio via `generate_audio`, and optional
  `return_last_frame`. Audio cannot be input alone.
- Size control: Seedance 2.0 duration is 4-15 s or `-1`; output is 24 fps.
  Resolutions are `480p | 720p | 1080p`, but Fast excludes 1080p. Ratios are
  `16:9`, `4:3`, `1:1`, `3:4`, `9:16`, `21:9`, and `adaptive`.
- Inputs: image refs may be URL, base64, or asset id; common formats include
  JPEG/PNG/WEBP/BMP/TIFF/GIF plus HEIC/HEIF for 1.5/2.0. Image dimensions must
  be 300-6000 px, each under 30 MB, request body <=64 MB; first frame uses
  1 image, first+last uses 2 images, and Seedance 2.0 multimodal references
  allow 1-9 images. Video refs are URL/asset id only, MP4/MOV, 480p/720p/1080p,
  2-15 s each, up to 3 refs, total <=15 s, aspect 0.4-2.5, dimensions
  300-6000 px, pixels 409,600-2,086,876, <=50 MB, and 24-60 fps. Audio refs
  are WAV/MP3, 2-15 s each, up to 3 refs, total <=15 s, <=15 MB each.
- Watermark, safety, and billing: watermark defaults false. Seedance 2.0 docs
  prohibit direct reference image/video uploads containing real human faces
  except approved/trusted flows. Video terms put rights, portrait/voice
  clearance, legality, and suitability on the user; inputs/outputs are not used
  for base-model training unless separately consented. Only successful videos
  are charged; billing is token unit price times completion token consumption,
  with actual usage from `usage.completion_tokens`.
- Status handling: poll until one of `queued`, `running`, `cancelled`,
  `succeeded`, `failed`, or `expired`. Persist successful media immediately
  because provider URLs expire after 24 hours.
- Docs: create task <https://docs.byteplus.com/en/docs/ModelArk/1520757>,
  retrieve task <https://docs.byteplus.com/en/docs/ModelArk/1521309>, list
  tasks <https://docs.byteplus.com/en/docs/ModelArk/1521675>, cancel/delete
  task <https://docs.byteplus.com/en/docs/ModelArk/1521720>, model list
  <https://docs.byteplus.com/en/docs/ModelArk/1330310>, billing
  <https://docs.byteplus.com/en/docs/ModelArk/1544106>, video terms
  <https://docs.byteplus.com/en/docs/ModelArk/Specific_Terms_for_the_BytePlus_Video_Generation_Model_Services>.
  Evidence note: rendered official BytePlus docs verify endpoint/auth shape,
  exact model ids, media limits, task retention, URL expiry, watermark behavior,
  task statuses, usage fields, timeout bounds, and billing formulas. Build
  remains gated on operator account, billing, and stored-credential
  confirmation.

### 2. WAN 2.7 — Alibaba

- Status: skipped/deferred for StackOS v1. Alibaba's lineup confirms
  `wan2.7-t2v`, `wan2.7-i2v`, `wan2.7-r2v`, and `wan2.7-videoedit`, but the
  accessible public API references do not fully verify executable WAN 2.7
  schemas across t2v/r2v/videoedit. Do not build a connector from the partial
  facts below.
- API shape: asynchronous DashScope HTTP API. Text-to-video uses
  `POST /api/v1/services/aigc/video-generation/video-synthesis` with
  `X-DashScope-Async: enable`; image-to-video uses the same submit/poll model.
  Save `task_id`, poll status, download output, and persist immediately.
  `task_id` and returned video URLs are valid for 24 hours.
- Modes: text-to-video (multi-shot via `shot_type`), image-to-video with
  `first_frame` / `last_frame` / `first_clip` video continuation,
  audio-driven generation (`driving_audio`), reference-to-video, instruction
  video editing, native audio. Lip-sync via separate `wan2.2-s2v`.
- Size control: `resolution` `720P | 1080P` (default 1080P); output snaps to
  the input aspect ratio with width/height as multiples of 16; `duration`
  2–15 s (r2v/edit 2–10 s); 30 fps. Text-to-video uses exact `size` strings
  such as `1280*720`, `720*1280`, `960*960`, `1088*832`, `832*1088`,
  `1920*1080`, `1080*1920`, `1440*1440`, `1632*1248`, and `1248*1632`,
  depending on model/resolution tier.
- Pricing: pricing gaps alone are no longer considered blockers by operator
  direction, but WAN remains skipped because the executable API docs are not
  complete enough. Older wan2.6 references list $0.10/s (720P), $0.15/s
  (1080P), and flash tiers from $0.025/s. Partial docs show `watermark`
  defaults false and output URLs expire after 24 h.
- Docs: lineup
  <https://www.alibabacloud.com/help/en/model-studio/video-generate-edit-model/>,
  i2v reference
  <https://www.alibabacloud.com/help/en/model-studio/image-to-video-general-api-reference>,
  t2v reference
  <https://www.alibabacloud.com/help/en/model-studio/text-to-video-api-reference>,
  pricing <https://www.alibabacloud.com/help/en/model-studio/model-pricing>.

### 3. Veo 3.1 — Google

- Status: Google docs mark Veo 3.1 / 3.1 Fast / 3.1 Lite as preview models;
  Veo 3 and Veo 2 are the stable Gemini API model families. Preview model
  behavior/pricing can change, so first delivery should explicitly choose
  whether to expose only stable `veo-3.0-*`/`veo-2.0-*` or include the
  preview `veo-3.1-*` surface. #4 on LMArena text-to-video; strongest Western
  entry on image-to-video boards.
- API shape: asynchronous Gemini `predictLongRunning`; connector must submit,
  poll until `done=true`, download output, and persist before the 2-day
  server-side retention window expires.
- Models: `veo-3.1-generate-preview`, `veo-3.1-fast-generate-preview`,
  `veo-3.1-lite-generate-preview`, stable `veo-3.0-generate-001`,
  `veo-3.0-fast-generate-001`, and `veo-2.0-generate-001`.
- Modes: text-to-video, image-to-video, first+last frame interpolation, up to
  3 reference images, provider-documented video-to-video for Veo 3.1 variants,
  extend +7 s per step up to 20 steps, and native audio generation from prompt
  cues. Extension is limited to prior Veo-generated or referenced videos from
  the last 2 days. If StackOS v1 defers arbitrary video-to-video, list it as an
  unsupported provider feature rather than implying the provider lacks it.
- Size control: `aspectRatio` `16:9 | 9:16`; 24 fps. Veo 3.1/3.1 Fast support
  `resolution` `720p | 1080p | 4k`; Lite supports `720p | 1080p`; Veo 3
  stable supports `720p | 1080p`; Veo 2 supports `720p`. Veo 3.1 preview
  models support `durationSeconds` `4 | 6 | 8` with 8 s required for 1080p/4k
  where supported; Veo 3 stable is documented as 8 s; Veo 2 supports
  `5 | 6 | 8`. Veo 2 can return 1 or 2 videos per request; Veo 3/3.1 return 1.
- Safety and region: SynthID watermark, safety filters, and memorization
  checks apply. EU/UK/CH/MENA restrict `personGeneration` for Veo 3/3.1 to
  `allow_adult`.
- Pricing: budget unit is generated seconds. Veo 3.1 Standard is $0.40/s for
  720p/1080p and $0.60/s for 4K; Fast is $0.10/s 720p, $0.12/s 1080p,
  $0.30/s 4K; Lite is $0.05/s 720p and $0.08/s 1080p. Audio is included.
  Google pricing docs warn that preview models may change before becoming
  stable and can have more restrictive rate limits.
- StackOS v1 scope decision: use stable `veo-3.0-generate-001` as the default
  model. The action may expose Veo 3.1 preview model ids only with explicit
  preview capability metadata and no silent default to preview. If v1 defers
  video-to-video, extension, or reference-image modes, list them in unsupported
  provider features while preserving the verified provider capability facts in
  this contract.
- Docs: <https://ai.google.dev/gemini-api/docs/video>, pricing
  <https://ai.google.dev/gemini-api/docs/pricing>.

### 4. Kling 3.0 — Kuaishou

- Status: GA on the Kling Open Platform (global developer API; paid API plan
  purchased separately from consumer credits). Artificial Analysis
  text-to-video #4–7 cluster (Pro/Std variants 1094–1104), above Veo 3.1
  there; LMArena image-to-video top-12. Released 2026-02-04.
- Modes: text-to-video, image-to-video, multi-shot storyboard (up to 6 shots
  per clip), native audio with multilingual lip-synced dialogue, motion
  control/transfer, lip-sync endpoint; the 3.0 Omni ("O3") variant adds
  reference-to-video (character traits + voice from a reference video) and
  video editing.
- Size control: API pricing distinguishes `720p` and `1080p` tiers; aspect
  ratios `16:9, 9:16, 1:1` (historical official enum — mirror-sourced, the
  developer docs block automated review); duration up to 15 s. Consumer-app
  "4K/60fps" claims are not present in API tiers.
- Pricing: $0.084–$0.168/s reported from the developer pricing pages
  (credit-schedule: 6–12 credits/s by tier and audio). Confirm in-console
  during deep review.
- Caveats: docs and pricing pages return HTTP 446 to automated tools —
  everything above the pricing line needs in-console verification after
  registration; watermark and commercial terms unverified.
- Docs: developer docs
  <https://app.klingai.com/global/dev/document-api>, dev pricing
  <https://kling.ai/dev/pricing>, 3.0 launch
  <https://ir.kuaishou.com/news-releases/news-release-details/kling-ai-launches-30-model-ushering-era-where-everyone-can-be>.

### 5. Grok Imagine Video — xAI (requested include)

- Status: GA on the xAI API; official docs and pricing page list
  `grok-imagine-video` output at $0.05/s for 480p and $0.07/s for 720p, with
  image inputs charged at $0.002/image. xAI pricing docs also list
  image-to-video-only `grok-imagine-video-1.5-preview`; it is intentionally not
  exposed in v1 because StackOS is delivering the stable provider-specific
  generate surface first.
- API shape: async REST. Submit to `/v1/videos/generations`,
  `/v1/videos/edits`, or `/v1/videos/extensions`, receive `request_id`, then
  poll `GET /v1/videos/{request_id}` until `pending`, `done`, `expired`, or
  `failed`. Completed videos return temporary URLs; connector must download and
  persist promptly.
- Modes: text-to-video, image-to-video, reference-to-video, video editing, and
  video extension. Each request supports exactly one mode; `image` and
  `reference_images` cannot be mixed. Video editing retains the input duration
  and caps input/output at 8.7 s. Native audio is marketed for Imagine, but no
  separate audio input/control parameter was verified.
- Size control: `resolution` `480p | 720p` only (the shortlist's lowest
  ceiling); `aspect_ratio` `1:1, 16:9, 9:16, 4:3, 3:4, 3:2, 2:3`; duration
  1–15 s for generation/image/reference modes. Video editing does not support
  custom duration, aspect ratio, or resolution; output matches input aspect and
  caps resolution at 720p.
- Errors: failed jobs include `error.code` values such as
  `invalid_argument`, `permission_denied`, `failed_precondition`,
  `service_unavailable`, and `internal_error`; auth/model/rate-limit failures
  can occur synchronously before a job id is created.
- StackOS v1 scope decision: `utils.xai.video.generate` exposes text-to-video,
  image-to-video, and reference-to-video through `grok-imagine-video`. Video
  editing and extension are separate endpoint families and remain unsupported
  until they get dedicated actions. `grok-imagine-video-1.5-preview` remains
  unsupported until preview-model policy and image-to-video-only behavior are
  reviewed. Metadata gaps such as exact URL expiry, fps, audio behavior, and
  watermark policy are documented limitations, not build blockers.
- Docs: <https://docs.x.ai/developers/model-capabilities/video/generation>,
  image-to-video
  <https://docs.x.ai/developers/model-capabilities/video/image-to-video>,
  video editing
  <https://docs.x.ai/developers/model-capabilities/video/editing>,
  reference-to-video
  <https://docs.x.ai/developers/model-capabilities/video/reference-to-video>,
  video extension
  <https://docs.x.ai/developers/model-capabilities/video/extension>, models
  <https://docs.x.ai/developers/models>, pricing
  <https://docs.x.ai/developers/pricing>.

Video runners-up and watch list: HappyHorse 1.0 (Alibaba Taotian — #2 on all
boards, v2v editing, 7-language lip sync; excluded only because the Model
Studio API is limited beta — watch
<https://www.alibabacloud.com/help/en/model-studio/happyhorse-video-edit-api-reference>),
Vidu Q3 (value pick with audio + full ratio set, platform.vidu.com), LTX-2.3
(only native-4K root API; 16:9/9:16 only, docs.ltx.video), Luma Ray3.2
(Modify video-to-video restyle, 6 aspect ratios), SwitchX by Beeble
(special-purpose video-to-video VFX: relighting and background/prop swaps,
<https://developer.beeble.ai/>).

Excluded as not publicly accessible or not root: Sora 2 (API shuts down
2026-09-24, no successor), Midjourney (no public API), Pika (official API path
is fal.ai, an aggregator), Tencent Hunyuan (China enterprise-only API),
Moonvalley (waitlist).

## Integration Readiness Notes

How the shortlist maps onto the StackOS pattern when integration starts:

- Each platform becomes a provider manifest in the utils plugin with an
  `api_key` auth method (all shortlisted platforms use bearer/API-key auth),
  mirroring `openai-images`. Google needs a note that the key comes from AI
  Studio with Cloud billing attached; Alibaba keys are region-locked; BytePlus
  requires organization verification before keys are issued; Kling requires a
  separate paid API plan.
- Provider shape: register per-vendor providers and actions. The
  provider-neutral `video-generation` provider remains only as a deferred
  placeholder until concrete video backends are delivered.
- Candidate video APIs are async job APIs; connectors need submit → poll →
  download → persist-to-generated-assets, with provider job ids recorded in
  action audit metadata. xAI is executable through this path; WAN remains
  skipped for v1 because the public executable API contract is not verified.
  WAN output URLs expire in 24 h where documented; Veo stores server-side for
  2 days.
- Image APIs return base64 or URLs synchronously; persistence mirrors the
  existing `openai-images` integration (bytes into generated assets, local
  artifact URLs, no payloads in agent responses).
- Watermark flags differ: WAN `watermark` defaults false, Seedance exposes a
  request parameter, Veo always embeds invisible SynthID, Nano Banana always
  embeds SynthID, Kling unverified. Record per-provider behavior in the
  contract review before exposing actions.
- Budget kinds: one per platform (`google-veo`, `byteplus-ark`,
  `alibaba-wan`, `kling`, `xai-imagine`, `reve`, `ideogram`) following the
  `openai-images` budget pattern, since pricing units differ (per second by
  resolution vs per image by quality).

## Open Verification Items For Deep Review

1. Kling 3.0 aspect-ratio enum, per-second pricing, watermark, and commercial
   terms — developer docs block automated review; verify in-console after
   registration.
2. BytePlus build scope: decide whether Seedream v1 exposes streaming and
   legacy model ids, and whether Seedance v1 exposes full multimodal
   edit/extension/callback behavior or starts with text/image-to-video polling.
3. HappyHorse Model Studio beta access criteria and GA timeline.
