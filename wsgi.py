"""Root WSGI entry point for PaaS / S2I Django deployments without Docker."""
import os
from pathlib import Path
import sys

_INNER_ROOT = str(Path(__file__).resolve().parent / 'syntel')
if _INNER_ROOT not in sys.path:
    sys.path.insert(0, _INNER_ROOT)

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings_production')

application = get_wsgi_application()
