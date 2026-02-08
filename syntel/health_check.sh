#!/bin/bash

# Health Check Script for Syntel Production

echo "🏥 Syntel System Health Check"
echo "=============================="

# Check Docker
echo ""
echo "🐳 Docker Status:"
if command -v docker &> /dev/null; then
    echo "✅ Docker installed"
    docker --version
else
    echo "❌ Docker not installed"
fi

# Check containers
echo ""
echo "📦 Container Status:"
docker-compose ps

# Check database connection
echo ""
echo "🗄️  Database Status:"
if docker exec syntel_db pg_isready -U syntel_user -d syntel_prod > /dev/null 2>&1; then
    echo "✅ Database is ready"
    DB_SIZE=$(docker exec syntel_db psql -U syntel_user -d syntel_prod -t -c "SELECT pg_size_pretty(pg_database_size('syntel_prod'));")
    echo "Database size: $DB_SIZE"
else
    echo "❌ Database connection failed"
fi

# Check Redis
echo ""
echo "🔴 Redis Status:"
if docker exec syntel_redis redis-cli ping > /dev/null 2>&1; then
    echo "✅ Redis is responding"
else
    echo "❌ Redis connection failed"
fi

# Check web service
echo ""
echo "🌐 Web Service Status:"
if curl -f http://localhost:80/health/ > /dev/null 2>&1; then
    echo "✅ Web service is responding"
    RESPONSE_TIME=$(curl -o /dev/null -s -w '%{time_total}' http://localhost:80/health/)
    echo "Response time: ${RESPONSE_TIME}s"
else
    echo "❌ Web service not responding"
fi

# Check disk space
echo ""
echo "💾 Disk Space:"
df -h | grep -E "Filesystem|/$"

# Check memory
echo ""
echo "🧠 Memory Usage:"
free -h

# Check system load
echo ""
echo "⚡ System Load:"
uptime

# Check logs for errors
echo ""
echo "📝 Recent Errors (last 10):"
docker-compose logs --tail=100 | grep -i error | tail -10 || echo "No recent errors"

echo ""
echo "=============================="
echo "Health check complete!"
