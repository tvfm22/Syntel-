#!/bin/bash

# Server Setup Script for Syntel
# Run this script on your server before deploying
# Usage: sudo bash server_setup.sh

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    print_error "Please run as root (use sudo)"
    exit 1
fi

print_info "Setting up server for Syntel deployment..."

# Update system packages
print_info "Updating system packages..."
apt-get update
apt-get upgrade -y

# Install required packages
print_info "Installing required packages..."
apt-get install -y \
    apt-transport-https \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    git \
    nano \
    htop \
    ufw

# Install Docker
if ! command -v docker &> /dev/null; then
    print_info "Installing Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    rm get-docker.sh
    print_info "Docker installed successfully"
else
    print_info "Docker is already installed"
fi

# Install Docker Compose
if ! command -v docker-compose &> /dev/null; then
    print_info "Installing Docker Compose..."
    curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    chmod +x /usr/local/bin/docker-compose
    print_info "Docker Compose installed successfully"
else
    print_info "Docker Compose is already installed"
fi

# Configure firewall
print_info "Configuring firewall..."
ufw --force enable
ufw allow 22/tcp   # SSH
ufw allow 80/tcp   # HTTP
ufw allow 443/tcp  # HTTPS (for future)
ufw status

# Create application user
if ! id -u syntel &> /dev/null; then
    print_info "Creating application user..."
    useradd -m -s /bin/bash syntel
    usermod -aG docker syntel
    print_info "User 'syntel' created"
else
    print_info "User 'syntel' already exists"
fi

# Create application directory
print_info "Creating application directory..."
mkdir -p /opt/syntel
chown syntel:syntel /opt/syntel

# Configure system limits
print_info "Configuring system limits..."
cat >> /etc/security/limits.conf << 'EOF'
syntel soft nofile 65536
syntel hard nofile 65536
EOF

# Configure sysctl for better performance
print_info "Configuring kernel parameters..."
cat >> /etc/sysctl.conf << 'EOF'
# Network optimizations
net.core.somaxconn = 1024
net.ipv4.tcp_max_syn_backlog = 2048
net.ipv4.ip_local_port_range = 10000 65535
net.ipv4.tcp_tw_reuse = 1
EOF
sysctl -p

# Enable Docker service
print_info "Enabling Docker service..."
systemctl enable docker
systemctl start docker

# Setup log rotation
print_info "Setting up log rotation..."
cat > /etc/logrotate.d/syntel << 'EOF'
/app/data/logs/*.log {
    daily
    missingok
    rotate 14
    compress
    delaycompress
    notifempty
    create 0640 syntel syntel
    sharedscripts
    postrotate
        docker-compose -f /opt/syntel/docker-compose.yml restart web > /dev/null 2>&1 || true
    endscript
}
EOF

print_info ""
print_info "${GREEN}Server setup completed successfully!${NC}"
print_info ""
print_info "Next steps:"
print_info "1. Clone your repository to /opt/syntel"
print_info "2. Configure the .env file"
print_info "3. Run the deployment script: bash deploy.sh"
print_info ""
print_info "Application user: syntel"
print_info "Application directory: /opt/syntel"
