# Render deploy only. Builds the frontend and bakes it into the backend
# image so FastAPI can serve it directly (StaticFiles mount + SPA fallback
# in app.py) -- this makes frontend+API genuinely same-origin without a
# separate static site or reverse proxy, which Render's static-site
# "Rewrite" rules don't actually support for external destinations. Local
# dev is unaffected: BackEnd/Dockerfile and docker-compose.yml are unchanged
# and don't build the frontend at all.
#
# Build context for this Dockerfile must be the REPO ROOT, not BackEnd/, so
# it can COPY from both FrontEnd/ and BackEnd/ -- on Render, set Root
# Directory to "." and Dockerfile Path to "deploy/Dockerfile".

FROM node:20-alpine AS frontend-build
WORKDIR /frontend
COPY FrontEnd/package*.json ./
RUN npm ci
COPY FrontEnd/ ./
# Baked in at build time -- empty on purpose so the built app calls the API
# same-origin (relative paths) instead of a hardcoded host. See client.js's
# `??` (not `||`) handling of this var.
ARG VITE_API_BASE=""
ENV VITE_API_BASE=$VITE_API_BASE
RUN npm run build

FROM python:3.11-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_LINK_MODE=copy

COPY BackEnd/pyproject.toml BackEnd/uv.lock ./
RUN uv sync --locked --no-install-project

COPY BackEnd/ .
RUN uv sync --locked

COPY --from=frontend-build /frontend/dist ./static

EXPOSE 8000

CMD ["./start.sh"]
