"""The `serve` web app: a JSON API over the cache, and the built SPA.

Read-only by construction -- it opens the database read-only and never imports
funda's fetching functions, so a page view can never hit the network. WAL means
it keeps working while a `fetch` writes underneath it.

Photos are hotlinked straight from funda's CDN, so the app serves no images of
its own. The front end lives in `frontend/` and builds into `dist/` here.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import Flask, current_app, g

from housemaster import db
from housemaster.web import views

DIST_DIR = Path(__file__).parent / "dist"
"""Where `npm run build` in frontend/ writes. Shipped inside the wheel."""


def get_conn() -> sqlite3.Connection:
    """One connection per request. The dev server is threaded and SQLite
    connections are not shareable across threads."""
    if "conn" not in g:
        g.conn = db.connect(current_app.config["DB_PATH"], read_only=True)
    conn: sqlite3.Connection = g.conn
    return conn


def close_conn(_exception: BaseException | None = None) -> None:
    conn = g.pop("conn", None)
    if conn is not None:
        conn.close()


def create_app(db_path: Path, dist_dir: Path | None = None) -> Flask:
    """Build the app. A factory so tests can bind it to a temporary cache."""
    # No Flask static folder: the shell route serves the build itself, so that
    # every path it does not recognise can fall through to the app.
    app = Flask(__name__, static_folder=None)
    # Resolved: Flask resolves a relative path against the *package* directory,
    # not the working directory, so `data/housemaster.db` would look in the
    # wrong place entirely.
    app.config["DB_PATH"] = Path(db_path).resolve()
    app.config["DIST_DIR"] = Path(dist_dir or DIST_DIR).resolve()
    app.json.ensure_ascii = False  # type: ignore[attr-defined]  # € and m², not €

    app.teardown_appcontext(close_conn)
    app.register_blueprint(views.bp)
    return app
