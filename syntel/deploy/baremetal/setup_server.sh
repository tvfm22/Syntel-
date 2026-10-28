#!/bin/bash

# Initial Server Setup Script for Syntel on 45.159.150.199
# Run this script on the server for the first time setup

set -e

echo "🔧 Setting up Syntel Production Environment..."

# Update system packages
echo "📦 Updating system packages..."
sudo apt-get update
sudo apt-get upgrade -y

# Install required system packages
echo "📦 Installing system dependencies..."
sudo apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    nginx \
    git \
    curl \
    wget \
    sqlite3 \
    supervisor

# Install Python packages
echo "🐍 Setting up Python environment..."
cd /opt/project/Syntel-/syntel

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
pip install --upgrade pip
pip install -r requirements.txt
pip install gunicorn

# Create necessary directories (writable data under DATA_PATH, default /app/data)
mkdir -p "${DATA_PATH:-/app/data}"/{logs,db,uploads,cache,tmp,sessions,backups} staticfiles

# Set permissions
chmod +x *.sh

# Configure Nginx
echo "🌐 Configuring Nginx..."
sudo cp nginx.conf /etc/nginx/sites-available/syntel
sudo ln -sf /etc/nginx/sites-available/syntel /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default

# Test Nginx configuration
sudo nginx -t

# Configure firewall
echo "🔥 Configuring firewall..."
sudo ufw allow 22/tcp  # SSH
sudo ufw allow 80/tcp  # HTTP
sudo ufw allow 8000/tcp  # Gunicorn
sudo ufw --force enable

# Setup systemd service
echo "⚙️ Setting up systemd service..."
sudo tee /etc/systemd/system/syntel.service > /dev/null <<EOF
[Unit]
Description=Syntel Django Application
After=network.target

[Service]
Type=forking
User=$USER
Group=$USER
WorkingDirectory=/opt/project/Syntel-/syntel
Environment="PATH=/opt/project/Syntel-/syntel/venv/bin"
ExecStart=/opt/project/Syntel-/syntel/start_production.sh
ExecStop=/opt/project/Syntel-/syntel/stop_production.sh
ExecReload=/opt/project/Syntel-/syntel/restart_production.sh
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd
sudo systemctl daemon-reload
sudo systemctl enable syntel

echo "✅ Server setup completed!"
echo ""
echo "Next steps:"
echo "1. Edit .env.production with your settings"
echo "2. Run: ./start_production.sh"
echo "3. Start Nginx: sudo systemctl restart nginx"
echo "4. Access your site at: http://45.159.150.199"
