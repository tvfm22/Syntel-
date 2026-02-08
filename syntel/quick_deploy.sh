#!/bin/bash

# Quick Deployment Script for Syntel
# Use this for quick updates without full rebuild

set -e

echo "🚀 Starting quick deployment..."

# Pull latest code
echo "📥 Pulling latest code..."
git pull origin main || echo "Not a git repository or no changes"

# Restart services
echo "🔄 Restarting services..."
docker-compose restart web celery

# Run migrations
echo "📊 Running migrations..."
docker-compose exec web python manage.py migrate --noinput

# Collect static files
echo "📦 Collecting static files..."
docker-compose exec web python manage.py collectstatic --noinput

# Show status
echo "✅ Quick deployment complete!"
docker-compose ps
