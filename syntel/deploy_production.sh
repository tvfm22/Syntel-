#!/bin/bash

# Syntel Production Deployment Script
# Server IP: 45.159.150.199
# Date: February 8, 2026

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}================================${NC}"
echo -e "${GREEN}Syntel Production Deployment${NC}"
echo -e "${GREEN}Server: 45.159.150.199${NC}"
echo -e "${GREEN}================================${NC}"

# Check if .env file exists
if [ ! -f .env ]; then
    echo -e "${RED}Error: .env file not found!${NC}"
    echo "Please create .env file first."
    exit 1
fi

# Load environment variables
source .env

# Step 1: Stop existing containers
echo -e "${YELLOW}[1/10] Stopping existing containers...${NC}"
docker-compose down || true

# Step 2: Backup database (if exists)
echo -e "${YELLOW}[2/10] Creating database backup...${NC}"
if docker ps -a | grep -q syntel_db; then
    mkdir -p ./backups
    BACKUP_FILE="./backups/backup_$(date +%Y%m%d_%H%M%S).sql"
    docker exec syntel_db pg_dump -U syntel_user syntel_prod > "$BACKUP_FILE" 2>/dev/null || echo "No previous database to backup"
    echo -e "${GREEN}Backup created: $BACKUP_FILE${NC}"
fi

# Step 3: Clean up old Docker resources
echo -e "${YELLOW}[3/10] Cleaning up old Docker resources...${NC}"
docker system prune -f

# Step 4: Build Docker images
echo -e "${YELLOW}[4/10] Building Docker images...${NC}"
docker-compose build --no-cache

# Step 5: Start database and redis first
echo -e "${YELLOW}[5/10] Starting database and cache services...${NC}"
docker-compose up -d db redis

# Wait for database to be ready
echo -e "${YELLOW}Waiting for database to be ready...${NC}"
sleep 10
until docker exec syntel_db pg_isready -U syntel_user -d syntel_prod > /dev/null 2>&1; do
    echo -n "."
    sleep 2
done
echo -e "${GREEN}Database is ready!${NC}"

# Step 6: Run migrations
echo -e "${YELLOW}[6/10] Running database migrations...${NC}"
docker-compose run --rm web python manage.py migrate --noinput

# Step 7: Collect static files
echo -e "${YELLOW}[7/10] Collecting static files...${NC}"
docker-compose run --rm web python manage.py collectstatic --noinput

# Step 8: Create superuser if needed (skip if exists)
echo -e "${YELLOW}[8/10] Checking superuser...${NC}"
docker-compose run --rm web python manage.py shell << EOF
from purchases.models import User
if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser('admin', 'admin@syntel.com', 'admin123')
    print('Superuser created: admin/admin123')
else:
    print('Superuser already exists')
EOF

# Step 9: Start all services
echo -e "${YELLOW}[9/10] Starting all services...${NC}"
docker-compose up -d

# Wait for services to be ready
echo -e "${YELLOW}Waiting for services to start...${NC}"
sleep 15

# Step 10: Verify deployment
echo -e "${YELLOW}[10/10] Verifying deployment...${NC}"

# Check if containers are running
if docker-compose ps | grep -q "Up"; then
    echo -e "${GREEN}✓ Containers are running${NC}"
else
    echo -e "${RED}✗ Some containers failed to start${NC}"
    docker-compose ps
    exit 1
fi

# Check web service health
if curl -f http://localhost:80/health/ > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Web service is responding${NC}"
else
    echo -e "${RED}✗ Web service is not responding${NC}"
fi

# Show running containers
echo -e "\n${GREEN}Running containers:${NC}"
docker-compose ps

# Show logs
echo -e "\n${YELLOW}Recent logs (last 20 lines):${NC}"
docker-compose logs --tail=20

echo -e "\n${GREEN}================================${NC}"
echo -e "${GREEN}Deployment Complete!${NC}"
echo -e "${GREEN}================================${NC}"
echo -e "Access your application at:"
echo -e "${GREEN}http://45.159.150.199${NC}"
echo -e "\nDefault credentials:"
echo -e "Username: ${YELLOW}admin${NC}"
echo -e "Password: ${YELLOW}admin123${NC}"
echo -e "\n${RED}IMPORTANT: Change the default password immediately!${NC}"
echo -e "\nUseful commands:"
echo -e "  View logs:        ${YELLOW}docker-compose logs -f${NC}"
echo -e "  Restart services: ${YELLOW}docker-compose restart${NC}"
echo -e "  Stop services:    ${YELLOW}docker-compose down${NC}"
echo -e "  Database shell:   ${YELLOW}docker exec -it syntel_db psql -U syntel_user -d syntel_prod${NC}"
