"""Gunicorn configuration file for production deployment."""

import multiprocessing
import os

# Server socket
bind = "0.0.0.0:8000"
backlog = 512  # Reduced for low memory environments

# Worker processes - Optimized for Liara/low memory environments
# Use 2-3 workers max to prevent OOM (Out of Memory) errors
workers = int(os.getenv('WORKERS', 2))
worker_class = 'sync'
worker_connections = 100  # Reduced from 1000
timeout = 120
keepalive = 2

# Restart workers after this many requests, to help limit memory leaks
max_requests = 500  # Reduced to restart workers more frequently
max_requests_jitter = 50

# Logging - Send logs to stdout/stderr for Docker
accesslog = '-'  # stdout
errorlog = '-'   # stderr
loglevel = 'info'
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'

# Process naming
proc_name = 'syntel_gunicorn'

# Server mechanics
daemon = False
pidfile = None  # Don't use pidfile in Docker
user = None
group = None
tmp_upload_dir = None

# SSL (if needed in future)
# keyfile = None
# certfile = None
