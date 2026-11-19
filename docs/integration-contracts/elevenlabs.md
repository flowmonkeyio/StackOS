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
input schema. The Utilities plugin projects data action schemas and effects
from the pinned `stackos-connectors` ElevenLabs catalog.

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

Pass the returned `next_page_token` as `page_token` for another saved-voice
page. These structured fields retain their native names for `voices.list`.
Increment the zero-based `page` for another Voice Library page when
`has_more` is true. Library search preserves the owner ID and voice ID needed
by the add action.

Direct writes require `confirm_direct=true` and an `intent_summary`. Workflow
actions use `action.execute` with the exact action granted to the active step.
Use the same idempotency key for a retry of the same write intent. Each native
write sends at most one POST per dispatch. Check the action audit and provider
receipt before starting another intent after an ambiguous result. Repeating the
same intent after an unsafe-to-retry failure returns the original call ID and
reconciliation guidance. ElevenLabs enforces quota and rate limits. These data
actions have no StackOS budget enforcement. [Provider errors](https://elevenlabs.io/docs/eleven-api/resources/errors)

MCP and REST provider action responses use the shared response file by default.
The action audit records the execution and safe Account reference.
