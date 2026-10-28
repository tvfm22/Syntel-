#!/bin/bash

# Syntel Deployment Script
# This script deploys the Syntel Finance Management System

set -e  # Exit on any error

# Colors for output
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

# Check if .env file exists
if [ ! -f .env ]; then
    print_error "File .env not found!"
    print_info "Creating .env from template..."
    if [ -f .env.example ]; then
        cp .env.example .env
        print_warning "Please edit .env file with your configuration"
        exit 1
    else
        print_error "Template .env.example not found"
        exit 1
    fi
fi

# Load environment variables
source .env

print_info "Starting Syntel deployment..."

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    print_error "Docker is not installed. Please install Docker first."
    exit 1
fi

# Check if Docker Compose is installed
if ! command -v docker-compose &> /dev/null; then
    print_error "Docker Compose is not installed. Please install Docker Compose first."
    exit 1
fi

# Create necessary directories (writable data under DATA_PATH, default /app/data)
print_info "Creating necessary directories..."
mkdir -p "${DATA_PATH:-/app/data}"/{logs,db,uploads,cache,tmp,sessions,backups} staticfiles

# Stop existing containers
print_info "Stopping existing containers..."
docker-compose down

# Build images
print_info "Building Docker images..."
docker-compose build

# Start database and wait for it to be ready
print_info "Starting database..."
docker-compose up -d db redis

print_info "Waiting for database to be ready..."
sleep 10

# Run migrations
print_info "Running database migrations..."
docker-compose run --rm web python manage.py migrate

# Create superuser if needed
print_info "Checking for superuser..."
docker-compose run --rm web python manage.py shell << EOF
from django.contrib.auth import get_user_model
User = get_user_model()
if not User.objects.filter(username='admin').exists():
    print('Creating superuser...')
    User.objects.create_superuser('admin', 'admin@syntel.com', 'ChangeThisPassword123!')
    print('Superuser created! Username: admin, Password: ChangeThisPassword123!')
    print('Please change this password immediately!')
else:
    print('Superuser already exists')
EOF

# Collect static files
print_info "Collecting static files..."
docker-compose run --rm web python manage.py collectstatic --noinput

# Start all services
print_info "Starting all services..."
docker-compose up -d

# Wait for services to be ready
print_info "Waiting for services to start..."
sleep 5

# Check service status
print_info "Checking service status..."
docker-compose ps

# Test the application
print_info "Testing application health..."
sleep 3
if curl -f http://localhost:80 > /dev/null 2>&1; then
    print_info "${GREEN}✓${NC} Application is running successfully!"
    print_info "Access your application at: http://45.159.150.199"
else
    print_warning "Application health check failed. Check logs for details."
    print_info "View logs with: docker-compose logs"
fi

print_info "Deployment completed!"
print_info ""
print_info "Useful commands:"
print_info "  - View logs: docker-compose logs -f"
print_info "  - Stop services: docker-compose down"
print_info "  - Restart services: docker-compose restart"
print_info "  - View running containers: docker-compose ps"
print_info ""
print_warning "IMPORTANT: Change the default admin password immediately!"
