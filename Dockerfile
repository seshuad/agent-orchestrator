# The designer and everything a run needs (Conductor, the step library, the gateway), in one image.
#   docker build -t agent-orchestrator .   (or: deploy/gke/deploy.sh, which builds it with Cloud Build)
# The workspace lives outside the image: /data/workspace, a persistent disk on GKE.

FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /usr/local/bin/
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates build-essential && rm -rf /var/lib/apt/lists/*

# Conductor, pinned to the commit the service is tested with, as its own tool (runs start it through conductor_cached.py).
ENV UV_TOOL_DIR=/opt/uv-tools UV_TOOL_BIN_DIR=/usr/local/bin UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
ARG CONDUCTOR_REF=2bfc308fa0e9a9eb35786e9afd1b570193bfb6f7
RUN uv tool install --python /usr/local/bin/python3.13 "conductor-cli @ git+https://github.com/microsoft/conductor.git@${CONDUCTOR_REF}"

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project --python /usr/local/bin/python3.13
COPY src/ src/
COPY examples/ examples/
RUN uv sync --frozen --no-dev --python /usr/local/bin/python3.13
COPY --from=web /web/dist web/dist

RUN useradd --uid 1000 --create-home agent && mkdir -p /data/workspace && chown -R agent /data /app
USER agent
ENV PATH=/app/.venv/bin:$PATH AGENT_SERVICE_HOME=/data/workspace AGENT_SERVICE_BIND=0.0.0.0 AGENT_SERVICE_PORT=8700 PYTHONUNBUFFERED=1
EXPOSE 8700
CMD ["agent-service", "serve"]
