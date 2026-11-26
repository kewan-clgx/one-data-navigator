# syntax=docker/dockerfile:1
# Local development image for the api, agent, and worker services.
# ODN_EXTRAS selects the dependency set (api | agent | worker); the command is set per service.
FROM python:3.12-slim

ARG ODN_EXTRAS=api

# System CA bundle: the slim image has none, and the Copilot runtime verifies GitHub's TLS certificate with it.
# An optional extra CA (build secret "extra_ca") is for local builds behind a TLS-inspecting corporate proxy;
# prod builds pass an empty file and trust only the public roots.
RUN --mount=type=secret,id=extra_ca,required=false \
    apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/* \
    && if [ -s /run/secrets/extra_ca ]; then \
         cp /run/secrets/extra_ca /usr/local/share/ca-certificates/odn-extra-ca.crt && update-ca-certificates; \
       fi
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt
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

# The Copilot SDK (api, agent) drives a native Copilot runtime. Bake the checksum-verified runtime into the
# image at build time and never download at run time.
ENV COPILOT_CLI_EXTRACT_DIR=/opt/copilot-runtime
RUN if python -c "import copilot" 2>/dev/null; then \
      python -c "from copilot._cli_download import download_cli; print(download_cli())" \
      && chmod -R a+rX /opt/copilot-runtime; \
    fi
ENV COPILOT_SKIP_CLI_DOWNLOAD=1

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin odn
USER odn

EXPOSE 8000
