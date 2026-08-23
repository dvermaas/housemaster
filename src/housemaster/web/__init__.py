"""The `serve` web app: browse and filter the cached houses.

Read-only by construction -- it opens the database read-only and never imports
funda's fetching functions, so a page view can never hit the network. WAL means
it keeps working while a `fetch` writes underneath it.

Photos are hotlinked straight from funda's CDN, so the app serves no binary
assets of its own and has no media directory to be pointed at.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import Flask, current_app, g

from housemaster import db, photos
from housemaster.render import euro
from housemaster.web import filters, views


def get_conn() -> sqlite3.Connection:
    """One connection per request. The dev server is threaded and SQLite
    connections are not shareable across threads."""
    if "conn" not in g:
        g.conn = db.connect(current_app.config["DB_PATH"], read_only=True)
    return g.conn


def close_conn(_exception: BaseException | None = None) -> None:
    conn = g.pop("conn", None)
    if conn is not None:
        conn.close()


def create_app(db_path: Path) -> Flask:
    """Build the app. A factory so tests can bind it to a temporary cache."""
    app = Flask(__name__)
    # Resolved: Flask resolves a relative path against the *package* directory,
    # not the working directory, so `data/housemaster.db` would look in the
    # wrong place entirely.
    app.config["DB_PATH"] = Path(db_path).resolve()

    app.jinja_env.filters["euro"] = euro  # the CLI's formatter, so they agree
    app.jinja_env.filters["energy_class"] = filters.energy_class
    app.jinja_env.filters["compact"] = filters.compact
    app.jinja_env.filters["since"] = filters.since
    app.jinja_env.globals["photo_url"] = photos.photo_url

    app.teardown_appcontext(close_conn)
    app.register_blueprint(views.bp)
    return app
