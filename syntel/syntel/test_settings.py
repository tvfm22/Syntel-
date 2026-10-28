"""Test-only settings.

The project stores sessions as files under DATA_PATH/sessions. The local
execution sandbox blocks bulk deletes of those files, which makes unrelated
tests fail while Django rotates test sessions. Tests use signed-cookie sessions
instead (no file or DB session rows to delete); production settings are
unchanged.
"""
from .settings import *  # noqa: F401,F403

SESSION_ENGINE = 'django.contrib.sessions.backends.signed_cookies'
SESSION_FILE_PATH = None
