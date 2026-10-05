"""StackOS admission and transport limits for AIGNC actions.

These are application choices. Native provider capabilities live in
stackos_connectors.connectors.aignc.contract.
"""

MAX_AUDIO_BYTES = 20 * 1024 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_RESPONSE_BYTES = 30 * 1024 * 1024
MAX_MESSAGES = 100
MAX_TEXT_LENGTH = 32000
MAX_OUTPUT_TOKENS = 8192
DEFAULT_READ_TIMEOUT_SECONDS = 600
MIN_READ_TIMEOUT_SECONDS = 60
MAX_READ_TIMEOUT_SECONDS = 1800
