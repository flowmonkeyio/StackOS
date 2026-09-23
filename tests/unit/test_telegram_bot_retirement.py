from __future__ import annotations

import importlib.util

from stackos.integrations import REGISTRY


def test_telegram_bot_api_wrapper_and_action_package_are_absent() -> None:
    assert "telegram-bot" not in REGISTRY
    assert importlib.util.find_spec("stackos.actions.telegram_bot") is None
    assert importlib.util.find_spec("stackos.integrations.telegram_bot") is None
