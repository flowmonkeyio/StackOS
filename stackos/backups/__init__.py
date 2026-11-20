"""Generic local encrypted backup and inert-recovery lifecycle services."""

from .models import BackupError, BackupPolicy, BackupTarget
from .recovery import restore_backup
from .service import (
    backup_status,
    create_backup,
    generate_recovery_key,
    load_policy,
    run_due_backup,
    save_policy,
    update_policy,
)

__all__ = [
    "BackupError",
    "BackupPolicy",
    "BackupTarget",
    "backup_status",
    "create_backup",
    "generate_recovery_key",
    "load_policy",
    "restore_backup",
    "run_due_backup",
    "save_policy",
    "update_policy",
]
