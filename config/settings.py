"""
Django settings for the iTrend MCP Platform.

Every value that differs between environments (secrets, hosts, database,
debug mode) is read from environment variables, which are loaded from the
project's .env file during local development. See .env.example for the full
list of supported variables.
"""

import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env_str(name: str, default: str | None = None) -> str:
    """Return an environment variable, failing loudly if it is required and missing."""
    value = os.getenv(name, default)
    if value is None or value == "":
        if default is not None:
            return default
        raise ImproperlyConfigured(f"Environment variable {name} is required.")
    return value


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

SECRET_KEY = env_str("DJANGO_SECRET_KEY")

DEBUG = env_bool("DJANGO_DEBUG", default=False)

# True while "manage.py test" is running.
TESTING = len(sys.argv) > 1 and sys.argv[1] == "test"

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", default="localhost,127.0.0.1")

CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third party
    "rest_framework",
    "django_htmx",
    # Platform apps
    "frontend",  # pages, forms, templates and static files
    "backend",  # models, business logic (services) and the REST API
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "backend.services.audit.AuditContextMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # Templates are found automatically in each app's templates/ folder.
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "frontend.context_processors.sidebar",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"


# ---------------------------------------------------------------------------
# Database
#
# MySQL is the supported database. SQLite is available only as a local
# development fallback when MySQL is not available (DB_ENGINE=sqlite).
# ---------------------------------------------------------------------------

DB_ENGINE = env_str("DB_ENGINE", default="mysql").lower()

if DB_ENGINE == "mysql":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": env_str("DB_NAME"),
            "USER": env_str("DB_USER"),
            "PASSWORD": env_str("DB_PASSWORD"),
            "HOST": env_str("DB_HOST", default="localhost"),
            "PORT": env_str("DB_PORT", default="3306"),
            "CONN_MAX_AGE": int(env_str("DB_CONN_MAX_AGE", default="60")),
            "CONN_HEALTH_CHECKS": True,
            "OPTIONS": {
                # utf8mb4 stores any Unicode character, including emoji.
                "charset": "utf8mb4",
                # Strict mode makes MySQL reject bad data instead of silently truncating it.
                "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
                "connect_timeout": 5,
            },
        }
    }
elif DB_ENGINE == "sqlite":
    if not DEBUG:
        raise ImproperlyConfigured("SQLite is only allowed for local development (DJANGO_DEBUG=True).")
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
    raise ImproperlyConfigured("DB_ENGINE must be 'mysql' or 'sqlite'.")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ---------------------------------------------------------------------------
# MCP platform
# ---------------------------------------------------------------------------

# Public address of the MCP gateway (this website's /mcp/ path). Each MCP is
# reachable at <base>/<slug>/. In production: https://mcp.itrendsolution.com/mcp
MCP_PUBLIC_BASE_URL = env_str("MCP_PUBLIC_BASE_URL", default="http://127.0.0.1:8000/mcp")

# Shared secret that MCP servers send when asking the platform whether a token
# is valid (POST /api/v1/tokens/introspect/). At least 32 characters. Leave it
# empty to switch token checking off; MCP servers then refuse every request.
MCP_INTROSPECTION_KEY = os.getenv("MCP_INTROSPECTION_KEY", "")
if MCP_INTROSPECTION_KEY and len(MCP_INTROSPECTION_KEY) < 32:
    raise ImproperlyConfigured("MCP_INTROSPECTION_KEY must be at least 32 characters.")

# Your own MCPs: one folder each, containing an mcp.json file (see mcp_servers/README.md).
MCP_SERVERS_DIR = Path(env_str("MCP_SERVERS_DIR", default=str(BASE_DIR / "mcp_servers")))
# Output (stdout/stderr) of the MCPs the platform starts, one file per MCP.
MCP_LOG_DIR = Path(env_str("MCP_LOG_DIR", default=str(BASE_DIR / "var" / "mcp_logs")))
# Look for new or changed mcp.json files at most this often (seconds). 0 = only via
# "manage.py sync_mcps" or the Rescan button.
MCP_DISCOVERY_INTERVAL = int(env_str("MCP_DISCOVERY_INTERVAL", default="0" if TESTING else "30"))


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

if TESTING:
    # Deliberately slow production hashing makes every test login take seconds.
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

LOGIN_URL = "frontend:login"
LOGIN_REDIRECT_URL = "frontend:dashboard"
LOGOUT_REDIRECT_URL = "frontend:login"

# Failed-login lockout (see backend/services/login_protection.py).
LOGIN_MAX_FAILED_ATTEMPTS = int(env_str("LOGIN_MAX_FAILED_ATTEMPTS", default="5"))
LOGIN_LOCKOUT_SECONDS = int(env_str("LOGIN_LOCKOUT_SECONDS", default="900"))


# ---------------------------------------------------------------------------
# Sessions, cookies and HTTP security
# ---------------------------------------------------------------------------

SESSION_COOKIE_AGE = int(env_str("SESSION_COOKIE_AGE", default=str(8 * 60 * 60)))
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

# Production defaults. Nginx terminates HTTPS and forwards X-Forwarded-Proto.
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", default=not DEBUG)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = int(env_str("DJANGO_SECURE_HSTS_SECONDS", default="0" if DEBUG else "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
# Browser preload lists are very hard to leave again, so this is an explicit opt-in.
SECURE_HSTS_PRELOAD = env_bool("DJANGO_SECURE_HSTS_PRELOAD", default=False)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"


# ---------------------------------------------------------------------------
# Caching (used by login lockout and API throttling)
#
# Local memory is per-process. Phase 7 switches this to a shared cache so
# limits apply across all worker processes.
# ---------------------------------------------------------------------------

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "itrend-mcp",
    }
}


# ---------------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    "EXCEPTION_HANDLER": "backend.exceptions.api_exception_handler",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": env_str("API_THROTTLE_ANON", default="60/min"),
        "user": env_str("API_THROTTLE_USER", default="600/min"),
    },
}

if DEBUG:
    # The browsable API is convenient while developing, never in production.
    REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"].append("rest_framework.renderers.BrowsableAPIRenderer")


# ---------------------------------------------------------------------------
# Internationalization
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = env_str("DJANGO_TIME_ZONE", default="UTC")
USE_I18N = True
USE_TZ = True


# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------

# Static files are found automatically in each app's static/ folder.
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"


# ---------------------------------------------------------------------------
# Logging
#
# Technical details go to server-side logs, never to end users.
# ---------------------------------------------------------------------------

LOG_DIR = BASE_DIR / "logs"
# Tests only show warnings and errors, so their output stays readable.
LOG_LEVEL = env_str("DJANGO_LOG_LEVEL", default="WARNING" if TESTING else "INFO")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "{asctime} {levelname} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "backend": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "frontend": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
    },
}

if env_bool("DJANGO_LOG_TO_FILE", default=False):
    LOG_DIR.mkdir(exist_ok=True)
    LOGGING["handlers"]["file"] = {
        "class": "logging.handlers.RotatingFileHandler",
        "filename": LOG_DIR / "platform.log",
        "maxBytes": 10 * 1024 * 1024,
        "backupCount": 5,
        "formatter": "standard",
    }
    for logger in ("django", "backend", "frontend"):
        LOGGING["loggers"][logger]["handlers"].append("file")
    LOGGING["root"]["handlers"].append("file")
