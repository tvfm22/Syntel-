#!/bin/bash
set -e

echo "Starting Syntel application..."

# Create writable directory for SQLite database
mkdir -p /tmp/db

# Copy database to writable location if it exists
if [ -f "/app/db.sqlite3" ]; then
    echo "Copying database to writable location..."
    cp /app/db.sqlite3 /tmp/db/db.sqlite3
    chmod 666 /tmp/db/db.sqlite3
    export DATABASE_PATH="/tmp/db/db.sqlite3"
else
    echo "No database file found, will create new one..."
    export DATABASE_PATH="/tmp/db/db.sqlite3"
    # Run migrations to create database
    python manage.py migrate --noinput || echo "Migration failed, continuing..."
fi

# Create static files directory
mkdir -p /tmp/staticfiles /tmp/media
export STATIC_ROOT="/tmp/staticfiles"
export MEDIA_ROOT="/tmp/media"

# Collect static files
python manage.py collectstatic --noinput || echo "Collectstatic failed, continuing..."

# Start gunicorn
echo "Starting gunicorn..."
exec gunicorn --config gunicorn_config.py syntel.wsgi:application
