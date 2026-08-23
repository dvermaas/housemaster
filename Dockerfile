# HouseMaster -- Alpine, multi-stage.
#
# Alpine is only viable because curl_cffi ships musllinux wheels for cp314
# (x86_64 and aarch64). Without them this would have to build curl-impersonate
# from source, which is why scrapers usually end up on Debian instead.
# UV_NO_BUILD on the dependency step is the guard: if a musl wheel ever
# disappears the build fails loudly rather than quietly compiling for an hour.

FROM python:3.14-alpine AS build

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

# Dependencies resolve from the lockfile before the source is copied, so editing
# a template does not re-resolve or re-download anything.
COPY pyproject.toml uv.lock README.md ./
RUN UV_NO_BUILD=1 uv sync --frozen --no-dev --no-install-project --extra serve

# The project itself has no wheel and is built here, so the guard above cannot
# apply to this step.
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable --extra serve


FROM python:3.14-alpine AS runtime

# The venv hardcodes its interpreter path, so runtime must be the same base
# image with the venv at the same location.
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOUSEMASTER_DB=/data/housemaster.db

# Unprivileged, and owning /data so a fresh named volume is writable by `fetch`.
RUN addgroup -S app && adduser -S -G app app \
 && mkdir -p /data && chown app:app /data
USER app

EXPOSE 8765

# Two workers is plenty: every request is a handful of indexed SQLite reads, and
# WAL lets them read while a `fetch` writes. Threads rather than more processes
# because the work is I/O-bound and each process opens its own connections.
# --preload imports the app in the master before forking. Without it a bad
# HOUSEMASTER_DB is an import error *inside a worker*, which gunicorn answers
# by respawning forever -- the container never exits and just spins. Nothing
# is opened at import (connections are per-request via flask.g), so
# preloading is free here.
CMD ["gunicorn", "housemaster.web.wsgi:app", "--preload", \
     "--bind", "0.0.0.0:8765", \
     "--workers", "2", \
     "--threads", "4", \
     "--access-logfile", "-", \
     "--forwarded-allow-ips", "*"]
