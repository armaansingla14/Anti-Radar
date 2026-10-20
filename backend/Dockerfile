# syntax=docker/dockerfile:1
# ANTI RADAR backend. Build from the REPO ROOT so config/ (the thesis) is baked into the image:
#   docker build -f backend/Dockerfile .
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
# Optional extra CA (corporate TLS interception): `EXTRA_CA_CERT=/path/ca.crt docker compose build`
RUN --mount=type=secret,id=extra_ca \
    if [ -s /run/secrets/extra_ca ]; then cat /etc/ssl/certs/ca-certificates.crt /run/secrets/extra_ca > /tmp/ca.pem \
      && export SSL_CERT_FILE=/tmp/ca.pem; fi; \
    uv sync --frozen --no-dev --no-install-project
COPY config /app/config
COPY backend/ ./
RUN --mount=type=secret,id=extra_ca \
    if [ -s /run/secrets/extra_ca ]; then cat /etc/ssl/certs/ca-certificates.crt /run/secrets/extra_ca > /tmp/ca.pem \
      && export SSL_CERT_FILE=/tmp/ca.pem; fi; \
    uv sync --frozen --no-dev \
    && chmod +x start.sh \
    && useradd --system --uid 1001 --home /app radar && chown -R radar /app
ENV PATH="/app/backend/.venv/bin:$PATH" \
    THESIS_PATH=/app/config/anti_fund_thesis.yaml \
    PORT=8000
USER radar
EXPOSE 8000
CMD ["./start.sh"]
