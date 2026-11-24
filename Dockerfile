# Analysis — reporting and analytics service: production image (built from a clean git checkout).
#   docker build -t analysis .
#   docker run -p 8000:8000 -v analysis-data:/data --env-file .env analysis

# ---- 1) build the React pages (designer, /server, /admin, /analytics) into ui/app
FROM node:20-alpine AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend ./
COPY ui/brand /src/ui/brand
RUN npm run build

# ---- 2) the Python service
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    REPORTING_DATA_DIR=/data \
    REPORTING_ACCESS_MODE=required \
    REPORTING_ENABLED_AREAS=runner,admin,designer,server,analytics

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

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
# The "default" data adapter comes from MONGODB_URI / MONGODB_DATABASE.
RUN useradd --system --uid 10001 reporting && mkdir -p /data && chown -R reporting /data
USER reporting
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD ["python", "-m", "backend.app.healthcheck"]

# One worker: the report repository and data adapters are cached in the process (see ARCHITECTURE.md).
CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
