# Analysis — reporting and analytics service: production image (built from a clean git checkout).
#   docker build -t analysis .
#   docker run -p 8000:8000 -v analysis-data:/data --env-file .env analysis

# ---- 1) build the React pages (designer, /server, /admin, /analytics) into ui/app
FROM node:22-alpine AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend ./
COPY ui/brand /src/ui/brand
RUN npm run build

# ---- 2) fonts with Urdu / Arabic letters: used in a PDF when the report's own font has none (backend/app/fonts.py)
FROM debian:bookworm-slim AS fonts
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-noto-core \
 && mkdir -p /fonts \
 && cp /usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf /usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf /fonts/

# ---- 3) the Python packages, exactly the tested versions (constraints.txt)
FROM python:3.12-slim-bookworm AS packages
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt && pip check

# ---- 4) the service
FROM python:3.12-slim-bookworm

ENV PATH=/opt/venv/bin:$PATH \
    HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    REPORTING_DATA_DIR=/data \
    REPORTING_ACCESS_MODE=required \
    REPORTING_ENABLED_AREAS=runner,admin,designer,server,analytics

WORKDIR /app
COPY --from=packages /opt/venv /opt/venv
COPY --from=fonts /fonts /usr/share/fonts/truetype/noto

COPY backend ./backend
# sign-out helper, the embed viewer and the brand files; the React pages come from stage 1
COPY ui/signout.js ./ui/
COPY ui/embed ./ui/embed
COPY ui/brand ./ui/brand
COPY --from=frontend /src/ui/app ./ui/app
# Admin → Tools offers the Postman collection for this server
COPY postman ./postman

# API docs (/docs, /openapi.json) are off in the image; add "docs" to REPORTING_ENABLED_AREAS to show them.
# Nothing from data/ is baked in: reports, adapters, users, secrets and analytics datasets live on the /data volume.
# First sign-in: docker exec <container> python -m backend.app.setup (an admin for 24 hours). Data sources are added in the product.
# The folders are made here so a new volume on any of them belongs to the service's user.
RUN useradd --system --uid 10001 --no-create-home reporting \
 && mkdir -p /data/analytics /data/exports && chown -R reporting /data
USER reporting
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD ["python", "-m", "backend.app.healthcheck"]

# One worker: the report repository and data adapters are cached in the process (see ARCHITECTURE.md).
CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
