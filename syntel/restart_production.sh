#!/bin/bash

# Production Restart Script for Syntel

echo "🔄 Restarting Syntel Production Server..."

# Stop the server
./stop_production.sh

# Wait a moment
sleep 3

# Start the server
./start_production.sh

echo "✅ Server restarted successfully"
