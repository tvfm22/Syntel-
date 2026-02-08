#!/bin/bash

# Database Backup Script for Syntel
# Creates timestamped backups of PostgreSQL database

set -e

BACKUP_DIR="./backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/syntel_backup_${TIMESTAMP}.sql"

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

echo "📦 Creating database backup..."

# Create backup
docker exec syntel_db pg_dump -U syntel_user -d syntel_prod > "$BACKUP_FILE"

# Compress backup
gzip "$BACKUP_FILE"

echo "✅ Backup created: ${BACKUP_FILE}.gz"
echo "📊 Backup size: $(du -h ${BACKUP_FILE}.gz | cut -f1)"

# Remove backups older than 30 days
find "$BACKUP_DIR" -name "syntel_backup_*.sql.gz" -mtime +30 -delete
echo "🗑️  Old backups (>30 days) removed"

# List recent backups
echo ""
echo "Recent backups:"
ls -lh "$BACKUP_DIR" | tail -5
