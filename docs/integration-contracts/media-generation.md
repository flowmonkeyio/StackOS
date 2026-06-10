# Media Generation Provider Shortlist

Status: pre-integration shortlist, researched 2026-06-09. This is the first
gate before per-provider deep contract reviews and connector delivery. Nothing
in this document is executable yet; `utils.video.generate` remains deferred
(`deferred-video-backend-selection`) and `utils.image.generate` /
`utils.image.edit` (OpenAI) are the only executable media actions.

Selection rules applied:

- Root providers only. The company that trains the model and runs its own
  first-party API. Aggregators (fal.ai, Replicate, Together, Krea, Freepik,
  Runway-as-reseller) are never the access path.
- Publicly accessible today. Self-serve signup and billing. Limited betas,
  waitlists, app-only products, and China-enterprise-only APIs are excluded.
- Ranked by the Artificial Analysis and LMArena leaderboards (read
  2026-06-09) plus API maturity and output size control.

## Registration Map

Four new registrations cover all eight shortlisted models. OpenAI is already
registered and integrated.

| Platform | Sign up / console | Billing model | Covers |
| --- | --- | --- | --- |
| OpenAI Platform | <https://platform.openai.com/> | Prepaid credits / pay-as-you-go | GPT Image 2 (already integrated) |
| Google AI Studio + Cloud billing | <https://aistudio.google.com/> (API key); paid tier required for Veo | Pay-as-you-go through the linked Google Cloud billing account | Veo 3.1 (video) + Nano Banana 2 (image) |
| BytePlus ModelArk | <https://console.byteplus.com/> (ModelArk product) | Pay-as-you-go; organization/real-name verification required; free trial quotas | Seedance 2.0 (video) + Seedream (image) |
| Alibaba Cloud Model Studio | <https://www.alibabacloud.com/> console, Model Studio, Singapore region for international | Pay-as-you-go; API keys are region-locked (Singapore vs Beijing) | WAN 2.7 (video); also Wan 2.7 Image (runner-up) and HappyHorse when it leaves limited beta |
| xAI Console | <https://console.x.ai/> | Prepaid credits | Grok Imagine video + Grok Imagine image |

## Image Generation — Best 4

### 1. GPT Image 2 — OpenAI (already integrated)

- Status: executable in StackOS today (`utils.image.generate`,
  `utils.image.edit`). #1 on both LMArena text-to-image (1385) and image-edit
  (1465) boards as of 2026-06-05.
- Models: `gpt-image-2` (snapshot `gpt-image-2-2026-04-21`),
  `gpt-image-1.5` (keeps transparency + `input_fidelity`), `gpt-image-1-mini`
  (cheap drafts).
- Modes: generation, edits with up to 16 input reference images,
  always-high input fidelity on gpt-image-2. No transparent background on
  gpt-image-2.
- Size control: free `WxH` — both edges divisible by 16, max edge 3840,
  ratio at most 3:1, total pixels 655,360–8,294,400. True 9:16 / 4:5 / 16:9 /
  1.91:1 outputs.
- Pricing: $0.006 (low) to $0.211 (high) per 1024x1024-class image.
- Docs: guide <https://developers.openai.com/api/docs/guides/image-generation>,
  model page <https://developers.openai.com/api/docs/models/gpt-image-2>.

### 2. Nano Banana 2 — Google (`gemini-3.1-flash-image`)

- Status: GA on the Gemini API and Vertex AI; #3 LMArena text-to-image.
  Released 2026-02-26.
- Modes: text-to-image, conversational editing, up to 14 reference images
  (10 object + 4 character), Image Search grounding, strong multilingual
  in-image text. No transparency; SynthID watermark on all outputs.
- Size control: `imageConfig.aspectRatio` enum
  `1:1, 2:3, 3:2, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9, 1:4, 4:1, 1:8, 8:1`
  times `imageSize` `512 | 1K | 2K | 4K`. Only top-tier image API with an
  explicit `4:5` enum and 4K output.
