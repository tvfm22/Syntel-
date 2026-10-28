#!/bin/bash

# Syntel Server Deployment Script
# Deploy on IP: 45.159.150.199

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"

echo "=========================================="
echo "    Syntel Server Deployment Script      "
echo "=========================================="
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored messages
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

# Step 1: Install dependencies
print_info "Step 1: Installing system dependencies..."
apt-get update
apt-get install -y python3 python3-pip python3-venv nginx postgresql postgresql-contrib

# Step 2: Create virtual environment
print_info "Step 2: Setting up Python virtual environment..."
cd "$PROJECT_ROOT"
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate

# Step 3: Install Python packages
print_info "Step 3: Installing Python packages..."
pip install --upgrade pip
pip install -r requirements.txt

# Step 4: Setup environment file
print_info "Step 4: Setting up environment variables..."
if [ ! -f ".env.production" ]; then
    print_warning ".env.production not found, creating from .env.example..."
    if [ -f ".env.example" ]; then
        cp .env.example .env.production
    else
        cat > .env.production << EOF
DJANGO_SETTINGS_MODULE=syntel.settings_production
DEBUG=False
SECRET_KEY=$(python3 -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())')
ALLOWED_HOSTS=45.159.150.199,localhost
DATA_PATH=${DATA_PATH:-/app/data}
DATABASE_URL=sqlite:///${DATA_PATH}/db/db.sqlite3
WORKERS=5
EOF
    fi
    print_warning "Please edit .env.production and set your configuration!"
fi

# Step 5: Create necessary directories (writable data under DATA_PATH, default /app/data)
print_info "Step 5: Creating necessary directories..."
mkdir -p "${DATA_PATH:-/app/data}"/{logs,db,uploads,cache,tmp,sessions,backups} staticfiles

# Step 6: Django setup
print_info "Step 6: Running Django migrations and collecting static files..."
export DJANGO_SETTINGS_MODULE=syntel.settings_production
python manage.py migrate --noinput
python manage.py collectstatic --noinput

# Step 7: Create superuser (if needed)
print_info "Step 7: Checking for superuser..."
python manage.py shell << EOF
from django.contrib.auth import get_user_model
User = get_user_model()
if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser('admin', 'admin@syntel.com', 'admin123')
    print('Superuser created: admin / admin123')
else:
    print('Superuser already exists')
EOF

# Step 8: Setup Nginx
print_info "Step 8: Configuring Nginx..."
cp nginx_server.conf /etc/nginx/sites-available/syntel
ln -sf /etc/nginx/sites-available/syntel /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t

# Step 9: Setup Systemd service
print_info "Step 9: Configuring systemd service..."
cp syntel.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable syntel
systemctl restart syntel

# Step 10: Restart Nginx
print_info "Step 10: Restarting Nginx..."
systemctl restart nginx

# Step 11: Check status
print_info "Step 11: Checking services status..."
sleep 3
systemctl status syntel --no-pager || true
systemctl status nginx --no-pager || true

echo ""
echo "=========================================="
print_info "Deployment completed successfully!"
echo "=========================================="
echo ""
echo "Application URL: http://45.159.150.199"
echo "Admin Panel: http://45.159.150.199/admin"
echo "Default credentials: admin / admin123"
echo ""
echo "Useful commands:"
echo "  - View logs: journalctl -u syntel -f"
echo "  - Restart service: systemctl restart syntel"
echo "  - Check status: systemctl status syntel"
echo ""
