"""WSGI entry point, for running behind a real server.

`housemaster serve` uses Werkzeug's development server, which is fine on a
laptop and explicitly not fine facing a network. This is what gunicorn imports
instead:

    gunicorn housemaster.web.wsgi:app

The database path comes from `$HOUSEMASTER_DB`, the same variable the CLI
honours, so a container and a shell agree on where the cache lives.
"""

from __future__ import annotations

import os
from pathlib import Path

from housemaster import db
from housemaster.web import create_app

DB_PATH = Path(os.environ.get("HOUSEMASTER_DB") or db.DEFAULT_DB_PATH)

# Create the file if it is not there yet, so a fresh volume serves an empty
# cache instead of crash-looping. This is the only write the web app ever makes,
# and it happens once at start-up -- every request still opens read-only.
if not DB_PATH.exists():
    try:
        db.connect(DB_PATH).close()
    except OSError as exc:
        raise SystemExit(f"Cannot create {DB_PATH}: {exc}") from exc

app = create_app(DB_PATH)
