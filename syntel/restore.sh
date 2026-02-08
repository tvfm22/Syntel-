#!/bin/bash

# Database Restore Script for Syntel
# Restores database from backup file

set -e

if [ $# -eq 0 ]; then
    echo "Usage: ./restore.sh <backup_file>"
    echo ""
    echo "Available backups:"
    ls -lh ./backups/*.sql.gz 2>/dev/null || echo "No backups found"
    exit 1
fi

BACKUP_FILE=$1

if [ ! -f "$BACKUP_FILE" ]; then
    echo "❌ Backup file not found: $BACKUP_FILE"
    exit 1
fi

echo "⚠️  WARNING: This will replace the current database!"
read -p "Are you sure you want to continue? (yes/no): " confirm

if [ "$confirm" != "yes" ]; then
    echo "Restore cancelled"
    exit 0
fi

echo "📦 Restoring database from: $BACKUP_FILE"

# Decompress if needed
if [[ "$BACKUP_FILE" == *.gz ]]; then
    echo "Decompressing backup..."
    gunzip -c "$BACKUP_FILE" > /tmp/restore.sql
    RESTORE_FILE="/tmp/restore.sql"
else
    RESTORE_FILE="$BACKUP_FILE"
fi

# Drop and recreate database
echo "Dropping existing database..."
docker exec syntel_db psql -U syntel_user -c "DROP DATABASE IF EXISTS syntel_prod;"
docker exec syntel_db psql -U syntel_user -c "CREATE DATABASE syntel_prod;"

# Restore backup
echo "Restoring database..."
docker exec -i syntel_db psql -U syntel_user -d syntel_prod < "$RESTORE_FILE"

# Clean up
if [ -f /tmp/restore.sql ]; then
    rm /tmp/restore.sql
fi

echo "✅ Database restored successfully!"
echo "🔄 Restarting web service..."
docker-compose restart web

echo "✅ Restore complete!"
