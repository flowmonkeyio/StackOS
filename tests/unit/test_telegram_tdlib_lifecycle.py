from __future__ import annotations

from pathlib import Path

import pytest

from stackos.cli import doctor_commands as doctor_cli
from stackos.config import Settings
from stackos.integrations.telegram_tdlib.runtime import (
    TDLIB_RUNTIME_REPAIR,
    TelegramTdlibRuntimeError,
)


def test_telegram_tdlib_doctor_reports_verified_runtime_without_native_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    monkeypatch.setattr(doctor_cli, "tdlib_runtime_root", lambda data_dir: tmp_path / "runtime")
    monkeypatch.setattr(
        doctor_cli,
        "verify_tdlib_runtime",
        lambda *, runtime_root: type(
            "Runtime",
            (),
            {
                "tdlib_version": "1.8.67",
                "library_sha256": "a" * 64,
            },
        )(),
    )

    ok, details = doctor_cli._check_telegram_tdlib_runtime(settings)

    assert ok is True
    assert details == {
        "provider": "telegram",
        "runtime": "tdlib",
        "library_verified": True,
        "tdlib_version": "1.8.67",
        "library_sha256": "a" * 64,
        "repair": None,
    }
    assert str(tmp_path) not in str(details)


def test_telegram_tdlib_doctor_reports_managed_repair_without_native_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    monkeypatch.setattr(doctor_cli, "tdlib_runtime_root", lambda data_dir: tmp_path / "runtime")

    def unavailable(*, runtime_root: Path) -> object:
        _ = runtime_root
        raise TelegramTdlibRuntimeError(TDLIB_RUNTIME_REPAIR)

    monkeypatch.setattr(
        doctor_cli,
        "verify_tdlib_runtime",
        unavailable,
    )

    ok, details = doctor_cli._check_telegram_tdlib_runtime(settings)

    assert ok is False
    assert details == {
        "provider": "telegram",
        "runtime": "tdlib",
        "library_verified": False,
        "tdlib_version": None,
        "library_sha256": None,
        "repair": TDLIB_RUNTIME_REPAIR,
    }
    assert str(tmp_path) not in str(details)
