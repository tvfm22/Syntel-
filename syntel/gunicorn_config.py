"""Gunicorn configuration file for production deployment."""

import os

# Server socket — honor platform PORT env var (default 8000)
_port = os.environ.get('PORT', '8000').strip() or '8000'
bind = f"0.0.0.0:{_port}"
backlog = 512

# Worker processes - Optimized for low-memory environments
workers = int(os.getenv('WORKERS', 2))
worker_class = 'sync'
worker_connections = 100
timeout = 120
keepalive = 2

# Restart workers periodically to limit memory growth
max_requests = 500
max_requests_jitter = 50

# Logging - Send logs to stdout/stderr
accesslog = '-'
errorlog = '-'
loglevel = os.getenv('GUNICORN_LOG_LEVEL', 'info')
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'

# Process naming
proc_name = 'syntel_gunicorn'

# Server mechanics
daemon = False
pidfile = None
user = None
group = None
tmp_upload_dir = None
