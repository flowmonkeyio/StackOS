# ElevenLabs in StackOS

Create an ElevenLabs Account with the API key method and attach it to the
project from Connections. The key stays in daemon-held credential storage.
`account.test` makes one subscription GET and reports subscription read
access and usage fields on success. ElevenLabs enforces endpoint permissions
for each action. A successful test establishes access to the subscription
endpoint only. A rejected key or missing subscription permission produces a
safe HTTP diagnostic. An Account outside the selected project is denied before
transport. [API authentication](https://elevenlabs.io/docs/api-reference/authentication) ·
[Creating an API key](https://elevenlabs.io/docs/help-center/technical/how-do-i-authorize-myself-using-an-api-key)

Use `integration.list` to find the provider and setup guidance. Once an Account
is attached, use `action.list` and `action.describe` to select an action and its
input schema. The Utilities plugin uses the pinned `stackos-connectors`
ElevenLabs catalog for inputs and action effects.

| Action ref | Effect | Official contract |
| --- | --- | --- |
| `utils.elevenlabs.subscription.get` | Read subscription usage | [Get subscription](https://elevenlabs.io/docs/api-reference/user/subscription/get/) |
| `utils.elevenlabs.models.list` | Read available models | [List models](https://elevenlabs.io/docs/api-reference/models/list) |
| `utils.elevenlabs.voices.list` | Read one page of saved voices | [Search voices](https://elevenlabs.io/docs/api-reference/voices/search) |
| `utils.elevenlabs.voices.settings.default.get` | Read default voice settings | [Get default settings](https://elevenlabs.io/docs/api-reference/voices/settings/get-default) |
| `utils.elevenlabs.voices.settings.get` | Read saved settings for a voice ID | [Get voice settings](https://elevenlabs.io/docs/api-reference/voices/settings/get) |
| `utils.elevenlabs.library.search` | Read one page of shared voices | [List shared voices](https://elevenlabs.io/docs/api-reference/voices/voice-library/get-shared) |
| `utils.elevenlabs.library.add` | Add the selected shared voice to the Account | [Add shared voice](https://elevenlabs.io/docs/api-reference/voices/voice-library/share) |
| `utils.elevenlabs.voices.create` | Create a voice from an existing generated preview ID | [Create a voice](https://elevenlabs.io/docs/api-reference/text-to-voice/create) |
| `utils.elevenlabs.dictionaries.create` | Create a dictionary from explicit rules and return its ID and version ID | [Create a dictionary from rules](https://elevenlabs.io/docs/api-reference/pronunciation-dictionaries/create-from-rules) |
| `utils.elevenlabs.speech.generate` | Generate and save project audio with character timing. May incur a charge. | [Create speech with timing](https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps) |
| `utils.elevenlabs.voices.design` | Generate and save voice previews as project audio. May incur a charge. | [Design a voice](https://elevenlabs.io/docs/api-reference/text-to-voice/design) |

Pass the returned `next_page_token` as `page_token` for another saved-voice
page. These structured fields retain their native names for `voices.list`.
Increment the zero-based `page` for another Voice Library page when
`has_more` is true. Library search preserves the owner ID and voice ID needed
by the add action.

Direct write and cost actions require `confirm_direct=true` and an
`intent_summary`. Workflow actions use `action.execute` with the exact action
granted to the active step and any approval the workflow requires. Pass a stable
`intent_id` or `idempotency_key` for a direct intent you may need to replay.
Each native write or cost dispatch sends at most one POST. ElevenLabs enforces
quota and rate limits. StackOS has no price estimate or budget enforcement for
these actions. [Provider errors](https://elevenlabs.io/docs/eleven-api/resources/errors)

Speech returns `artifact_id`, `output_format`, and each supplied `alignment`
and `normalized_alignment`. Timing arrays keep their native order and values.
An explicit null stays null and an omitted alignment stays absent.

Voice design returns preview rows in provider order. Saved rows carry their
`generated_voice_id`, original `file_index`, and project `artifact_id`. The
index counts saved native files, so it can differ from the preview's position
after a failure. Failed rows keep any known generated ID and their
`reason_code`. Use `artifact.get` in the project for the stored audio MIME type
and metadata. Use the selected preview's `generated_voice_id` for a separately
confirmed `voices.create`. Audio bytes stay in managed project files, and
reference audio supplied with voice design is redacted from the action audit.
The artifact URI identifies private local storage. The public static mount
returns 404 for these audio files.

Inspect the original call after a partial preview batch, ambiguous provider
outcome, or local storage failure. A post-dispatch storage error carries the
original call ID, any available provider request or history ID, and any saved
artifact or generated preview IDs. Replaying that intent returns the original
result or recovery receipt. Starting a new intent can incur another charge.

MCP and REST provider action responses use the shared response file by default.
The action audit records the execution and safe Account reference.