- Pricing: $0.045 (0.5K) / $0.067 (1K) / $0.101 (2K) / $0.151 (4K) per image.
- Docs: <https://ai.google.dev/gemini-api/docs/image-generation>, pricing
  <https://ai.google.dev/gemini-api/docs/pricing>, announcement
  <https://blog.google/innovation-and-ai/technology/developers-tools/build-with-nano-banana-2/>.

### 3. Seedream 4.5 / 5.0 Lite — ByteDance

- Status: GA on BytePlus ModelArk (international first-party platform).
  Seedream 4.5 shipped Nov 2025; 5.0 Lite shipped early 2026; full 5.0 is
  app-only so far.
- Modes: text-to-image, unified editing (SeedEdit absorbed since 4.0),
  multi-image editing with strict reference detail preservation, sequential
  image sets, official dense-text/typography claims.
- Size control: `size` = `2K` (default) or `4K`, or free pixel `WxH` with
  total pixels 3.6M–16.7M (up to 4096x4096), ratio 1/16–16. Caveat: parameter
  detail mirror-verified; BytePlus docs render via JS (`docs.byteplus.com`).
- Pricing: ~$0.03–0.045/image (4.5), ~$0.035 (5.0 Lite) — secondary-sourced;
  confirm on the official pricing page during deep review.
- Docs: image API <https://docs.byteplus.com/en/docs/ModelArk/1541523>,
  Seedream 4.0–5.0 tutorial <https://docs.byteplus.com/en/docs/ModelArk/1824121>,
  pricing <https://docs.byteplus.com/en/docs/ModelArk/1544106>, model pages
  <https://seed.bytedance.com/en/seedream4_5>,
  <https://seed.bytedance.com/en/seedream5_0_lite>.

### 4. Grok Imagine Image — xAI

- Status: GA on the xAI API; `grok-imagine-image` ($0.02/image) and
  `grok-imagine-image-quality` ($0.05/image). LMArena image-edit top-10.
- Modes: text-to-image (up to 10 images/request), text-driven editing,
  multi-image editing with up to 3 source images. No masks/inpainting; no
  transparency.
- Size control: `aspect_ratio` enum
  `1:1, 16:9, 9:16, 4:3, 3:4, 3:2, 2:3, 2:1, 1:2, 19.5:9, 9:19.5, 20:9, 9:20, auto`;
  resolution tiers `1k | 2k`.
- Docs: generation
  <https://docs.x.ai/developers/model-capabilities/images/generation>, editing
  <https://docs.x.ai/developers/model-capabilities/images/editing>, models +
  pricing <https://docs.x.ai/developers/models>.

Image runners-up (not shortlisted): Reve 2.0 (#2 ranked, native 4K, ~$0.0067
per image, but beta API with JS-only console docs — revisit after deep
review), FLUX.2 [pro]/[max] (Black Forest Labs, free WxH up to 4MP, strong
editing), Ideogram 4.0 (best typography + native transparency), Wan 2.7 Image
(free WxH to 4K, bbox edits, hex `color_palette` parameter — free rider on the
Alibaba registration).

## Video Generation — Best 4

All four video APIs are asynchronous (submit job, poll status, download
output) — the connector design must use the polling pattern anticipated by the
deferred `utils.video.generate` contract.

### 1. Seedance 2.0 — ByteDance

- Status: public beta on BytePlus ModelArk since 2026-04-14 (organization
  verification required; beta rate limits ~QPS 2, 3 concurrent tasks). #1 on
  both arenas for text-to-video and #1–2 for image-to-video.
- Modes: text-to-video, image-to-video (`first_frame`), first+last frame,
  heavy multimodal referencing (up to 9 reference images + 3 videos + 3
  audio), native audio, multi-shot, `return_last_frame` chaining. `watermark`
  request parameter.
