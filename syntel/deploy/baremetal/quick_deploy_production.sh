#!/bin/bash

# Quick Deploy Script for Syntel on 45.159.150.199
# This script handles complete deployment in one go

set -e

echo "================================================="
echo "🚀 Syntel Quick Deploy - IP: 45.159.150.199"
echo "================================================="
echo ""

# Change to project directory
cd /opt/project/Syntel-/syntel

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source venv/bin/activate

# Install/Update dependencies
echo "📦 Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt
pip install gunicorn

# Create necessary directories (writable data under DATA_PATH, default /app/data)
echo "📁 Creating necessary directories..."
mkdir -p "${DATA_PATH:-/app/data}"/{logs,db,uploads,cache,tmp,sessions,backups} staticfiles

# Run migrations
echo "🗄️ Running database migrations..."
python manage.py migrate --settings=syntel.settings_production --noinput

# Collect static files
echo "📁 Collecting static files..."
python manage.py collectstatic --settings=syntel.settings_production --noinput --clear

# Stop existing processes
echo "🛑 Stopping existing processes..."
pkill -f gunicorn || echo "No gunicorn process found"
sleep 2

# Start Gunicorn
echo "🚀 Starting Gunicorn server..."
gunicorn syntel.wsgi:application \
    --config gunicorn_config.py \
    --env DJANGO_SETTINGS_MODULE=syntel.settings_production \
    --daemon

# Wait for server to start
sleep 3

# Check if server is running
if pgrep -f gunicorn > /dev/null; then
    echo ""
    echo "================================================="
    echo "✅ Deployment Successful!"
    echo "================================================="
    echo ""
    echo "📊 Server Status:"
    echo "  - Django: Running on port 8000"
    echo "  - URL: http://45.159.150.199:8000"
    echo ""
    echo "📝 Logs:"
    echo "  - Access: ${DATA_PATH:-/app/data}/logs/gunicorn-access.log"
    echo "  - Error: ${DATA_PATH:-/app/data}/logs/gunicorn-error.log"
    echo ""
    echo "🔧 Management Commands:"
    echo "  - Stop: ./stop_production.sh"
    echo "  - Restart: ./restart_production.sh"
    echo "  - Logs: tail -f ${DATA_PATH:-/app/data}/logs/gunicorn-error.log"
    echo ""
    echo "================================================="
else
    echo ""
    echo "❌ Failed to start server!"
    echo "Check logs at: ${DATA_PATH:-/app/data}/logs/gunicorn-error.log"
    exit 1
fi
