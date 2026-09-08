# syntax=docker/dockerfile:1

# ---- Stage 1: build the React frontend ----
FROM node:20-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ .
RUN npm run build

# ---- Stage 2: install the Python backend + CLI ----
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    OFFERPILOT_DATA=/data

RUN adduser --disabled-password --gecos "" --home /app offerpilot \
    && mkdir -p /data \
    && chown -R offerpilot:offerpilot /data /app \
    && python -m pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev \
    && ln -s /app/.venv/bin/oc /usr/local/bin/oc

COPY --from=web /web/dist /app/web/dist

USER offerpilot
EXPOSE 8080
VOLUME ["/data"]
ENTRYPOINT ["oc"]
CMD ["start", "--host", "0.0.0.0", "--port", "8080"]
