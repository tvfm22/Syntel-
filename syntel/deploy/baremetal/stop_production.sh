#!/bin/bash

# Production Stop Script for Syntel

echo "🛑 Stopping Syntel Production Server..."

# Kill gunicorn processes
pkill -f gunicorn || echo "No gunicorn processes found"

# Kill any python manage.py processes
pkill -f "python.*manage.py" || echo "No Django processes found"

echo "✅ Server stopped successfully"
