"""
ASGI config for syntel project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os
import sys
from pathlib import Path

_BASE_DIR = str(Path(__file__).resolve().parent.parent)
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

_outer_pkg = sys.modules.get('syntel')
if _outer_pkg is not None and getattr(_outer_pkg, '__file__', None) is None:
    sys.modules.pop('syntel', None)

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')

application = get_asgi_application()
