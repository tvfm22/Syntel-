#!/bin/bash

# Production Start Script for Syntel
# IP: 45.159.150.199

set -e

echo "🚀 Starting Syntel in Production Mode..."

# Set working directory
cd /opt/project/Syntel-/syntel

# Load environment variables
if [ -f .env.production ]; then
    export $(cat .env.production | grep -v '^#' | xargs)
fi

# Create necessary directories (writable data under DATA_PATH, default /app/data)
mkdir -p "${DATA_PATH:-/app/data}"/{logs,db,uploads,cache,tmp,sessions,backups} staticfiles

# Activate virtual environment if exists
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Run migrations
echo "📦 Running database migrations..."
python manage.py migrate --settings=syntel.settings_production --noinput

# Collect static files
echo "📁 Collecting static files..."
python manage.py collectstatic --settings=syntel.settings_production --noinput --clear

# Create superuser if needed (optional)
# python manage.py createsuperuser --settings=syntel.settings_production --noinput || true

# Stop any existing gunicorn processes
echo "🛑 Stopping existing processes..."
pkill -f gunicorn || true
sleep 2

# Start Gunicorn
echo "✅ Starting Gunicorn server..."
gunicorn syntel.wsgi:application \
    --config gunicorn_config.py \
    --env DJANGO_SETTINGS_MODULE=syntel.settings_production \
    --daemon

echo "✅ Syntel is now running on http://45.159.150.199:8000"
echo "📊 Check logs at: ${DATA_PATH:-/app/data}/logs/gunicorn-access.log and ${DATA_PATH:-/app/data}/logs/gunicorn-error.log"
echo "🔧 To stop: ./stop_production.sh"
