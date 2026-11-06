"""CLI adapter for the shipped engineering plugin controller."""

import sys
from importlib import resources, util
from pathlib import Path

bundled = Path(
    str(resources.files("stackos").joinpath("_assets/plugins/engineering/scripts/delivery.py"))
)
source = Path(__file__).resolve().parents[2] / "plugins/engineering/scripts/delivery.py"
path = bundled if bundled.is_file() else source
spec = util.spec_from_file_location("_stackos_engineering_delivery", path)
if spec is None or spec.loader is None:
    raise ImportError("shipped engineering delivery controller is unavailable")
module = util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