- Size control: `ratio` enum `21:9, 16:9, 4:3, 1:1, 3:4, 9:16`; `duration`
  4–15 s; 24 fps. Caveat: global beta caps resolution at 720p (CN endpoint
  documents 1080p and 2K Pro tiers) — confirm current cap during deep review.
- Pricing: unconfirmed (JS-gated pricing page); third-party reports range
  $0.01–0.15/s by tier. 20 free Fast calls/month during beta.
- Risk notes: ByteDance terms; active Hollywood cease-and-desist fight over
  copyrighted-character output; real-human-likeness generation restricted.
- Docs: video API <https://docs.byteplus.com/en/docs/ModelArk/1520757>,
  launch <https://seed.bytedance.com/en/blog/official-launch-of-seedance-2-0>.

### 2. Veo 3.1 — Google

- Status: GA (paid preview) on Gemini API and Vertex AI; the most
  production-stable option (Google Cloud terms, SynthID provenance).
- Models: `veo-3.1-generate-preview`, `veo-3.1-fast-generate-preview`,
  `veo-3.1-lite-generate-preview`.
- Modes: text-to-video, image-to-video, first+last frame interpolation, up to
  3 reference images, extend +7 s per step up to 20 steps, native audio
  always. No video-to-video restyle.
- Size control: `resolution` `720p | 1080p | 4k` (Lite: 720p/1080p);
  `aspectRatio` `16:9 | 9:16` only; `durationSeconds` `4 | 6 | 8` (8 s
  required for 1080p/4k/reference images); 24 fps.
- Pricing: $0.40/s standard (720p/1080p), $0.60/s 4k; Fast $0.10–0.30/s;
  Lite $0.05–0.08/s. Audio included.
- Docs: <https://ai.google.dev/gemini-api/docs/video>, pricing
  <https://ai.google.dev/gemini-api/docs/pricing>.

### 3. WAN 2.7 — Alibaba

- Status: GA on Alibaba Cloud Model Studio / DashScope with the
  best-documented international API of the Chinese providers. Models
  `wan2.7-t2v`, `wan2.7-i2v`, `wan2.7-r2v`, `wan2.7-videoedit`.
- Modes: text-to-video (multi-shot via `shot_type`), image-to-video with
  `first_frame` / `last_frame` / `first_clip` video continuation,
  audio-driven generation (`driving_audio`), reference-to-video, instruction
  video editing, native audio. Lip-sync via separate `wan2.2-s2v`.
- Size control: `resolution` `720P | 1080P` (default 1080P); output snaps to
  the input aspect ratio with width/height as multiples of 16; `duration`
  2–15 s (r2v/edit 2–10 s); 30 fps. The wan2.6 generation exposes exact WxH
  enums covering 16:9, 9:16, 1:1, 4:3, 3:4 if explicit dimensions are needed.
- Pricing: wan2.6 reference $0.10/s (720P), $0.15/s (1080P), flash tiers from
  $0.025/s; wan2.7 not yet on the international pricing page — confirm during
  deep review. 50 s free quota. `watermark` parameter defaults false. Output
  URLs expire after 24 h — the connector must download and persist promptly.
- Docs: lineup
  <https://www.alibabacloud.com/help/en/model-studio/video-generate-edit-model/>,
  i2v reference
  <https://www.alibabacloud.com/help/en/model-studio/image-to-video-general-api-reference>,
  t2v reference
  <https://www.alibabacloud.com/help/en/model-studio/text-to-video-api-reference>,
  pricing <https://www.alibabacloud.com/help/en/model-studio/model-pricing>.

### 4. Grok Imagine Video — xAI

- Status: GA on the xAI API since 2026-01-28; `grok-imagine-video` ($0.05/s)
  and `grok-imagine-video-1.5-preview` ($0.08/s, currently #1-preliminary on
  the LMArena image-to-video board). Cheapest shortlisted video API.
