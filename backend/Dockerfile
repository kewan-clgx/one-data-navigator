# Local development image for the api, agent, and worker services.
# ODN_EXTRAS selects the dependency set (api | agent | worker); the command is set per service.
FROM python:3.12-slim

ARG ODN_EXTRAS=api

RUN pip install --no-cache-dir uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first so code-only changes don't bust the layer cache.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra ${ODN_EXTRAS} --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev --extra ${ODN_EXTRAS}

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin odn
USER odn

EXPOSE 8000
