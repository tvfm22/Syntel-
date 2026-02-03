#!/bin/bash
# Production Deployment Validation Script
# This script validates the production environment before deployment

set -e

echo "🔍 Syntel Production Deployment Validation"
echo "=========================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Validation counters
PASSED=0
FAILED=0

validate() {
    local test_name="$1"
    local command="$2"

    echo -n "Testing $test_name... "

    if eval "$command" > /dev/null 2>&1; then
        echo -e "${GREEN}PASSED${NC}"
        ((PASSED++))
    else
        echo -e "${RED}FAILED${NC}"
        ((FAILED++))
    fi
}

# Check if we're in the correct directory
validate "Project Directory" "test -f manage.py"

# Check Python version
validate "Python Version (3.11+)" "python3 --version | grep -q 'Python 3\.\(11\|12\)'"

# Check if virtual environment is active
validate "Virtual Environment" "test -n '$VIRTUAL_ENV'"

# Check required files exist
validate "Production Settings" "test -f syntel/settings_production.py"
validate "Environment File" "test -f .env"
validate "Requirements File" "test -f requirements.txt"
validate "Dockerfile" "test -f Dockerfile"
validate "Docker Compose" "test -f docker-compose.yml"
validate "Nginx Config" "test -f nginx.conf"
validate "Deployment Script" "test -f deploy_production.sh"

# Check environment variables
validate "DJANGO_SECRET_KEY" "test -n '$DJANGO_SECRET_KEY'"
validate "DJANGO_ALLOWED_HOSTS" "test -n '$DJANGO_ALLOWED_HOSTS'"
validate "Database Password" "test -n '$DB_PASSWORD'"
validate "Email Configuration" "test -n '$EMAIL_HOST_USER' && test -n '$EMAIL_HOST_PASSWORD'"

# Check database connectivity
validate "Database Connection" "python3 manage.py dbshell --settings=syntel.settings_production -c 'SELECT 1;'"

# Check Django migrations
validate "Database Migrations" "python3 manage.py showmigrations --settings=syntel.settings_production | grep -q '\[X\]'"

# Check static files collection
validate "Static Files" "test -d staticfiles && test -n '$(ls -A staticfiles)'"

# Check Redis connectivity (if configured)
if [ -n "$REDIS_URL" ]; then
    validate "Redis Connection" "python3 -c \"import redis; r = redis.from_url('$REDIS_URL'); r.ping()\""
else
    echo -e "Redis Connection... ${YELLOW}SKIPPED${NC} (not configured)"
fi

# Check SSL certificates (if configured)
if [ -n "$SSL_CERT_PATH" ] && [ -n "$SSL_KEY_PATH" ]; then
    validate "SSL Certificates" "test -f '$SSL_CERT_PATH' && test -f '$SSL_KEY_PATH'"
else
    echo -e "SSL Certificates... ${YELLOW}SKIPPED${NC} (not configured)"
fi

# Run Django system checks
validate "Django System Checks" "python3 manage.py check --settings=syntel.settings_production --deploy"

# Run Django tests
validate "Django Tests" "python3 manage.py test --settings=syntel.settings_production"

# Check Gunicorn configuration
validate "Gunicorn Workers" "test ${GUNICORN_WORKERS:-4} -gt 0"

echo ""
echo "=========================================="
echo "Validation Summary:"
echo -e "Passed: ${GREEN}$PASSED${NC}"
echo -e "Failed: ${RED}$FAILED${NC}"

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}✅ All validation checks passed! Ready for deployment.${NC}"
    exit 0
else
    echo -e "${RED}❌ $FAILED validation checks failed. Please fix before deployment.${NC}"
    exit 1
fi