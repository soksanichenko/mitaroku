"""Django settings, driven by ``mitaroku.config``."""

import ipaddress
from pathlib import Path

from mitaroku.config import load_settings

config = load_settings()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config.secret_key.get_secret_value()
DEBUG = config.debug
ALLOWED_HOSTS = config.allowed_hosts
CSRF_TRUSTED_ORIGINS = config.csrf_trusted_origins
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_SECURE = not DEBUG

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'huey.contrib.djhuey',
    'accounts',
    'tracking',
    'catalog',
    'providers',
    'web',
]

AUTH_USER_MODEL = 'accounts.User'
AUTH_MODE = config.auth.mode
PORTAL_TRUSTED_NETWORKS = [
    ipaddress.ip_network(item, strict=False) for item in config.auth.trusted_proxies
]
PORTAL_LOGOUT_URL = config.auth.portal_logout_url
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = LOGIN_URL


def middleware_for(mode: str) -> list[str]:
    """Return the middleware stack for an authentication mode.

    Args:
        mode: ``portal`` or ``local``.

    Returns:
        The ordered middleware dotted paths.
    """
    auth_gate = (
        'accounts.middleware.PortalAuthMiddleware'
        if mode == 'portal'
        else 'django.contrib.auth.middleware.LoginRequiredMiddleware'
    )
    return [
        'django.middleware.security.SecurityMiddleware',
        'django.contrib.sessions.middleware.SessionMiddleware',
        'django.middleware.locale.LocaleMiddleware',
        'django.middleware.common.CommonMiddleware',
        'django.middleware.csrf.CsrfViewMiddleware',
        'django.contrib.auth.middleware.AuthenticationMiddleware',
        auth_gate,
        'accounts.middleware.ProfileLanguageMiddleware',
        'django.contrib.messages.middleware.MessageMiddleware',
        'django.middleware.clickjacking.XFrameOptionsMiddleware',
    ]


def backends_for(mode: str) -> list[str]:
    """Return the authentication backends for an authentication mode."""
    if mode == 'portal':
        return ['accounts.backends.PortalBackend']
    return ['django.contrib.auth.backends.ModelBackend']


MIDDLEWARE = middleware_for(AUTH_MODE)
AUTHENTICATION_BACKENDS = backends_for(AUTH_MODE)

ROOT_URLCONF = 'mitaroku.urls'
WSGI_APPLICATION = 'mitaroku.wsgi.application'
ASGI_APPLICATION = 'mitaroku.asgi.application'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'web.context_processors.shell',
            ],
        },
    },
]

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'HOST': config.database.host,
        'PORT': config.database.port,
        'NAME': config.database.name,
        'USER': config.database.user,
        'PASSWORD': config.database.password.get_secret_value(),
        # Fail fast so /healthz/ answers 503 instead of hanging.
        'OPTIONS': {'connect_timeout': 5},
    }
}

REDIS_HOST = config.redis.host
REDIS_PORT = config.redis.port
REDIS_DB = config.redis.db
REDIS_PASSWORD = (
    config.redis.password.get_secret_value() if config.redis.password else None
)

HUEY = {
    'huey_class': 'huey.RedisHuey',
    'name': 'mitaroku',
    'immediate': config.huey_immediate,
    'connection': {
        'host': REDIS_HOST,
        'port': REDIS_PORT,
        'db': REDIS_DB,
        'password': REDIS_PASSWORD,
    },
}

LANGUAGE_CODE = 'en'
LANGUAGES = [('en', 'English'), ('ru', 'Русский')]
LOCALE_PATHS = [BASE_DIR / 'locale']
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
# Inside the repository root (BASE_DIR is sources/); serving it is deferred.
STATIC_ROOT = BASE_DIR.parent / 'staticfiles'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
