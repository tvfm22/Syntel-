#!/bin/bash

# Check Syntel Deployment Status

echo "======================================"
echo "  Syntel Deployment Status Checker   "
echo "======================================"
echo ""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

check_service() {
    if systemctl is-active --quiet $1; then
        echo -e "${GREEN}✓${NC} $1 is running"
        return 0
    else
        echo -e "${RED}✗${NC} $1 is not running"
        return 1
    fi
}

check_port() {
    if netstat -tuln | grep -q ":$1 "; then
        echo -e "${GREEN}✓${NC} Port $1 is listening"
        return 0
    else
        echo -e "${RED}✗${NC} Port $1 is not listening"
        return 1
    fi
}

echo "Checking Services:"
check_service nginx
check_service syntel

echo ""
echo "Checking Ports:"
check_port 80
check_port 8000

echo ""
echo "Testing HTTP Response:"
if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1 | grep -q "200\|301\|302"; then
    echo -e "${GREEN}✓${NC} HTTP server responding"
else
    echo -e "${RED}✗${NC} HTTP server not responding"
fi

echo ""
echo "Recent Gunicorn Logs:"
echo "-----------------------------------"
tail -n 20 "${DATA_PATH:-/app/data}/logs/gunicorn-error.log" 2>/dev/null || echo "No logs found"

echo ""
echo "Recent Nginx Error Logs:"
echo "-----------------------------------"
tail -n 10 /var/log/nginx/syntel-error.log 2>/dev/null || echo "No logs found"

echo ""
echo "To view live logs:"
echo "  - Gunicorn: journalctl -u syntel -f"
echo "  - Nginx: tail -f /var/log/nginx/syntel-error.log"
echo ""
