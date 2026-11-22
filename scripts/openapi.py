"""Write the API's OpenAPI description to frontend/openapi.json (step 10a).

Usage (from the repo root, venv active):
    python -m scripts.openapi                 then, in frontend/: npm run api:types

The frontend generates its TypeScript types from this file (openapi-typescript), so its types always match the API's
response models. Run both after changing an API route or response model. No server, database or Claude needed: free.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from src.api.main import create_app

OUT = Path(__file__).resolve().parents[1] / "frontend" / "openapi.json"


def main() -> int:
    spec = create_app().openapi()
    OUT.write_text(json.dumps(spec, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {OUT} ({len(spec['paths'])} paths).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
