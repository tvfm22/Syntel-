#!/bin/bash

# Server Status Check Script

echo "🔍 Checking Syntel Server Status..."
echo ""

# Check if gunicorn is running
if pgrep -f gunicorn > /dev/null; then
    echo "✅ Gunicorn: Running"
    echo "   PID: $(pgrep -f gunicorn | head -1)"
    echo "   Processes: $(pgrep -f gunicorn | wc -l)"
else
    echo "❌ Gunicorn: Not running"
fi

echo ""

# Check if port 8000 is listening
if ss -tulpn | grep :8000 > /dev/null 2>&1; then
    echo "✅ Port 8000: Listening"
else
    echo "❌ Port 8000: Not listening"
fi

echo ""

# Check recent errors (logs live under DATA_PATH, default /app/data)
if [ -f "${DATA_PATH:-/app/data}/logs/gunicorn-error.log" ]; then
    echo "📋 Recent Error Log (last 5 lines):"
    tail -5 "${DATA_PATH:-/app/data}/logs/gunicorn-error.log"
else
    echo "📋 No error log found"
fi

echo ""

# Test HTTP connection
echo "🌐 Testing HTTP connection..."
if curl -s -o /dev/null -w "%{http_code}" http://45.159.150.199:8000 > /dev/null 2>&1; then
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://45.159.150.199:8000)
    echo "✅ HTTP Response: $HTTP_CODE"
else
    echo "❌ Cannot connect to server"
fi

echo ""
echo "================================================="
