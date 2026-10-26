"""Shared archive structure and member names for creation and recovery."""

import re
from pathlib import PurePosixPath

MANIFEST_SCHEMA = "stackos.local-backup.v3"
LEGACY_MANIFEST_SCHEMA = "stackos.local-backup.v2"
CORE_NAMES = {
    "data/stackos.db",
    "state/seed.bin",
    "state/auth.token",
    "state/seed.bin.bak",
    "state/backup-policy.reference.json",
}
REQUIRED_NAMES = CORE_NAMES - {"state/seed.bin.bak"}
TARGET_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
PROJECT_ID = re.compile(r"^[1-9][0-9]{0,18}$")


def valid_project_id(value: str) -> bool:
    return bool(PROJECT_ID.fullmatch(value)) and int(value) <= 2**63 - 1


def allowed_member_name(name: str, *, directory: bool) -> bool:
    stripped = name[:-1] if directory else name
    path = PurePosixPath(stripped)
    if not stripped or "\\" in stripped or "\x00" in stripped or path.is_absolute():
        return False
    if any(part in {"", ".", ".."} for part in stripped.split("/")) or str(path) != stripped:
        return False
    parts = path.parts
    project_path = (
        len(parts) >= 3 and parts[:2] == ("data", "projects") and valid_project_id(parts[2])
    )
    if directory:
        return (
            stripped in {"data", "state", "external", "data/projects"}
            or project_path
            or (
                len(parts) >= 2
                and parts[0] == "external"
                and TARGET_ID.fullmatch(parts[1]) is not None
            )
        )
    return (
        stripped == "manifest.json"
        or stripped in CORE_NAMES
        or (project_path and len(parts) >= 4)
        or (
            len(parts) >= 3 and parts[0] == "external" and TARGET_ID.fullmatch(parts[1]) is not None
        )
    )
