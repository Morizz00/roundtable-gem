"""Export a recorded run to the static JSON the UI replays: {"run_id": ..., "events": [...]}.

    python scripts/export_run.py runs/recorded_<id>.jsonl [out.json]

The output is exactly what GET /runs/{id} returns, so a hosted demo can ship it as a static file (for example
web/public/runs/<id>.json) and replay it in the browser with no backend and no API key.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.state_log import StateLog  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    source = Path(sys.argv[1])
    log = StateLog.load(source)
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else source.with_suffix(".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"run_id": log.run_id, "events": [e.to_dict() for e in log.events]}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out} ({len(log.events)} events)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
