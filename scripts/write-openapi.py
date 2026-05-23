"""Write the source FastAPI OpenAPI document to a JSON file."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from stackos.server import create_app


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: write-openapi.py OUTPUT_JSON", file=sys.stderr)
        return 2
    target = Path(sys.argv[1])
    app = create_app()
    target.write_text(json.dumps(app.openapi(), sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
