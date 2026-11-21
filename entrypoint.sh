#!/bin/bash
set -e

echo "Starting Syntel application..."

# All writable data lives under DATA_PATH (default /app/data).
DATA_PATH="${DATA_PATH:-/app/data}"
mkdir -p "$DATA_PATH"/{logs,db,uploads,cache,tmp,sessions,backups}

# Static files live in a writable location at runtime (e.g. /tmp/staticfiles).
export STATIC_ROOT="${STATIC_ROOT:-/tmp/staticfiles}"
mkdir -p "$STATIC_ROOT"

# Seed the database from /app/seed_data/db on first boot (fresh persistent disk) if available.
if [ ! -f "$DATA_PATH/db/db.sqlite3" ]; then
    if [ -f "/app/seed_data/db/db.sqlite3" ]; then
        echo "Seeding database from /app/seed_data/db..."
        cp /app/seed_data/db/db.sqlite3 "$DATA_PATH/db/db.sqlite3"
    else
        echo "No seed database found. A fresh database will be initialized via migrations."
    fi
fi

# Always run migrations to ensure the schema is up to date (fail-fast).
echo "Running migrations..."
python manage.py migrate --noinput

# Collect static files into the writable STATIC_ROOT (fail-fast).
echo "Collecting static files into $STATIC_ROOT..."
python manage.py collectstatic --noinput --clear

# Start gunicorn (reads PORT env var, defaults to 8000).
echo "Starting gunicorn..."
exec gunicorn --config syntel/gunicorn_config.py syntel.wsgi:application
