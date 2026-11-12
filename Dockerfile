# MongoDB Reporting Service — production image.
# Build the designer first (frontend/ -> ui/app) so the image contains it:  cd frontend && npm ci && npm run build
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    REPORTING_DATA_DIR=/data \
    REPORTING_LOCAL_BOOTSTRAP=false \
    REPORTING_ACCESS_MODE=required

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py reporting_core.py designer_render.py jexpr.py jrxml_import.py ./
COPY reports ./reports
COPY ui ./ui
# Starter report definitions and the connection list (no secrets) are copied into /data on first start.
COPY data/reports ./data/reports
COPY data/connections.json ./data/connections.json

RUN useradd --system --uid 10001 reporting && mkdir -p /data && chown -R reporting /data
USER reporting
VOLUME ["/data"]
EXPOSE 8000

# One worker: saved reports and data adapters are cached in the process, so several workers would disagree.
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