- Modes: text-to-video, image-to-video (first frame), reference-to-video,
  video editing (`/v1/videos/edits`, input capped ~8.7 s), extend
  (`/v1/videos/extensions`, 2–10 s). Native audio marketed but not explicit in
  parameter docs — verify during deep review.
- Size control: `resolution` `480p | 720p` only (the shortlist's lowest
  ceiling); `aspect_ratio` `1:1, 16:9, 9:16, 4:3, 3:4, 3:2, 2:3` (widest
  ratio set); duration 1–15 s.
- Docs: <https://docs.x.ai/developers/model-capabilities/video/generation>,
  models <https://docs.x.ai/developers/models>, announcement
  <https://x.ai/news/grok-imagine-api>.

Video runners-up and watch list: HappyHorse 1.0 (Alibaba Taotian — #2 on all
boards, v2v editing, 7-language lip sync; excluded only because the Model
Studio API is limited beta — watch
<https://www.alibabacloud.com/help/en/model-studio/happyhorse-video-edit-api-reference>),
Kling 3.0 (strong, but the developer docs/pricing pages block automated review
and the API plan is purchased separately), Vidu Q3 (value pick with audio +
full ratio set), LTX-2.3 (only native-4K root API; 16:9/9:16 only), Luma
Ray3.2 (Modify video-to-video restyle), SwitchX by Beeble (special-purpose
video-to-video VFX: relighting and background/prop swaps,
<https://developer.beeble.ai/>).

Excluded as not publicly accessible or not root: Sora 2 (API shuts down
2026-09-24, no successor), Midjourney (no public API), Pika (official API path
is fal.ai, an aggregator), Tencent Hunyuan (China enterprise-only API),
Moonvalley (waitlist).

## Integration Readiness Notes

How the shortlist maps onto the StackOS pattern when integration starts:

- Each platform becomes a provider manifest in the utils plugin with an
  `api_key` auth method (all five use bearer/API-key auth), mirroring
  `openai-images`. Google needs a note that the key comes from AI Studio with
  Cloud billing attached; Alibaba keys are region-locked; BytePlus requires
  organization verification before keys are issued.
- Open design decision for the deep-review phase: keep the single
  provider-neutral `video-generation` provider and select a backend per
  deployment, or register per-vendor providers (`google-veo`,
  `byteplus-seedance`, `alibaba-wan`, `xai-grok-video`) like every other
  domain does. Existing repo precedent favors per-vendor providers; the
  neutral provider was a placeholder for exactly this decision.
- All four video APIs are async job APIs; the connector needs submit → poll →
  download → persist-to-generated-assets, with provider job ids recorded in
  action audit metadata. WAN output URLs expire in 24 h; Veo stores server-side
  for 2 days.
- Image APIs return base64 or URLs synchronously; persistence mirrors the
  existing `openai-images` integration (bytes into generated assets, local
  artifact URLs, no payloads in agent responses).
- Watermark flags differ: WAN `watermark` defaults false, Seedance exposes a
  request parameter, Veo always embeds invisible SynthID. Record per-provider
  behavior in the contract review before exposing actions.
- Budget kinds: one per provider (`google-veo`, `byteplus-ark`, `alibaba-wan`,
  `xai-imagine`) following the `openai-images` budget pattern, since pricing
  units differ (per second by resolution vs per image).

## Open Verification Items For Deep Review

1. Seedance global (BytePlus) resolution cap — 720p beta limit vs CN 1080p/2K
   tiers — and official per-second pricing (page is JS-rendered).
2. Seedream exact size/pixel constraints and per-image pricing on the official
   ModelArk pages.
3. wan2.7 video pricing on the international Model Studio pricing page.
4. Grok Imagine native audio behavior in API outputs, fps, and watermark
   policy.
5. Veo paid-preview regional restrictions (EU/UK person-generation limits) for
   our deployment region.
6. HappyHorse Model Studio beta access criteria and GA timeline.
