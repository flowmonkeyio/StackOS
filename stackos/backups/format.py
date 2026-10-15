"""Shared archive structure and member names for creation and recovery."""

import re
from pathlib import PurePosixPath

MANIFEST_SCHEMA = "stackos.local-backup.v2"
CORE_NAMES = {
    "data/stackos.db",
    "state/seed.bin",
    "state/auth.token",
    "state/seed.bin.bak",
    "state/backup-policy.reference.json",
}
REQUIRED_NAMES = CORE_NAMES - {"state/seed.bin.bak"}
TARGET_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def allowed_member_name(name: str, *, directory: bool) -> bool:
    stripped = name[:-1] if directory else name
    path = PurePosixPath(stripped)
    if not stripped or "\\" in stripped or "\x00" in stripped or path.is_absolute():
        return False
    if any(part in {"", ".", ".."} for part in stripped.split("/")) or str(path) != stripped:
        return False
    parts = path.parts
    if directory:
        return stripped in {"data", "state", "external"} or (
            len(parts) >= 2 and parts[0] == "external" and TARGET_ID.fullmatch(parts[1]) is not None
        )
    return (
        stripped == "manifest.json"
        or stripped in CORE_NAMES
        or (
            len(parts) >= 3 and parts[0] == "external" and TARGET_ID.fullmatch(parts[1]) is not None
        )
    )
