"""One set of bounds for backup creation, encryption and recovery."""

MAX_MEMBERS = 100_000  # Every file, directory and manifest ZIP member.
MAX_EXPANDED_BYTES = 20 * 1024**3
MAX_ARCHIVE_BYTES = 20 * 1024**3
MAX_MANIFEST_BYTES = 32 * 1024 * 1024
MAX_COMPRESSION_RATIO = 1000
MAX_RETAINED_ARCHIVES = 365
# Accommodates existing full-result history at the largest supported policy;
# new writes store compact retention receipts and bound their count.
MAX_OUTCOME_BYTES = 8 * 1024 * 1024
