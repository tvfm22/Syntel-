#!/bin/bash
set -e

echo "Starting Syntel application..."

# All writable data lives under DATA_PATH (default /app/data).
# settings.ensure_data_dirs() also creates these at import time.
DATA_PATH="${DATA_PATH:-/app/data}"
mkdir -p "$DATA_PATH"/{logs,db,uploads,media,cache,tmp,sessions,backups}

# Static files must live somewhere WRITABLE at runtime: the container image
# filesystem is read-only in Liara, so collectstatic cannot write to the
# image's STATIC_ROOT. Use /tmp (the proven old-deploy pattern) and let
# whitenoise serve from there. settings_production reads STATIC_ROOT env.
export STATIC_ROOT="${STATIC_ROOT:-/tmp/staticfiles}"
mkdir -p "$STATIC_ROOT"

# Seed the database from the image on first boot (fresh persistent disk) if available.
if [ ! -f "$DATA_PATH/db/db.sqlite3" ]; then
    if [ -f /app/seed/db/db.sqlite3 ]; then
        echo "Seeding database from /app/seed/db..."
        cp /app/seed/db/db.sqlite3 "$DATA_PATH/db/db.sqlite3"
    elif [ -d "/app/seed_data" ] && [ "$(ls -A /app/seed_data 2>/dev/null)" ]; then
        if [ -f "/app/seed_data/db/db.sqlite3" ]; then
            echo "Seeding database from /app/seed_data/db..."
            cp /app/seed_data/db/db.sqlite3 "$DATA_PATH/db/db.sqlite3"
        elif [ -f "/app/seed_data/db.sqlite3" ]; then
            echo "Seeding database from /app/seed_data..."
            cp /app/seed_data/db.sqlite3 "$DATA_PATH/db/db.sqlite3"
        fi
    else
        echo "No seed database found. A fresh database will be initialized via migrations."
    fi
fi

# Always run migrations to ensure the schema is up to date (fail-fast on migration error).
echo "Running migrations..."
python manage.py migrate --noinput

# Collect static files into the writable STATIC_ROOT.
echo "Collecting static files into $STATIC_ROOT..."
python manage.py collectstatic --noinput --clear || echo "Collectstatic failed, continuing..."

# Start gunicorn (config at syntel/gunicorn_config.py — bind 0.0.0.0:8000).
echo "Starting gunicorn..."
exec gunicorn --config syntel/gunicorn_config.py syntel.wsgi:application
