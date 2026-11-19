"""Gunicorn configuration file for production deployment."""

import multiprocessing
import os
import subprocess
import sys
from pathlib import Path

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


def on_starting(server):
    """Run pending Django migrations once in the master process before workers fork.

    Django PaaS (S2I) deployments do not execute Dockerfile/entrypoint.sh, so running
    `manage.py migrate --noinput` here guarantees new tables (e.g. `authtoken_token`
    for the mobile API) are created automatically on every deploy without risking
    concurrent SQLite writes across workers or leaking DB connections across fork().
    """
    if os.getenv('AUTO_MIGRATE_ON_START', '1').strip().lower() in ('0', 'false', 'no'):
        return

    manage_py = Path(__file__).resolve().parent.parent / 'manage.py'
    if not manage_py.exists():
        server.log.warning("manage.py not found at %s; skipping auto-migrate.", manage_py)
        return

    server.log.info("Running Django migrations via %s...", manage_py)
    subprocess.run(
        [sys.executable, str(manage_py), 'migrate', '--noinput'],
        check=True,
    )
    server.log.info("Django migrations complete.")
