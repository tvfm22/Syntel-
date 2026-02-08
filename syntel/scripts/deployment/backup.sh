#!/bin/bash

# Backup Script for Syntel
# Creates backup of database and media files

set -e

# Colors
GREEN='\033[0;32m'
NC='\033[0m'

print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

# Configuration
BACKUP_DIR="./backups"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_NAME="syntel_backup_${DATE}"

# Create backup directory
mkdir -p ${BACKUP_DIR}

print_info "Starting backup process..."

# Backup database
print_info "Backing up database..."
docker-compose exec -T db pg_dump -U syntel_user syntel_prod > "${BACKUP_DIR}/${BACKUP_NAME}_db.sql"

# Backup media files
print_info "Backing up media files..."
tar -czf "${BACKUP_DIR}/${BACKUP_NAME}_media.tar.gz" media/

# Create backup info file
cat > "${BACKUP_DIR}/${BACKUP_NAME}_info.txt" << EOF
Backup created at: $(date)
Database backup: ${BACKUP_NAME}_db.sql
Media backup: ${BACKUP_NAME}_media.tar.gz
EOF

print_info "Backup completed successfully!"
print_info "Backup files saved to: ${BACKUP_DIR}"
print_info "  - Database: ${BACKUP_NAME}_db.sql"
print_info "  - Media: ${BACKUP_NAME}_media.tar.gz"

# Remove old backups (keep last 7 days)
print_info "Cleaning old backups (keeping last 7 days)..."
find ${BACKUP_DIR} -name "syntel_backup_*" -type f -mtime +7 -delete

print_info "Backup process completed!"
