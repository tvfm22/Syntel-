# =============================================================================
# Syntel — Production Dockerfile (Liara platform:docker, builds from repo root)
# =============================================================================
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=syntel.settings_production \
    PYTHONPATH=/app/syntel \
    DATA_PATH=/app/data \
    STATIC_ROOT=/tmp/staticfiles \
    PORT=8000

WORKDIR /app

# System dependencies (libpq5 runtime for psycopg2, curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
        postgresql-client \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root application user
RUN addgroup --system syntel && adduser --system --ingroup syntel syntel

# Install Python dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application
COPY . .

# Keep any shipped seed DB only under /app/seed_data/db (avoid duplicate copies),
# strip transient runtime directories, and set ownership for the non-root user.
RUN mkdir -p /app/seed_data/db /app/data /tmp/staticfiles \
    && { [ -f /app/data/db/db.sqlite3 ] && [ ! -f /app/seed_data/db/db.sqlite3 ] && mv /app/data/db/db.sqlite3 /app/seed_data/db/db.sqlite3 || true; } \
    && rm -rf /app/data/db /app/data/logs /app/data/cache /app/data/tmp /app/data/sessions /app/data/backups /app/data/uploads \
    && find . -type d -name __pycache__ -exec rm -rf {} + \
    && chmod +x /app/entrypoint.sh \
    && chown -R syntel:syntel /app /tmp/staticfiles

USER syntel

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health/ || exit 1

ENTRYPOINT ["./entrypoint.sh"]
