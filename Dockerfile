# iTrend MCP Platform: website + monitor + the MCP servers it starts, in one container.
# They must share one container: the website starts MCPs as child processes and talks
# to them on 127.0.0.1, and the monitor checks those same processes.

# ---- Build: install Python packages (needs compilers for mysqlclient) ----
FROM python:3.12-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /usr/local/bin/uv
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential pkg-config default-libmysqlclient-dev \
 && rm -rf /var/lib/apt/lists/*
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# Platform packages first, so code changes don't reinstall everything.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY mcp_servers/mysql/pyproject.toml mcp_servers/mysql/uv.lock mcp_servers/mysql/
RUN cd mcp_servers/mysql && uv sync --frozen --no-dev --no-install-project

COPY . .
RUN cd mcp_servers/mysql && uv sync --frozen --no-dev

# ---- Run ----
FROM python:3.12-slim-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends libmariadb3 curl \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 app
COPY --from=build /usr/local/bin/uv /usr/local/bin/uv
COPY --from=build --chown=app:app /app /app

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    UV_CACHE_DIR=/tmp/uv-cache \
    UV_PYTHON_DOWNLOADS=never

# Collect CSS/JS/images at build time (WhiteNoise serves them).
RUN DJANGO_SECRET_KEY=build-only DJANGO_DEBUG=True DB_ENGINE=sqlite \
    python manage.py collectstatic --noinput \
 && sed -i 's/\r$//' docker/entrypoint.sh && chmod +x docker/entrypoint.sh \
 && mkdir -p /app/var && chown -R app:app /app/staticfiles /app/var

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
  CMD curl -fsS http://127.0.0.1:8000/api/v1/health/ || exit 1
ENTRYPOINT ["docker/entrypoint.sh"]
