# syntax=docker/dockerfile:1.7
#
# Application image. Multi-stage so the runtime image carries no build toolchain and no
# development dependencies (specification section 21.9: the smaller the image, the less
# there is to scan and to patch).

# ---------------------------------------------------------------------------
# Stage 1 — resolve dependencies from the committed lock file
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependency layer first: it changes far less often than application code, so this
# stays cached across ordinary commits.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# ---------------------------------------------------------------------------
# Stage 2 — runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings.production

# libpq is needed even with psycopg[binary]'s bundled libraries when the C extension
# falls back; curl is used by the container healthcheck below.
RUN apt-get update \
    && apt-get install --no-install-recommends -y libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

# Never run the application as root (section 21).
RUN groupadd --system --gid 1001 vsat \
    && useradd --system --uid 1001 --gid vsat --create-home vsat

WORKDIR /app

COPY --from=builder --chown=vsat:vsat /app/.venv /app/.venv
COPY --chown=vsat:vsat . /app

RUN chmod +x /app/docker/entrypoint.sh

USER vsat

EXPOSE 8000

# Liveness only. Readiness depends on the database, which is the orchestrator's concern
# rather than a reason to restart an otherwise healthy container.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl --fail --silent --show-error http://127.0.0.1:8000/health/live || exit 1

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
