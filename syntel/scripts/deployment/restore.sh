#!/bin/bash

# Restore Script for Syntel
# Restores database and media files from backup

set -e

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# Check if backup file is provided
if [ -z "$1" ]; then
    print_error "Please provide the backup date (e.g., 20260208_143000)"
    print_info "Available backups:"
    ls -1 backups/syntel_backup_* 2>/dev/null | grep -o '[0-9]\{8\}_[0-9]\{6\}' | sort -u || echo "No backups found"
    exit 1
fi

BACKUP_DATE=$1
BACKUP_DIR="./backups"
DB_BACKUP="${BACKUP_DIR}/syntel_backup_${BACKUP_DATE}_db.sql"
MEDIA_BACKUP="${BACKUP_DIR}/syntel_backup_${BACKUP_DATE}_media.tar.gz"

# Check if backup files exist
if [ ! -f "${DB_BACKUP}" ]; then
    print_error "Database backup not found: ${DB_BACKUP}"
    exit 1
fi

if [ ! -f "${MEDIA_BACKUP}" ]; then
    print_warning "Media backup not found: ${MEDIA_BACKUP}"
fi

# Confirmation
print_warning "This will restore data from backup: ${BACKUP_DATE}"
print_warning "All current data will be replaced!"
read -p "Are you sure you want to continue? (yes/no): " confirm

if [ "$confirm" != "yes" ]; then
    print_info "Restore cancelled"
    exit 0
fi

print_info "Starting restore process..."

# Restore database
print_info "Restoring database..."
docker-compose exec -T db psql -U syntel_user -d syntel_prod -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
docker-compose exec -T db psql -U syntel_user -d syntel_prod < "${DB_BACKUP}"

# Restore media files
if [ -f "${MEDIA_BACKUP}" ]; then
    print_info "Restoring media files..."
    rm -rf media/*
    tar -xzf "${MEDIA_BACKUP}"
fi

print_info "Restore completed successfully!"
print_info "Please restart the application: docker-compose restart web"
