#!/bin/bash
set -e

echo "Starting Syntel application..."

# All writable data lives under DATA_PATH (default /app/data).
# settings.ensure_data_dirs() also creates these at import time.
DATA_PATH="${DATA_PATH:-/app/data}"
mkdir -p "$DATA_PATH"/{logs,db,uploads,cache,tmp,sessions,backups}

# Static files must live somewhere WRITABLE at runtime: the container image
# filesystem is read-only in Liara, so collectstatic cannot write to the
# image's STATIC_ROOT. Use /tmp (the proven old-deploy pattern) and let
# whitenoise serve from there. settings_production reads STATIC_ROOT env.
export STATIC_ROOT="${STATIC_ROOT:-/tmp/staticfiles}"
mkdir -p "$STATIC_ROOT"

# Seed the database from the image on first boot (fresh persistent disk).
if [ ! -f "$DATA_PATH/db/db.sqlite3" ] && [ -f /app/seed/db/db.sqlite3 ]; then
    echo "Seeding database from image..."
    cp /app/seed/db/db.sqlite3 "$DATA_PATH/db/db.sqlite3"
fi

# Always run migrations to ensure the schema is up to date.
echo "Running migrations..."
python manage.py migrate --noinput || echo "Migration failed, continuing..."

# Collect static files into the writable STATIC_ROOT.
echo "Collecting static files into $STATIC_ROOT..."
python manage.py collectstatic --noinput --clear || echo "Collectstatic failed, continuing..."

# Start gunicorn (config at syntel/gunicorn_config.py — bind 0.0.0.0:8000).
echo "Starting gunicorn..."
exec gunicorn --config syntel/gunicorn_config.py syntel.wsgi:application
