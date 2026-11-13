"""
WSGI config for syntel project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os
import sys
from pathlib import Path

# Ensure the inner syntel/ project root (containing purchases/ and syntel/) is on sys.path
_BASE_DIR = str(Path(__file__).resolve().parent.parent)
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

# If imported as `syntel.syntel.wsgi` from the repo root, clear the outer namespace
# package from sys.modules so `syntel.settings` resolves to the inner package.
_outer_pkg = sys.modules.get('syntel')
if _outer_pkg is not None and getattr(_outer_pkg, '__file__', None) is None:
    sys.modules.pop('syntel', None)

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')

application = get_wsgi_application()
