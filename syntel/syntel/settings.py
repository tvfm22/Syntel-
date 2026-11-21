"""
Django settings for syntel project.

Unified environment-driven configuration shared by local development and
production (`syntel.settings_production` imports these defaults).
"""

import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.db.backends.signals import connection_created

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent

_ENV_FILE = PROJECT_ROOT / '.env'
try:
    from dotenv import load_dotenv
    if _ENV_FILE.is_file():
        load_dotenv(_ENV_FILE)
    else:
        load_dotenv()
except ImportError:
    if _ENV_FILE.is_file():
        for _line in _ENV_FILE.read_text(encoding='utf-8').splitlines():
            _line = _line.strip()
            if not _line or _line.startswith('#') or '=' not in _line:
                continue
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# Source workbooks are reference inputs, not writable runtime data.
MENU_WORKBOOK_PATH = Path(
    os.environ.get('MENU_WORKBOOK_PATH', str(PROJECT_ROOT / 'reference' / 'menu' / 'منو کوکر.xlsx'))
)

# =============================================================================
# DATA PATH — all writable data (database, uploads, logs, cache, temp files,
# sessions, backups) is centralized under a single base directory.
# =============================================================================
from syntel.data_paths import (
    build_data_subdirs,
    ensure_data_dirs as _ensure_data_dirs,
    resolve_data_path,
)

DATA_PATH = resolve_data_path(PROJECT_ROOT)
DATA_SUBDIRS = build_data_subdirs(DATA_PATH)
LOGS_DIR = DATA_SUBDIRS['logs']
DB_DIR = DATA_SUBDIRS['db']
UPLOADS_DIR = DATA_SUBDIRS['uploads']
CACHE_DIR = DATA_SUBDIRS['cache']
TMP_DIR = DATA_SUBDIRS['tmp']
SESSIONS_DIR = DATA_SUBDIRS['sessions']
BACKUPS_DIR = DATA_SUBDIRS['backups']

# Google Drive backup (پشتیبان‌گیری گوگل‌درایو) — configured via env vars
GOOGLE_DRIVE_CLIENT_ID = os.environ.get('GOOGLE_DRIVE_CLIENT_ID', '')
GOOGLE_DRIVE_CLIENT_SECRET = os.environ.get('GOOGLE_DRIVE_CLIENT_SECRET', '')
GOOGLE_DRIVE_FOLDER_ID = os.environ.get('GOOGLE_DRIVE_FOLDER_ID', '')


def ensure_data_dirs():
    """Create every writable data directory at startup (idempotent)."""
    _ensure_data_dirs(DATA_SUBDIRS, PROJECT_ROOT, DATA_PATH)


ensure_data_dirs()

# =============================================================================
# Core Security Settings
# =============================================================================
DEBUG = os.environ.get('DJANGO_DEBUG', 'False').strip().lower() in ('true', '1', 'yes')

_INSECURE_SECRET_PLACEHOLDERS = {
    '',
    'CHANGE_THIS_IN_PRODUCTION',
    'change-me-to-a-long-random-value',
    'django-insecure-syntel-default-change-in-production',
}
_raw_secret_key = (
    os.environ.get('DJANGO_SECRET_KEY')
    or os.environ.get('SECRET_KEY')
    or ''
).strip()

if not DEBUG and (_raw_secret_key in _INSECURE_SECRET_PLACEHOLDERS or _raw_secret_key.startswith('django-insecure-')):
    raise ImproperlyConfigured(
        'DJANGO_SECRET_KEY must be set to a strong non-default secret when DEBUG is False.'
    )

SECRET_KEY = _raw_secret_key or 'django-insecure-local-dev-only-syntel'

_env_allowed_hosts = [
    h.strip() for h in os.environ.get('DJANGO_ALLOWED_HOSTS', '').split(',') if h.strip()
]
if _env_allowed_hosts:
    ALLOWED_HOSTS = _env_allowed_hosts
elif DEBUG:
    ALLOWED_HOSTS = ['localhost', '127.0.0.1', '[::1]']
else:
    ALLOWED_HOSTS = ['localhost', '127.0.0.1']

# Reverse proxy SSL support (for PaaS deployments behind HTTPS reverse proxies)
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True

# HTTPS / Cookie security — enabled when DEBUG=False unless DISABLE_HTTPS_SECURITY=1
_disable_https_security = os.environ.get('DISABLE_HTTPS_SECURITY', '').strip().lower() in ('1', 'true', 'yes')
_https_security_enabled = (not DEBUG) and (not _disable_https_security)

SECURE_SSL_REDIRECT = _https_security_enabled
SECURE_HSTS_SECONDS = 31536000 if _https_security_enabled else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = _https_security_enabled
SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'

# CSRF Settings
CSRF_COOKIE_SECURE = _https_security_enabled
CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SAMESITE = 'Lax'

CSRF_TRUSTED_ORIGINS = [
    'http://localhost',
    'http://127.0.0.1',
    'http://localhost:8000',
    'http://127.0.0.1:8000',
    'https://localhost',
    'https://127.0.0.1',
    'https://localhost:8000',
    'https://127.0.0.1:8000',
]
_extra_csrf_origins = [
    o.strip() for o in os.environ.get('CSRF_TRUSTED_ORIGINS', '').split(',') if o.strip()
]
if _extra_csrf_origins:
    CSRF_TRUSTED_ORIGINS.extend(_extra_csrf_origins)

