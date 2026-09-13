"""Static AIGNC capability contract with no transport or workflow dependencies."""

AIGNC_IMAGE_MODEL = "gemini-3.1-flash-image"
AIGNC_MODELS = (
    "gemini-3.8-flash",
    "gemini-3.8-flash-high",
    "gemini-3.8-flash-medium",
    "gemini-3.8-flash-low",
    "gemini-3.8-flash-extra-low",
    "gemini-3.7-flash",
    "gemini-3.7-flash-high",
    "gemini-3.7-flash-medium",
    "gemini-3.6-flash",
    "gemini-3.6-flash-high",
    "gemini-3.6-flash-medium",
    AIGNC_IMAGE_MODEL,
    "gemini-3.1-flash-lite",
    "gemini-3.1-pro",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
    "gemini-pro-agent",
    "claude-3-5-sonnet-20241022",
    "claude-opus-4-6-thinking",
    "gpt-oss-120b-medium",
)
AIGNC_TEXT_MODELS = frozenset(AIGNC_MODELS) - {AIGNC_IMAGE_MODEL}
AIGNC_GROUNDING_MODELS = frozenset(
    model for model in AIGNC_TEXT_MODELS if model.startswith("gemini-")
)
AIGNC_AUDIO_MODELS = frozenset({"gemini-3.8-flash", "gemini-3.7-flash"})
AIGNC_AUDIO_FORMATS = frozenset({"wav", "mp3", "aac", "flac", "ogg"})
# StackOS bounds, not claims about undocumented provider limits.
MAX_AUDIO_BYTES = 20 * 1024 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_RESPONSE_BYTES = 30 * 1024 * 1024
MAX_MESSAGES = 100
MAX_TEXT_LENGTH = 32000
MAX_OUTPUT_TOKENS = 8192
DEFAULT_READ_TIMEOUT_SECONDS = 600
MIN_READ_TIMEOUT_SECONDS = 60
MAX_READ_TIMEOUT_SECONDS = 1800
