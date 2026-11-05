"""Load the fixed engineering helper shipped with StackOS."""

from __future__ import annotations

import importlib.util
import sys
from functools import lru_cache
from importlib import resources
from pathlib import Path
from types import ModuleType


@lru_cache(maxsize=1)
def task_delivery() -> ModuleType:
    bundled = Path(str(resources.files("stackos").joinpath("_assets/plugins/engineering/scripts")))
    source = Path(__file__).resolve().parents[2] / "plugins/engineering/scripts"
    path = (bundled if bundled.is_dir() else source) / "task_delivery.py"
    spec = importlib.util.spec_from_file_location("_stackos_builtin_task_delivery", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Shipped engineering task delivery helper is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