# Session Settings
SESSION_COOKIE_SECURE = _https_security_enabled
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_AGE = int(os.environ.get('SESSION_COOKIE_AGE', 86400))
SESSION_SAVE_EVERY_REQUEST = True

REDIS_URL = (os.environ.get('REDIS_URL') or '').strip()
if REDIS_URL:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': REDIS_URL,
        }
    }
    SESSION_ENGINE = 'django.contrib.sessions.backends.cached_db'
    SESSION_FILE_PATH = None
elif os.environ.get('USE_DB_SESSIONS', '').strip().lower() in ('1', 'true', 'yes'):
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache',
            'LOCATION': str(CACHE_DIR),
        }
    }
    SESSION_ENGINE = 'django.contrib.sessions.backends.db'
    SESSION_FILE_PATH = None
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache',
            'LOCATION': str(CACHE_DIR),
        }
    }
    SESSION_ENGINE = 'django.contrib.sessions.backends.file'
    SESSION_FILE_PATH = str(SESSIONS_DIR)


# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'purchases',
    'rest_framework',
    'rest_framework.authtoken',
    'drf_spectacular',
]

AUTH_USER_MODEL = 'purchases.User'

AUTHENTICATION_BACKENDS = [
    'purchases.auth_backends.CaseInsensitiveModelBackend',
]

# Django REST Framework
REST_FRAMEWORK = {
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.TokenAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': os.environ.get('DRF_THROTTLE_ANON', '30/min'),
        'user': os.environ.get('DRF_THROTTLE_USER', '300/min'),
    },
}

# drf-spectacular settings
SPECTACULAR_SETTINGS = {
    'TITLE': 'Syntel API',
    'DESCRIPTION': 'API documentation for Syntel Finance Management System',
    'VERSION': 'v1',
    'CONTACT': {'email': 'support@syntel.com'},
    'LICENSE': {'name': 'MIT License'},
}

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'purchases.middleware.CafeMiddleware',
    'purchases.middleware.CafeContextMiddleware',
]

ROOT_URLCONF = 'syntel.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'frontend' / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'purchases.context_processors.cafe_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'syntel.wsgi.application'


# Database
if os.environ.get('DB_HOST'):
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.environ.get('DB_NAME', 'syntel_prod'),
            'USER': os.environ.get('DB_USER', 'syntel_user'),
            'PASSWORD': os.environ.get('DB_PASSWORD', ''),
            'HOST': os.environ.get('DB_HOST', 'localhost'),
            'PORT': os.environ.get('DB_PORT', '5432'),
            'CONN_MAX_AGE': 60,
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': os.environ.get('DATABASE_PATH', str(DB_DIR / 'db.sqlite3')),
            'OPTIONS': {
                'timeout': 30,
            },
        }
    }


def _configure_sqlite_pragmas(sender, connection, **kwargs):
    if connection.vendor == 'sqlite':
        with connection.cursor() as cursor:
            cursor.execute('PRAGMA journal_mode=WAL;')
            cursor.execute('PRAGMA synchronous=NORMAL;')
            cursor.execute('PRAGMA busy_timeout=30000;')
            cursor.execute('PRAGMA foreign_keys=ON;')


connection_created.connect(_configure_sqlite_pragmas, dispatch_uid='syntel_sqlite_pragmas')


# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {'min_length': 8},
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
LANGUAGE_CODE = 'fa-ir'
TIME_ZONE = 'Asia/Tehran'
USE_I18N = True
USE_TZ = True


# Static and Media files
STATICFILES_DIRS = [BASE_DIR / 'frontend' / 'static']
STATIC_URL = '/static/'
STATIC_ROOT = os.environ.get('STATIC_ROOT', str(BASE_DIR / 'staticfiles'))

_is_test_run = 'test' in sys.argv or 'pytest' in sys.modules
STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': (
            'django.contrib.staticfiles.storage.StaticFilesStorage'
            if (DEBUG or _is_test_run)
            else 'whitenoise.storage.CompressedManifestStaticFilesStorage'
        ),
    },
}
WHITENOISE_MANIFEST_STRICT = False

MEDIA_URL = '/media/'
MEDIA_ROOT = os.environ.get('MEDIA_ROOT', str(UPLOADS_DIR))

FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024  # 10MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024  # 10MB
FILE_UPLOAD_TEMP_DIR = str(TMP_DIR)

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Authentication settings
LOGIN_URL = '/login/'
LOGOUT_REDIRECT_URL = '/login/'

# Email configuration
EMAIL_BACKEND = os.environ.get('EMAIL_BACKEND', 'django.core.mail.backends.smtp.EmailBackend')
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', 587))
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'True').strip().lower() in ('true', '1', 'yes')
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'noreply@syntel.com')

# Logging — multi-process safe stream logging (stdout/stderr)
_LOG_LEVEL = os.environ.get('DJANGO_LOG_LEVEL', 'INFO').upper()
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'level': _LOG_LEVEL,
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': _LOG_LEVEL,
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': _LOG_LEVEL,
            'propagate': False,
        },
        'purchases': {
            'handlers': ['console'],
            'level': _LOG_LEVEL,
            'propagate': False,
        },
    },
}
