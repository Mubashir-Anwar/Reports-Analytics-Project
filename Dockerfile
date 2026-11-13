# MongoDB Reporting Service — production image (built from a clean git checkout).
#   docker build -t mongo-reporting .
#   docker run -p 8000:8000 -v reporting-data:/data --env-file .env mongo-reporting

# ---- 1) build the React pages (designer, /server, /admin) into ui/app
FROM node:20-alpine AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend ./
RUN npm run build

# ---- 2) the Python service
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    REPORTING_DATA_DIR=/data \
    REPORTING_LOCAL_BOOTSTRAP=false \
    REPORTING_ACCESS_MODE=required

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY app.py reporting_core.py designer_render.py jexpr.py jrxml_import.py ./
COPY reports ./reports
COPY ui/index.html ui/runner.css ui/runner.js ui/styles.css ./ui/
COPY --from=frontend /src/ui/app ./ui/app

# Nothing from data/ is baked in: reports, adapters, users and secrets live on the /data volume.
# The "default" data adapter comes from MONGODB_URI / MONGODB_DATABASE.
RUN useradd --system --uid 10001 reporting && mkdir -p /data && chown -R reporting /data
USER reporting
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD ["python", "-m", "backend.app.healthcheck"]

# One worker: the report repository and data adapters are cached in the process (see ARCHITECTURE.md §9.1).
CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
