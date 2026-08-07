# =============================================================================
# Syntel — Production Dockerfile (Liara platform:docker, builds from repo root)
#
# Layout notes (post consolidation):
#   - manage.py and requirements.txt live at the REPO ROOT (build context)
#   - the Django project package lives in syntel/ (module: syntel.settings)
#   - manage.py inserts <base>/syntel into sys.path, so gunicorn must run with
#     PYTHONPATH=/app/syntel
#   - all writable data lives under DATA_PATH (default /app/data); mount a
#     persistent Liara disk there to keep the DB/uploads across redeploys
# =============================================================================
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=syntel.settings_production \
    PYTHONPATH=/app/syntel \
    DATA_PATH=/app/data

WORKDIR /app

# System dependencies (libpq for psycopg2, curl for the healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
        postgresql-client \
        build-essential \
        libpq-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application
COPY . .

# Keep the shipped database (tracked in git) as a seed: a Liara disk mounted
# at /app/data shadows this path at runtime, so the entrypoint copies the
# seed to DATA_PATH on first boot instead.
RUN mkdir -p /app/seed/db \
    && { [ -f /app/data/db/db.sqlite3 ] && cp /app/data/db/db.sqlite3 /app/seed/db/ || true; } \
    && rm -rf /app/data/logs /app/data/cache /app/data/tmp /app/data/sessions /app/data/backups /app/data/uploads \
    && find . -type d -name __pycache__ -exec rm -rf {} + \
    && chmod +x /app/entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health/ || exit 1

ENTRYPOINT ["./entrypoint.sh"]
