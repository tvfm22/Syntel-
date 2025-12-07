#!/bin/bash
# Syntel Production Deployment Script
# This script deploys the Syntel Finance Management System to production

set -e  # Exit on any error

# Configuration
APP_NAME="syntel"
APP_DIR="/var/www/syntel"
VENV_DIR="/var/www/syntel/venv"
USER="syntel"
GROUP="www-data"

echo "🚀 Starting Syntel Production Deployment"

# Check if running as root or with sudo
if [[ $EUID -eq 0 ]]; then
   echo "❌ This script should not be run as root for security reasons"
   exit 1
fi

# Update system packages
echo "📦 Updating system packages..."
sudo apt update && sudo apt upgrade -y

# Install required system packages
echo "📦 Installing system dependencies..."
sudo apt install -y python3 python3-pip python3-venv postgresql postgresql-contrib nginx certbot python3-certbot-nginx

# Install Python dependencies
echo "🐍 Installing Python dependencies..."
pip3 install --upgrade pip
pip3 install gunicorn psycopg2-binary django-environ

# Create application directory
echo "📁 Creating application directory..."
sudo mkdir -p $APP_DIR
sudo chown $USER:$GROUP $APP_DIR

# Clone or copy application code (assuming code is already in place)
echo "📋 Setting up application code..."
cd $APP_DIR

# Create virtual environment
echo "🌐 Creating Python virtual environment..."
python3 -m venv $VENV_DIR
source $VENV_DIR/bin/activate

# Install Python packages
echo "📦 Installing Python packages..."
pip install -r requirements.txt

# Database setup
echo "🗄️ Setting up database..."
sudo -u postgres createuser --createdb --no-superuser --no-createrole $DB_USER 2>/dev/null || true
sudo -u postgres psql -c "ALTER USER $DB_USER PASSWORD '$DB_PASSWORD';" 2>/dev/null || true
sudo -u postgres createdb --owner=$DB_USER $DB_NAME 2>/dev/null || true

# Run migrations
echo "🗄️ Running database migrations..."
python manage.py migrate

# Collect static files
echo "📄 Collecting static files..."
python manage.py collectstatic --noinput

# Create logs directory
echo "📝 Setting up logging..."
mkdir -p logs
chmod 755 logs

# Configure Gunicorn
echo "⚙️ Configuring Gunicorn..."
sudo tee /etc/systemd/system/gunicorn.service > /dev/null <<EOF
[Unit]
Description=Syntel Gunicorn daemon
After=network.target

[Service]
User=$USER
Group=$GROUP
WorkingDirectory=$APP_DIR
Environment="PATH=$VENV_DIR/bin"
Environment="DJANGO_SETTINGS_MODULE=syntel.settings_production"
ExecStart=$VENV_DIR/bin/gunicorn --workers 3 --bind unix:/run/gunicorn.sock syntel.wsgi:application
Restart=always

[Install]
WantedBy=multi-user.target
EOF

# Configure Nginx
echo "🌐 Configuring Nginx..."
sudo tee /etc/nginx/sites-available/syntel > /dev/null <<EOF
server {
    listen 80;
    server_name yourdomain.com www.yourdomain.com;

    location = /favicon.ico { access_log off; log_not_found off; }

    location /static/ {
        alias $APP_DIR/staticfiles/;
    }

    location /media/ {
        alias $APP_DIR/media/;
    }

    location / {
        include proxy_params;
        proxy_pass http://unix:/run/gunicorn.sock;
    }
}
EOF

# Enable site
sudo ln -sf /etc/nginx/sites-available/syntel /etc/nginx/sites-enabled
sudo rm -f /etc/nginx/sites-enabled/default

# Test Nginx configuration
echo "🧪 Testing Nginx configuration..."
sudo nginx -t

# Start services
echo "▶️ Starting services..."
sudo systemctl daemon-reload
sudo systemctl start gunicorn
sudo systemctl enable gunicorn
sudo systemctl restart nginx

# SSL Certificate (Let's Encrypt)
echo "🔒 Setting up SSL certificate..."
sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com

# Final checks
echo "✅ Running final checks..."
python manage.py check --deploy

# Create backup script
echo "💾 Setting up backup script..."
sudo tee /usr/local/bin/syntel-backup > /dev/null <<EOF
#!/bin/bash
BACKUP_DIR="/var/backups/syntel"
DATE=\$(date +%Y%m%d_%H%M%S)
mkdir -p \$BACKUP_DIR

# Database backup
pg_dump -U $DB_USER -h localhost $DB_NAME > \$BACKUP_DIR/syntel_db_\$DATE.sql

# Files backup
tar -czf \$BACKUP_DIR/syntel_files_\$DATE.tar.gz -C $APP_DIR media staticfiles

# Clean old backups (keep last 7 days)
find \$BACKUP_DIR -name "*.sql" -mtime +7 -delete
find \$BACKUP_DIR -name "*.tar.gz" -mtime +7 -delete

echo "Backup completed: \$DATE"
EOF

sudo chmod +x /usr/local/bin/syntel-backup

# Set up cron job for backups
echo "⏰ Setting up automated backups..."
sudo crontab -l | { cat; echo "0 2 * * * /usr/local/bin/syntel-backup"; } | sudo crontab -

echo "🎉 Deployment completed successfully!"
echo ""
echo "Next steps:"
echo "1. Update your .env file with production values"
echo "2. Configure your domain DNS to point to this server"
echo "3. Test the application at https://yourdomain.com"
echo "4. Set up monitoring and alerting"
echo "5. Create admin user: python manage.py createsuperuser"
echo ""
echo "Useful commands:"
echo "- Restart app: sudo systemctl restart gunicorn"
echo "- View logs: sudo journalctl -u gunicorn -f"
echo "- Backup: sudo /usr/local/bin/syntel-backup"