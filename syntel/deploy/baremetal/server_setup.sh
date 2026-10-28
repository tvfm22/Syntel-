#!/bin/bash

# Server Initial Setup and Installation Script
# For Ubuntu/Debian servers
# Server IP: 45.159.150.199

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}Syntel Server Initial Setup${NC}"
echo -e "${GREEN}======================================${NC}"

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo -e "${RED}Please run as root (use sudo)${NC}"
    exit 1
fi

# Update system
echo -e "${YELLOW}[1/8] Updating system packages...${NC}"
apt-get update
apt-get upgrade -y

# Install essential packages
echo -e "${YELLOW}[2/8] Installing essential packages...${NC}"
apt-get install -y \
    curl \
    wget \
    git \
    vim \
    htop \
    ufw \
    fail2ban \
    unzip \
    software-properties-common \
    apt-transport-https \
    ca-certificates \
    gnupg \
    lsb-release

# Install Docker
echo -e "${YELLOW}[3/8] Installing Docker...${NC}"
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    rm get-docker.sh
    systemctl enable docker
    systemctl start docker
    echo -e "${GREEN}✓ Docker installed successfully${NC}"
else
    echo -e "${GREEN}✓ Docker already installed${NC}"
fi

# Install Docker Compose
echo -e "${YELLOW}[4/8] Installing Docker Compose...${NC}"
if ! command -v docker-compose &> /dev/null; then
    DOCKER_COMPOSE_VERSION=$(curl -s https://api.github.com/repos/docker/compose/releases/latest | grep 'tag_name' | cut -d\" -f4)
    curl -L "https://github.com/docker/compose/releases/download/${DOCKER_COMPOSE_VERSION}/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    chmod +x /usr/local/bin/docker-compose
    echo -e "${GREEN}✓ Docker Compose installed successfully${NC}"
else
    echo -e "${GREEN}✓ Docker Compose already installed${NC}"
fi

# Configure firewall
echo -e "${YELLOW}[5/8] Configuring firewall...${NC}"
ufw --force enable
ufw allow 22/tcp   # SSH
ufw allow 80/tcp   # HTTP
ufw allow 443/tcp  # HTTPS
ufw reload
echo -e "${GREEN}✓ Firewall configured${NC}"

# Configure fail2ban
echo -e "${YELLOW}[6/8] Configuring fail2ban...${NC}"
systemctl enable fail2ban
systemctl start fail2ban
echo -e "${GREEN}✓ Fail2ban configured${NC}"

# Create project directory
echo -e "${YELLOW}[7/8] Creating project directory...${NC}"
mkdir -p /opt/syntel
# All writable data lives under DATA_PATH (default: /app/data)
mkdir -p "${DATA_PATH:-/app/data}"/{logs,db,uploads,cache,tmp,sessions,backups}
chmod 755 /opt/syntel
echo -e "${GREEN}✓ Project directories created${NC}"

# Configure system limits
echo -e "${YELLOW}[8/8] Configuring system limits...${NC}"
cat >> /etc/security/limits.conf << EOF
* soft nofile 65536
* hard nofile 65536
EOF

cat >> /etc/sysctl.conf << EOF
# Syntel Production Settings
net.core.somaxconn = 1024
net.ipv4.tcp_max_syn_backlog = 2048
vm.overcommit_memory = 1
EOF
sysctl -p

echo -e "\n${GREEN}======================================${NC}"
echo -e "${GREEN}Server Setup Complete!${NC}"
echo -e "${GREEN}======================================${NC}"

# Show versions
echo -e "\n${YELLOW}Installed versions:${NC}"
echo -e "Docker: $(docker --version)"
echo -e "Docker Compose: $(docker-compose --version)"
echo -e "Git: $(git --version)"

echo -e "\n${YELLOW}Next steps:${NC}"
echo -e "1. Copy project files to ${GREEN}/opt/syntel${NC}"
echo -e "2. Configure ${GREEN}.env${NC} file"
echo -e "3. Run ${GREEN}./deploy_production.sh${NC}"

echo -e "\n${YELLOW}Access your server:${NC}"
echo -e "SSH: ${GREEN}ssh root@45.159.150.199${NC}"
echo -e "Web: ${GREEN}http://45.159.150.199${NC}"
