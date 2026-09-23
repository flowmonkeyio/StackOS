"""Write the source FastAPI OpenAPI document to a JSON file."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic.json_schema import models_json_schema

from stackos.operations.registry import build_operation_registry
from stackos.server import create_app


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: write-openapi.py OUTPUT_JSON", file=sys.stderr)
        return 2
    target = Path(sys.argv[1])
    app = create_app()
    document = app.openapi()
    # The generic operation REST route intentionally has a dynamic envelope.
    # Include the registry-owned contracts used by the Action Calls console so
    # the UI does not maintain a second copy of durable job/receipt schemas.
    models = [
        (model, mode)
        for spec in build_operation_registry().by_surface("rest")
        if spec.name.startswith("actionCall.")
        for model, mode in (
            (spec.input_model, "validation"),
            (spec.output_model, "serialization"),
        )
    ]
    _, operation_schema = models_json_schema(models, ref_template="#/components/schemas/{model}")
    components = document.setdefault("components", {}).setdefault("schemas", {})
    for name, schema in operation_schema.get("$defs", {}).items():
        components.setdefault(name, schema)
    target.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
