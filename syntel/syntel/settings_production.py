"""
Django settings for syntel project - PRODUCTION CONFIGURATION

This file contains production-ready settings for the Syntel Finance Management System.
Copy this to settings.py for production deployment.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/topics/settings/
"""

import os
from pathlib import Path

# لود کردن فایل متغیرهای محیطی
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent

# Source workbooks are reference inputs, not writable runtime data.
MENU_WORKBOOK_PATH = Path(os.environ.get('MENU_WORKBOOK_PATH', str(PROJECT_ROOT / 'reference' / 'menu' / 'منو کوکر.xlsx')))

# =============================================================================
# DATA PATH — all writable data (database, uploads, logs, cache, temp files,
# sessions, backups) is centralized under a single base directory.
# Default: /app/data — override per environment via the DATA_PATH env var.
# =============================================================================
DATA_PATH = Path(os.environ.get('DATA_PATH', '/app/data')).resolve()

DATA_SUBDIRS = {
    'logs': DATA_PATH / 'logs',
    'db': DATA_PATH / 'db',
    'uploads': DATA_PATH / 'uploads',
    'cache': DATA_PATH / 'cache',
    'tmp': DATA_PATH / 'tmp',
    'sessions': DATA_PATH / 'sessions',
    'backups': DATA_PATH / 'backups',
}
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
# Optional: upload backups into a specific Drive folder instead of root
GOOGLE_DRIVE_FOLDER_ID = os.environ.get('GOOGLE_DRIVE_FOLDER_ID', '')


def ensure_data_dirs():
    """Create every writable data directory at startup (idempotent)."""
    for path in DATA_SUBDIRS.values():
        path.mkdir(parents=True, exist_ok=True)


ensure_data_dirs()

# SECURITY SETTINGS - PRODUCTION HARDENED
# ======================================

# Generate a new secret key for production
# Use: python -c "import secrets; print(secrets.token_urlsafe(50))"
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'CHANGE_THIS_IN_PRODUCTION')

# DEBUG must be False in production
DEBUG = os.environ.get('DJANGO_DEBUG', 'False') == 'True'

# ALLOWED_HOSTS - Configure for your domain
ALLOWED_HOSTS = os.environ.get('DJANGO_ALLOWED_HOSTS', '').split(',') if os.environ.get('DJANGO_ALLOWED_HOSTS') else [
    'syntel.liara.run',
    'localhost',
    '127.0.0.1',
    '*',  # Remove this in production for better security
]

# CSRF Settings
CSRF_TRUSTED_ORIGINS = [
    'https://syntel.liara.run',
    'http://syntel.liara.run',
    'http://45.159.150.199',
    'http://localhost',
    'http://127.0.0.1',
]

# HTTPS Security Settings (Disabled for HTTP deployment)
# Enable these when you have SSL certificates:
# SECURE_SSL_REDIRECT = True
# SECURE_HSTS_SECONDS = 31536000  # 1 year
# SECURE_HSTS_INCLUDE_SUBDOMAINS = True
# SECURE_HSTS_PRELOAD = True
SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = 0

# Proxy / Liara settings - Trust X-Forwarded-Proto header from Liara's reverse proxy
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True

# Cookie Security (Disabled for HTTP deployment)
# SESSION_COOKIE_SECURE = True
# CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False

# Additional Security Headers
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = 'DENY'

# Security Middleware
SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'

# APPLICATION DEFINITION
# ======================

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
    'drf_spectacular',
]

AUTH_USER_MODEL = 'purchases.User'

# Django REST Framework
REST_FRAMEWORK = {
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
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
    'whitenoise.middleware.WhiteNoiseMiddleware',  # Serve /static/ in the container (no nginx)
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

# DATABASE CONFIGURATION
# ======================

# Production database configuration
# Falls back to SQLite if PostgreSQL credentials are not provided
if os.environ.get('DB_HOST'):
    # PostgreSQL Database (Production)
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.environ.get('DB_NAME', 'syntel_prod'),
            'USER': os.environ.get('DB_USER', 'syntel_user'),
            'PASSWORD': os.environ.get('DB_PASSWORD', ''),
            'HOST': os.environ.get('DB_HOST', 'localhost'),
            'PORT': os.environ.get('DB_PORT', '5432'),
            'OPTIONS': {
                # 'sslmode': 'require',  # Enforce SSL for database connections (disabled for Docker)
            },
            'CONN_MAX_AGE': 60,  # Connection pooling
        }
    }
else:
    # SQLite Database (Fallback)
    # Stored under DATA_PATH/db (legacy DATABASE_PATH env override still wins)
    db_path = os.environ.get('DATABASE_PATH', str(DB_DIR / 'db.sqlite3'))
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': db_path,
        }
    }

# PASSWORD VALIDATION
# ===================

AUTH_PASSWORD_VALIDATORS = []

# INTERNATIONALIZATION
# ====================

LANGUAGE_CODE = 'fa-ir'
TIME_ZONE = 'Asia/Tehran'
USE_I18N = True
USE_TZ = True

# STATIC FILES CONFIGURATION
# ==========================

STATIC_URL = '/static/'
STATIC_ROOT = os.environ.get('STATIC_ROOT', str(BASE_DIR / 'staticfiles'))
STATICFILES_DIRS = [
    BASE_DIR / 'frontend' / 'static',
]

# Media files — stored under DATA_PATH/uploads (legacy MEDIA_ROOT env override still wins)
MEDIA_URL = '/media/'
MEDIA_ROOT = os.environ.get('MEDIA_ROOT', str(UPLOADS_DIR))

# AUTHENTICATION SETTINGS
# =======================

LOGIN_URL = '/login/'
LOGOUT_REDIRECT_URL = '/login/'

# DEFAULT AUTO FIELD
# ==================

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# EMAIL CONFIGURATION
# ===================

EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', 587))
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'noreply@syntel.com')

# LOGGING CONFIGURATION
# ====================
# Logs to console (Docker/cloud) AND to a rotating file under DATA_PATH/logs

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
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'django_file': {
            'level': 'INFO',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': str(LOGS_DIR / 'django.log'),
            'maxBytes': 5 * 1024 * 1024,
            'backupCount': 3,
            'formatter': 'verbose',
            'encoding': 'utf-8',
        },
    },
    'root': {
        'handlers': ['console', 'django_file'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console', 'django_file'],
            'level': 'INFO',
            'propagate': False,
        },
        'purchases': {
            'handlers': ['console', 'django_file'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

# CUSTOM SETTINGS
# ===============

# Cache configuration — file-based, stored under DATA_PATH/cache
# (Redis can be added later if needed)
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache',
        'LOCATION': str(CACHE_DIR),
    }
}

# Session configuration
# Sessions stored as files under DATA_PATH/sessions.
# NOTE: this requires a shared persistent disk for DATA_PATH across workers.
SESSION_ENGINE = 'django.contrib.sessions.backends.file'
SESSION_FILE_PATH = str(SESSIONS_DIR)
SESSION_COOKIE_AGE = 86400  # 24 hours
SESSION_SAVE_EVERY_REQUEST = True

# File upload settings
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024  # 10MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024  # 10MB
FILE_UPLOAD_TEMP_DIR = str(TMP_DIR)  # Large uploads spill to DATA_PATH/tmp

# Admin URL customization (change from default /admin/)
# from django.urls import reverse_lazy
# ADMIN_URL = 'admin-panel/'

# Maintenance mode (custom middleware needed)
# MAINTENANCE_MODE = False