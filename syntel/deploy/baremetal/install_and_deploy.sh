#!/bin/bash

# Complete Deployment Script - From Zero to Production
# This script will install everything and deploy Syntel

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}"
echo "╔══════════════════════════════════════╗"
echo "║   Syntel Complete Deployment         ║"
echo "║   Server: 45.159.150.199            ║"
echo "╚══════════════════════════════════════╝"
echo -e "${NC}"

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo -e "${YELLOW}Running with sudo...${NC}"
    sudo "$0" "$@"
    exit $?
fi

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Step 1: Check Docker
echo -e "\n${YELLOW}[STEP 1] Checking Docker installation...${NC}"
if ! command -v docker &> /dev/null; then
    echo -e "${YELLOW}Docker not found. Installing...${NC}"
    
    # Install Docker
    apt-get update
    apt-get install -y curl
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    rm get-docker.sh
    
    # Start Docker
    systemctl enable docker
    systemctl start docker
    
    echo -e "${GREEN}✓ Docker installed successfully${NC}"
else
    echo -e "${GREEN}✓ Docker already installed${NC}"
fi

# Step 2: Check Docker Compose
echo -e "\n${YELLOW}[STEP 2] Checking Docker Compose installation...${NC}"
if ! command -v docker-compose &> /dev/null; then
    echo -e "${YELLOW}Docker Compose not found. Installing...${NC}"
    
    COMPOSE_VERSION=$(curl -s https://api.github.com/repos/docker/compose/releases/latest | grep 'tag_name' | cut -d\" -f4)
    curl -L "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    chmod +x /usr/local/bin/docker-compose
    
    echo -e "${GREEN}✓ Docker Compose installed successfully${NC}"
else
    echo -e "${GREEN}✓ Docker Compose already installed${NC}"
fi

# Show versions
echo -e "\n${BLUE}Installed versions:${NC}"
docker --version
docker-compose --version

# Step 3: Check .env file
echo -e "\n${YELLOW}[STEP 3] Checking configuration...${NC}"
if [ ! -f .env ]; then
    echo -e "${RED}Error: .env file not found!${NC}"
    echo "Please create .env file first."
    exit 1
fi
echo -e "${GREEN}✓ Configuration file found${NC}"

# Step 4: Setup firewall (if available)
echo -e "\n${YELLOW}[STEP 4] Configuring firewall...${NC}"
if command -v ufw &> /dev/null; then
    ufw --force enable
    ufw allow 22/tcp
    ufw allow 80/tcp
    ufw allow 443/tcp
    ufw reload
    echo -e "${GREEN}✓ Firewall configured${NC}"
else
    echo -e "${YELLOW}⚠ UFW not found, skipping firewall configuration${NC}"
fi

# Step 5: Create directories (writable data under DATA_PATH, default /app/data)
echo -e "\n${YELLOW}[STEP 5] Creating directories...${NC}"
mkdir -p "${DATA_PATH:-/app/data}"/{logs,db,uploads,cache,tmp,sessions,backups} staticfiles ssl
chmod 755 "${DATA_PATH:-/app/data}"/{logs,db,uploads,cache,tmp,sessions,backups} staticfiles
echo -e "${GREEN}✓ Directories created${NC}"

# Step 6: Run deployment
echo -e "\n${YELLOW}[STEP 6] Starting deployment...${NC}"
echo -e "${BLUE}This may take 5-10 minutes...${NC}\n"

# Stop existing containers
docker-compose down 2>/dev/null || true

# Clean up
docker system prune -f

# Build images
echo -e "${YELLOW}Building Docker images...${NC}"
docker-compose build --no-cache

# Start database first
echo -e "${YELLOW}Starting database...${NC}"
docker-compose up -d db redis

# Wait for database
echo -e "${YELLOW}Waiting for database to be ready...${NC}"
sleep 15
until docker exec syntel_db pg_isready -U syntel_user -d syntel_prod > /dev/null 2>&1; do
    echo -n "."
    sleep 2
done
echo -e "\n${GREEN}✓ Database is ready${NC}"

# Run migrations
echo -e "${YELLOW}Running migrations...${NC}"
docker-compose run --rm web python manage.py migrate --noinput

# Collect static files
echo -e "${YELLOW}Collecting static files...${NC}"
docker-compose run --rm web python manage.py collectstatic --noinput

# Create superuser
echo -e "${YELLOW}Creating superuser...${NC}"
docker-compose run --rm web python manage.py shell << 'EOF'
from purchases.models import User
if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser('admin', 'admin@syntel.com', 'admin123')
    print('✓ Superuser created')
else:
    print('✓ Superuser already exists')
EOF

# Start all services
echo -e "${YELLOW}Starting all services...${NC}"
docker-compose up -d

# Wait for services
sleep 10

# Step 7: Verify deployment
echo -e "\n${YELLOW}[STEP 7] Verifying deployment...${NC}"

# Check containers
if docker-compose ps | grep -q "Up"; then
    echo -e "${GREEN}✓ Containers are running${NC}"
else
    echo -e "${RED}✗ Some containers failed${NC}"
    docker-compose ps
    echo -e "\n${YELLOW}Showing logs:${NC}"
    docker-compose logs --tail=50
    exit 1
fi

# Check web service
sleep 5
if curl -f http://localhost:80/health/ > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Web service is responding${NC}"
else
    echo -e "${YELLOW}⚠ Web service not responding yet (may need more time)${NC}"
fi

# Show status
echo -e "\n${BLUE}Container Status:${NC}"
docker-compose ps

# Success message
echo -e "\n${GREEN}"
echo "╔══════════════════════════════════════╗"
echo "║     Deployment Successful! 🎉        ║"
echo "╚══════════════════════════════════════╝"
echo -e "${NC}"

echo -e "${BLUE}Access your application:${NC}"
echo -e "  🌐 URL: ${GREEN}http://45.159.150.199${NC}"
echo -e "\n${BLUE}Default login credentials:${NC}"
echo -e "  👤 Username: ${GREEN}admin${NC}"
echo -e "  🔑 Password: ${GREEN}admin123${NC}"
echo -e "\n${RED}⚠️  IMPORTANT: Change the default password immediately!${NC}"

echo -e "\n${BLUE}Useful commands:${NC}"
echo -e "  View logs:     ${YELLOW}docker-compose logs -f${NC}"
echo -e "  Restart:       ${YELLOW}docker-compose restart${NC}"
echo -e "  Stop:          ${YELLOW}docker-compose down${NC}"
echo -e "  Health check:  ${YELLOW}./health_check.sh${NC}"
echo -e "  Backup:        ${YELLOW}./backup.sh${NC}"

echo -e "\n${BLUE}Documentation:${NC}"
echo -e "  Quick Start:   ${YELLOW}cat QUICKSTART.md${NC}"
echo -e "  Full Guide:    ${YELLOW}cat DEPLOYMENT.md${NC}"

echo -e "\n${GREEN}Done! Your Syntel system is now running.${NC}\n"
