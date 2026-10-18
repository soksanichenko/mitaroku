# Plan 1: Foundation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the Django project that every later plan builds on: configuration, PostgreSQL, Redis and the Huey worker, portal and local authentication with the admin role rule, and the application shell (header, bottom navigation, theme, EN/RU) from the design mockup.

**Architecture:** A modular Django monolith in `sources/` with two apps for now: `accounts` (user, profile, portal authentication, language) and `web` (shell templates, static files, health endpoint, background tasks). Configuration is a Pydantic model that reads a YAML file and lets environment variables override it. In portal mode a small middleware logs users in from the headers nginx sets after the SSO check, trusting them only from configured proxy networks.

**Tech Stack:** Python 3.12, Django 5.2.18 (LTS), PostgreSQL 17, Redis 7, Huey 3.4.0, pydantic-settings 2.15.0, htmx 2.0.4, Alpine.js 3.14.9, pytest 9.1.1 with pytest-django and pytest-env, ruff 0.17.0.

**Spec:** `docs/superpowers/specs/2026-10-09-mitaroku-requirements-design.md` (sections 2, 9, 10, 11 and the shell part of 5). Design reference: `docs/design/`.

## Global Constraints

- Django 5.2 LTS, Python 3.12, PostgreSQL, Redis, Huey on Redis (spec sections 2 and 12).
- Configuration is YAML validated by Pydantic; environment variables override YAML values (common rules, spec section 2).
- Single quotes for Python strings (docstrings use triple double quotes); type annotations on public functions; Google-style docstrings; PEP 8 with 88 columns preferred.
- Log with `logging`, not `print()`. This plan adds no operator output, so no logger is needed yet.
- Everything committed (code, comments, commit messages, docs) is in English.
- Admin means `is_staff` plus the group `app-admins`; never `is_superuser` (spec section 9).
- Portal headers are trusted only from the configured proxy networks (spec section 9).
- CSRF protection must cover HTMX requests (spec section 10).
- UI is Russian and English; the interface language lives in the profile (spec section 11).
- Touch targets are at least 44 px and the design's red-on-text rule applies: buttons that carry text use `--color-accent-700` (design notes in `docs/design/`).
- Every YAML file ends with `...` and a blank line, except GitHub workflow files, which keep the template's existing style (no end marker).

## Decisions and deviations from the mockup

- **Tailwind is deferred.** The mockup is built on the design system's own CSS (`ds.css`, copied unchanged from `docs/design/_ds/`) plus inline styles, so this plan ports it with a small hand-written `app.css`. Tailwind joins when a page needs utilities the design system lacks.
- **Header scope.** The header holds the logo, Library, Upcoming, the theme toggle and the user menu. The search field, the notifications bell, the Profile and Search tabs and the "Profile & settings" and "Import / export" menu items arrive with the plans that create their pages.
- **Mobile.** The mockup moves the language switch into a Profile page on small screens. That page does not exist yet, so the user menu stays visible on every width until the settings page lands.
- **Fonts.** `ds.css` imports Archivo from Google Fonts. Self-hosting the font is a deployment-plan decision; without network access the page falls back to the system font.
- **`manage.py` needs `config.yaml`.** pytest gets its settings from `pyproject.toml`, but management commands read `config.yaml` from the working directory. Task 2 creates it from `config.example.yaml`.

## Review Focus

These are inputs the spec implies and a person running the service is most likely to hit. Each line names the test that pins it.

- Portal headers arriving from a peer outside the trusted networks must be refused and create no user: `test_headers_from_untrusted_peer_are_rejected` (Task 3).
- A missing, empty or non-numeric `X-Discord-User-Id` must not create a user or crash: `test_missing_header_is_rejected`, `test_malformed_user_id_is_rejected_without_creating_a_user` (Task 3).
- The portal changing or dropping the header mid-session must switch or end the session, never reuse the previous user's: `test_session_does_not_survive_header_removal`, `test_changing_the_header_switches_user_not_session` (Task 3).
- An over-long email or avatar URL from the portal must be ignored, not turn into a 500 on PostgreSQL: `test_overlong_email_and_avatar_are_ignored_not_fatal` (Task 3).
- A language switch with an off-site `next` or an unknown language must stay on the site and change nothing: `test_set_language_rejects_open_redirect`, `test_set_language_ignores_unknown_language` (Task 3).
- The health endpoint must report 503 naming the failing dependency when PostgreSQL or Redis is down: `test_healthz_reports_a_down_redis`, `test_healthz_reports_a_down_database` (Task 2).

## File Structure

| Path | Responsibility |
| --- | --- |
| `sources/mitaroku/config.py` | Pydantic settings: YAML plus environment |
| `sources/mitaroku/settings.py` | Django settings built from the config; `middleware_for` and `backends_for` per auth mode |
| `sources/mitaroku/urls.py`, `wsgi.py`, `asgi.py` | Entry points |
| `sources/accounts/models.py` | `User` (Discord id, avatar) and `Profile` (languages) |
| `sources/accounts/portal.py` | Header helpers: trusted peer check, id validation, role sync |
| `sources/accounts/backends.py`, `middleware.py` | Portal login and per-user language |
| `sources/accounts/views.py`, `urls.py` | Language switch, local login and logout |
| `sources/web/views.py`, `urls.py` | Library and Upcoming pages, health endpoint |
| `sources/web/tasks.py` | The `ping` Huey task that proves the worker pipeline |
| `sources/web/context_processors.py`, `templatetags/web_extras.py` | Shell template data and the `initials` filter |
| `sources/web/templates/web/`, `sources/web/static/web/` | Base layout, header, bottom nav, CSS, theme script, vendored htmx and Alpine |
| `sources/locale/ru/LC_MESSAGES/django.po` | Russian translations |
| `tests/` | One test module per concern |
| `docker-compose.dev.yml`, `config.example.yaml` | Development dependencies and sample config |

All commands run from the repository root with the virtualenv active (`source .venv/bin/activate`, created in Task 1).

---

### Task 1: Tooling and configuration

**Files:**
- Create: `pyproject.toml` (replaces the template's), `sources/requirements.txt`, `tests/requirements.txt`, `sources/mitaroku/__init__.py`, `sources/mitaroku/config.py`, `config.example.yaml`, `tests/test_config.py`
- Modify: `requirements.txt`, `.gitignore`

**Interfaces:**
- Produces: `mitaroku.config.load_settings() -> Settings` (cached). `Settings` has `secret_key: SecretStr`, `debug: bool`, `allowed_hosts: list[str]`, `csrf_trusted_origins: list[str]`, `huey_immediate: bool`, `auth: AuthConfig` (`mode: 'portal' | 'local'`, `trusted_proxies: list[str]`, `portal_logout_url: str`), `database: DatabaseConfig` (`host`, `port`, `name`, `user`, `password: SecretStr`), `redis: RedisConfig` (`host`, `port`, `db`, `password: SecretStr | None`). Environment variables use the prefix `MITAROKU_` with `__` between nested keys; the YAML path comes from `MITAROKU_CONFIG` (default `config.yaml`) and a missing file is ignored.

- [ ] **Step 1: Create the packaging and test-runner files**

Replace the template's placeholders and add the pinned dependencies. The `env` list gives pytest the settings it needs; the `D:` prefix means "only if not already set", so CI and developers can override.

**Replace the whole file `pyproject.toml`:**

```toml
[project]
name = "mitaroku"
version = "0.1.0"
description = "Self-hosted tracker for movies, TV series and anime"
requires-python = ">=3.12"
dependencies = []

[tool.setuptools.packages.find]
where = ["sources"]

[tool.ruff.lint]
select = ["E4", "E7", "E9", "F", "I"]

[tool.ruff.format]
quote-style = "single"

[tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = "mitaroku.settings"
pythonpath = ["sources"]
testpaths = ["tests"]
env = [
  "D:MITAROKU_CONFIG=tests/no-such-config.yaml",
  "D:MITAROKU_SECRET_KEY=test-secret-key-not-for-production",
  "D:MITAROKU_DATABASE__PASSWORD=mitaroku",
  "D:MITAROKU_DATABASE__PORT=55432",
  "D:MITAROKU_REDIS__PORT=56379",
  "D:MITAROKU_HUEY_IMMEDIATE=true",
  "D:MITAROKU_AUTH__MODE=portal",
]
```

**Create `sources/requirements.txt`:**

```text
Django==5.2.18
huey==3.4.0
psycopg[binary]==3.3.6
pydantic==2.14.0
pydantic-settings==2.15.0
PyYAML==6.0.3
redis==8.1.0
```

**Create `tests/requirements.txt`:**

```text
-r ../sources/requirements.txt
pytest==9.1.1
pytest-django==4.14.0
pytest-env==1.7.1
```

**Replace the whole file `requirements.txt`:**

```text
-r sources/requirements.txt
-r tests/requirements.txt
infisicalsdk==1.0.17
pre-commit==4.6.2
yamllint==1.38.0
ansible-lint==26.9.0
ruff==0.17.0
```

Append to `.gitignore`:

```text
config.yaml
*.mo
.pytest_cache/
.ruff_cache/
```

Create the virtualenv and install everything:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

- [ ] **Step 2: Write the failing configuration tests**

**Create `tests/test_config.py`:**

```python
import pytest
from mitaroku.config import Settings
from pydantic import ValidationError


def test_yaml_values_are_loaded(tmp_path, monkeypatch):
    config_file = tmp_path / 'config.yaml'
    config_file.write_text(
        'secret_key: from-yaml\n'
        'debug: true\n'
        'database:\n  password: pw\n  host: db.internal\n'
    )
    monkeypatch.setenv('MITAROKU_CONFIG', str(config_file))
    for name in ('MITAROKU_SECRET_KEY', 'MITAROKU_DATABASE__PASSWORD'):
        monkeypatch.delenv(name)
    settings = Settings()
    assert settings.secret_key.get_secret_value() == 'from-yaml'
    assert settings.debug is True
    assert settings.database.host == 'db.internal'


def test_environment_overrides_yaml(tmp_path, monkeypatch):
    config_file = tmp_path / 'config.yaml'
    config_file.write_text(
        'secret_key: from-yaml\ndatabase:\n  password: pw\n  host: yaml-host\n'
    )
    monkeypatch.setenv('MITAROKU_CONFIG', str(config_file))
    monkeypatch.setenv('MITAROKU_SECRET_KEY', 'from-env')
    monkeypatch.setenv('MITAROKU_DATABASE__HOST', 'env-host')
    settings = Settings()
    assert settings.secret_key.get_secret_value() == 'from-env'
    assert settings.database.host == 'env-host'


def test_missing_required_value_is_reported(monkeypatch):
    monkeypatch.delenv('MITAROKU_SECRET_KEY')
    with pytest.raises(ValidationError) as error:
        Settings()
    assert 'secret_key' in str(error.value)


def test_invalid_trusted_proxy_is_rejected(monkeypatch):
    monkeypatch.setenv('MITAROKU_AUTH__TRUSTED_PROXIES', '["not-a-network"]')
    with pytest.raises(ValidationError):
        Settings()


def test_unknown_key_is_rejected(tmp_path, monkeypatch):
    config_file = tmp_path / 'config.yaml'
    config_file.write_text('secret_keyy: typo\n')
    monkeypatch.setenv('MITAROKU_CONFIG', str(config_file))
    with pytest.raises(ValidationError):
        Settings()
```

- [ ] **Step 3: Run them and watch them fail**

Run: `python -m pytest -p no:django tests/test_config.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'mitaroku'`. (`-p no:django` is used until Task 2 creates the Django project; these tests do not need Django.)

- [ ] **Step 4: Implement the configuration**

**Create the empty file `sources/mitaroku/__init__.py`.**

**Create `sources/mitaroku/config.py`:**

```python
"""Application configuration: a YAML file overridden by environment variables."""

import ipaddress
import os
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, SecretStr, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)


class DatabaseConfig(BaseModel):
    """PostgreSQL connection settings."""

    host: str = 'localhost'
    port: int = 5432
    name: str = 'mitaroku'
    user: str = 'mitaroku'
    password: SecretStr


class RedisConfig(BaseModel):
    """Redis connection settings (Huey broker and provider cache)."""

    host: str = 'localhost'
    port: int = 6379
    db: int = 0
    password: SecretStr | None = None


class AuthConfig(BaseModel):
    """How users are authenticated.

    Attributes:
        mode: ``portal`` trusts the SSO headers set by nginx; ``local`` uses
            Django accounts.
        trusted_proxies: Networks that may send the portal headers.
        portal_logout_url: Where the "Log out" menu item points in portal mode.
            Empty hides the item.
    """

    mode: Literal['portal', 'local'] = 'portal'
    trusted_proxies: list[str] = ['127.0.0.1/32', '::1/128', '172.16.0.0/12']
    portal_logout_url: str = ''

    @field_validator('trusted_proxies')
    @classmethod
    def _valid_networks(cls, value: list[str]) -> list[str]:
        """Reject entries that are not IP networks."""
        for item in value:
            ipaddress.ip_network(item, strict=False)
        return value


class Settings(BaseSettings):
    """All runtime settings.

    Values come from, highest priority first: environment variables prefixed
    ``MITAROKU_`` (nested keys joined with ``__``), then the YAML file named by
    ``MITAROKU_CONFIG`` (default ``config.yaml``). A missing file is ignored.
    """

    model_config = SettingsConfigDict(
        env_prefix='MITAROKU_', env_nested_delimiter='__', extra='forbid'
    )

    secret_key: SecretStr
    debug: bool = False
    allowed_hosts: list[str] = ['localhost', '127.0.0.1']
    csrf_trusted_origins: list[str] = []
    huey_immediate: bool = False
    auth: AuthConfig = AuthConfig()
    database: DatabaseConfig
    redis: RedisConfig = RedisConfig()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Put the YAML file below the environment in priority."""
        yaml_source = YamlConfigSettingsSource(
            settings_cls, yaml_file=os.environ.get('MITAROKU_CONFIG', 'config.yaml')
        )
        return init_settings, env_settings, yaml_source


@lru_cache
def load_settings() -> Settings:
    """Load and cache the settings for the process."""
    return Settings()
```

**Create `config.example.yaml`:**

```yaml
---
# Copy to config.yaml (git-ignored) for local development.
# Every key can also be set through the environment, for example
# MITAROKU_DATABASE__PASSWORD; the environment wins over this file.
secret_key: change-me
debug: true
allowed_hosts: [localhost, 127.0.0.1]
huey_immediate: false

auth:
  mode: local  # local = Django accounts; portal = trust the SSO headers from nginx
  trusted_proxies: ['127.0.0.1/32', '::1/128', '172.16.0.0/12']
  portal_logout_url: ''

database:
  host: localhost
  port: 55432
  name: mitaroku
  user: mitaroku
  password: mitaroku

redis:
  host: localhost
  port: 56379
...
```

- [ ] **Step 5: Run the tests and watch them pass**

Run: `python -m pytest -p no:django tests/test_config.py -q`
Expected: `5 passed` (plus one warning about the unknown `DJANGO_SETTINGS_MODULE` option, which disappears in Task 2).

- [ ] **Step 6: Lint and commit**

```bash
ruff check --fix sources tests && ruff format sources tests
git add pyproject.toml requirements.txt sources/requirements.txt tests/requirements.txt \
  .gitignore config.example.yaml sources/mitaroku tests/test_config.py
git commit -m "feat: add Pydantic configuration with YAML and environment sources"
```

---

### Task 2: Django project, accounts models, Redis and Huey

**Files:**
- Create: `sources/manage.py`, `sources/mitaroku/settings.py`, `sources/mitaroku/urls.py`, `sources/mitaroku/wsgi.py`, `sources/mitaroku/asgi.py`, `sources/accounts/{__init__,apps,models,signals,admin}.py`, `sources/accounts/migrations/__init__.py`, `sources/web/{__init__,apps,tasks,views,urls}.py`, `docker-compose.dev.yml`, `tests/test_models.py`, `tests/test_health_and_tasks.py`
- Generated: `sources/accounts/migrations/0001_initial.py`

**Interfaces:**
- Consumes: `load_settings()` from Task 1.
- Produces: Django settings `AUTH_MODE`, `PORTAL_TRUSTED_NETWORKS` (list of `ipaddress` networks), `PORTAL_LOGOUT_URL`, `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB`, `REDIS_PASSWORD`, `HUEY`, plus `middleware_for(mode: str) -> list[str]` and `backends_for(mode: str) -> list[str]` (the middleware stack already names `accounts.middleware` classes that Task 3 creates). `accounts.models.User` (`discord_id: str | None`, unique; `avatar_url: str`) and `Profile` (`user`, `interface_language`, `metadata_language`, both default `'en'`), created automatically for every new user. `web.tasks.ping() -> str` (a Huey `db_task`; in immediate mode `ping().get()` returns `'pong'`). `web.views.healthz` and the URL names `index`, `library`, `healthz`.

- [ ] **Step 1: Write the failing tests**

**Create `tests/test_models.py`:**

```python
import pytest
from accounts.models import Profile
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

pytestmark = pytest.mark.django_db


def test_saving_a_user_creates_a_profile_with_english_defaults():
    user = get_user_model().objects.create_user('kira')
    assert user.profile.interface_language == 'en'
    assert user.profile.metadata_language == 'en'


def test_saving_a_user_again_does_not_duplicate_the_profile():
    user = get_user_model().objects.create_user('kira')
    user.first_name = 'Kira'
    user.save()
    assert Profile.objects.filter(user=user).count() == 1


def test_discord_id_is_unique_but_optional():
    users = get_user_model().objects
    users.create_user('a')
    users.create_user('b')  # two users without a discord id are fine
    users.create_user('c', discord_id='7')
    with pytest.raises(IntegrityError), transaction.atomic():
        users.create_user('d', discord_id='7')
```

**Create `tests/test_health_and_tasks.py`:**

```python
import pytest
from django.test import RequestFactory
from web.tasks import ping
from web.views import healthz


def call_healthz():
    return healthz(RequestFactory().get('/healthz/'))


@pytest.mark.django_db
def test_healthz_reports_ok_when_dependencies_answer():
    response = call_healthz()
    assert response.status_code == 200
    assert response.content == b'{"status": "ok"}'


@pytest.mark.django_db
def test_healthz_reports_a_down_redis(settings):
    settings.REDIS_PORT = 1
    response = call_healthz()
    assert response.status_code == 503
    assert response.content == b'{"status": "error", "failing": ["redis"]}'


def test_healthz_reports_a_down_database(monkeypatch):
    def broken():
        raise RuntimeError('db down')

    monkeypatch.setattr('web.views.connection.ensure_connection', broken)
    response = call_healthz()
    assert response.status_code == 503
    assert response.content == b'{"status": "error", "failing": ["database"]}'


def test_ping_task_runs_through_huey():
    assert ping().get() == 'pong'
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python -m pytest -q`
Expected: an `ImportError` from pytest-django, because `mitaroku.settings` cannot be imported yet.

- [ ] **Step 3: Start PostgreSQL and Redis**

Create `docker-compose.dev.yml`, then start it. The ports are non-default (55432 and 56379) so they cannot clash with services already on the host, and they match the defaults in `pyproject.toml` and `config.example.yaml`.

**Create `docker-compose.dev.yml`:**

```yaml
---
# Development dependencies only. The ports are non-default so they do not
# clash with a PostgreSQL or Redis already running on the host.
services:
  postgres:
    image: postgres:17-alpine
    environment:
      POSTGRES_USER: mitaroku
      POSTGRES_PASSWORD: mitaroku
      POSTGRES_DB: mitaroku
    ports:
      - "127.0.0.1:55432:5432"
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U mitaroku"]
      interval: 5s
      timeout: 3s
      retries: 10

  redis:
    image: redis:7-alpine
    ports:
      - "127.0.0.1:56379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10

volumes:
  postgres-data:
...
```

```bash
docker compose -f docker-compose.dev.yml up -d --wait
```

Expected: both services report `healthy`.

- [ ] **Step 4: Implement the project**

**Create `sources/manage.py`:**

```python
#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import os
import sys


def main() -> None:
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mitaroku.settings')
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
```

**Create `sources/mitaroku/settings.py`:**

```python
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
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
```

**Create `sources/mitaroku/urls.py`:**

```python
"""Root URL configuration."""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('web.urls')),
]
```

**Create `sources/mitaroku/wsgi.py`:**

```python
"""WSGI entry point."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mitaroku.settings')
application = get_wsgi_application()
```

**Create `sources/mitaroku/asgi.py`:**

```python
"""ASGI entry point."""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mitaroku.settings')
application = get_asgi_application()
```

**Create the empty file `sources/accounts/__init__.py`.**

**Create the empty file `sources/accounts/migrations/__init__.py`.**

**Create `sources/accounts/apps.py`:**

```python
"""Accounts app configuration."""

from django.apps import AppConfig


class AccountsConfig(AppConfig):
    """Register the accounts app and its signals."""

    name = 'accounts'

    def ready(self) -> None:
        """Connect the profile-creation signal."""
        from accounts import signals  # noqa: F401
```

**Create `sources/accounts/models.py`:**

```python
"""User and profile models."""

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """A mitaroku user.

    Attributes:
        discord_id: Discord user id supplied by the SSO portal; the stable key
            for portal users. Local accounts leave it empty.
        avatar_url: Avatar URL supplied by the SSO portal.
    """

    discord_id = models.CharField(max_length=32, unique=True, null=True, blank=True)
    avatar_url = models.URLField(blank=True)


class Profile(models.Model):
    """Per-user preferences."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile'
    )
    interface_language = models.CharField(
        max_length=5, choices=settings.LANGUAGES, default='en'
    )
    metadata_language = models.CharField(
        max_length=5, choices=settings.LANGUAGES, default='en'
    )

    def __str__(self) -> str:
        """Return a readable label for the admin."""
        return f'Profile of {self.user}'
```

**Create `sources/accounts/signals.py`:**

```python
"""Signals that keep related rows in step with users."""

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from accounts.models import Profile


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_profile(sender, instance, created, **kwargs) -> None:
    """Create a profile the first time a user is saved."""
    if created:
        Profile.objects.create(user=instance)
```

**Create `sources/accounts/admin.py`:**

```python
"""Admin registrations."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from accounts.models import Profile, User


@admin.register(User)
class MitarokuUserAdmin(UserAdmin):
    """User admin with the portal fields visible."""

    fieldsets = UserAdmin.fieldsets + (
        ('Portal', {'fields': ('discord_id', 'avatar_url')}),
    )
    list_display = ('username', 'email', 'discord_id', 'is_staff')


admin.site.register(Profile)
```

**Create the empty file `sources/web/__init__.py`.**

**Create `sources/web/apps.py`:**

```python
"""Web app configuration."""

from django.apps import AppConfig


class WebConfig(AppConfig):
    """Pages, the application shell and the health endpoint."""

    name = 'web'
```

**Create `sources/web/tasks.py`:**

```python
"""Background tasks."""

from huey.contrib.djhuey import db_task


@db_task()
def ping() -> str:
    """Return ``pong``; proves the worker pipeline end to end."""
    return 'pong'
```

**Create `sources/web/views.py`:**

```python
"""Page and health views."""

import redis
from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect


def index(request):
    """Send the root URL to the library."""
    return redirect('library')


def library(request):
    """Placeholder until the shell templates arrive."""
    return HttpResponse('Library')


@login_not_required
def healthz(request):
    """Report whether PostgreSQL and Redis answer.

    Returns:
        200 with ``{"status": "ok"}`` or 503 naming the failing dependency.
    """
    failing = []
    try:
        connection.ensure_connection()
    except Exception:
        failing.append('database')
    try:
        redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            password=settings.REDIS_PASSWORD,
            socket_connect_timeout=2,
        ).ping()
    except Exception:
        failing.append('redis')
    if failing:
        return JsonResponse({'status': 'error', 'failing': failing}, status=503)
    return JsonResponse({'status': 'ok'})
```

**Create `sources/web/urls.py`:**

```python
"""Web URLs."""

from django.urls import path

from web import views

urlpatterns = [
    path('', views.index, name='index'),
    path('library/', views.library, name='library'),
    path('healthz/', views.healthz, name='healthz'),
]
```

- [ ] **Step 5: Create the local config, generate the migration, format, check**

```bash
cp config.example.yaml config.yaml
python sources/manage.py makemigrations accounts
ruff check --fix sources tests && ruff format sources tests
python sources/manage.py check
```

Expected: `makemigrations` prints `Create model User` and `Create model Profile`; `check` prints `System check identified no issues (0 silenced).` (`config.yaml` is git-ignored; it is what management commands read, while pytest reads the `env` list in `pyproject.toml`.)

- [ ] **Step 6: Run the tests and watch them pass**

Run: `python -m pytest -q`
Expected: `12 passed` (5 configuration, 3 model, 4 health and task tests).

- [ ] **Step 7: Commit**

```bash
git add sources docker-compose.dev.yml tests
git commit -m "feat: add Django project with accounts models, Redis health check and Huey task"
```

---

### Task 3: Portal authentication, roles and language switch

**Files:**
- Create: `sources/accounts/portal.py`, `sources/accounts/backends.py`, `sources/accounts/middleware.py`, `sources/accounts/migrations/0002_app_admins_group.py`, `tests/conftest.py`, `tests/test_portal_auth.py`, `tests/test_language.py`
- Create: `sources/accounts/views.py`, `sources/accounts/urls.py` (Task 5 extends both)
- Replace: `sources/mitaroku/urls.py`

**Interfaces:**
- Consumes: `middleware_for`, `backends_for`, `PORTAL_TRUSTED_NETWORKS` from Task 2; `User` and `Profile` from Task 2.
- Produces:
  - `accounts.portal.is_trusted_peer(remote_addr: str, networks) -> bool`; `is_valid_discord_id(value: str) -> bool` (1 to 32 ASCII digits); `sync_user(user, *, email: str, avatar_url: str, access_level: str) -> None`; constant `ADMIN_GROUP = 'app-admins'`.
  - `accounts.backends.PortalBackend` (creates users keyed by Discord id, unusable password).
  - `accounts.middleware.PortalAuthMiddleware` (headers to session, 403 for untrusted peers, 400 for malformed ids, 401 for anonymous requests to views that require login) and `ProfileLanguageMiddleware` (activates `request.user.profile.interface_language`).
  - URL name `set_language`: POST `language` and `next`, saves the profile and the `django_language` cookie.
  - Test fixture `portal_headers` (a fresh dict of valid member headers).

- [ ] **Step 1: Write the failing tests**

**Create `tests/conftest.py`:**

```python
"""Shared fixtures."""

import pytest

PORTAL = {
    'HTTP_X_DISCORD_USER_ID': '1001',
    'HTTP_X_DISCORD_EMAIL': 'kira@example.com',
    'HTTP_X_DISCORD_AVATAR_URL': 'https://cdn.example.com/a.png',
    'HTTP_X_DISCORD_ACCESS_LEVEL': 'member',
}


@pytest.fixture
def portal_headers() -> dict[str, str]:
    """Return a fresh copy of valid portal headers for a member."""
    return dict(PORTAL)
```

**Create `tests/test_portal_auth.py`:**

```python
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

User = get_user_model()
pytestmark = pytest.mark.django_db


def test_first_request_creates_user_keyed_by_discord_id(client, portal_headers):
    response = client.get('/library/', **portal_headers)
    assert response.status_code == 200
    user = User.objects.get(discord_id='1001')
    assert user.username == '1001'
    assert user.email == 'kira@example.com'
    assert user.avatar_url == 'https://cdn.example.com/a.png'
    assert not user.has_usable_password()
    assert user.profile.interface_language == 'en'


def test_second_request_reuses_user_and_updates_email(client, portal_headers):
    client.get('/library/', **portal_headers)
    portal_headers['HTTP_X_DISCORD_EMAIL'] = 'new@example.com'
    client.get('/library/', **portal_headers)
    assert User.objects.count() == 1
    assert User.objects.get().email == 'new@example.com'


def test_admin_header_makes_staff_in_group_but_never_superuser(client, portal_headers):
    portal_headers['HTTP_X_DISCORD_ACCESS_LEVEL'] = 'admin'
    client.get('/library/', **portal_headers)
    user = User.objects.get(discord_id='1001')
    assert user.is_staff
    assert not user.is_superuser
    assert user.groups.filter(name='app-admins').exists()


def test_demotion_removes_staff_and_group(client, portal_headers):
    portal_headers['HTTP_X_DISCORD_ACCESS_LEVEL'] = 'admin'
    client.get('/library/', **portal_headers)
    portal_headers['HTTP_X_DISCORD_ACCESS_LEVEL'] = 'member'
    client.get('/library/', **portal_headers)
    user = User.objects.get(discord_id='1001')
    assert not user.is_staff
    assert not user.groups.filter(name='app-admins').exists()


def test_unknown_access_level_is_treated_as_member(client, portal_headers):
    portal_headers['HTTP_X_DISCORD_ACCESS_LEVEL'] = 'superuser'
    client.get('/library/', **portal_headers)
    assert not User.objects.get().is_staff


def test_missing_header_is_rejected(client):
    assert client.get('/library/').status_code == 401


def test_headers_from_untrusted_peer_are_rejected(client, portal_headers):
    response = client.get('/library/', REMOTE_ADDR='8.8.8.8', **portal_headers)
    assert response.status_code == 403
    assert not User.objects.exists()


def test_session_does_not_survive_header_removal(client, portal_headers):
    client.get('/library/', **portal_headers)
    assert client.get('/library/').status_code == 401


def test_changing_the_header_switches_user_not_session(client, portal_headers):
    client.get('/library/', **portal_headers)
    portal_headers['HTTP_X_DISCORD_USER_ID'] = '2002'
    client.get('/library/', **portal_headers)
    assert User.objects.filter(discord_id__in=['1001', '2002']).count() == 2
    response = client.get('/library/', **portal_headers)
    assert response.wsgi_request.user.discord_id == '2002'


def test_admin_group_is_created_by_migration():
    assert Group.objects.filter(name='app-admins').exists()


@pytest.mark.parametrize('bad_id', ['', 'admin', '12 34', '١٢٣', '1' * 33])
def test_malformed_user_id_is_rejected_without_creating_a_user(
    client, portal_headers, bad_id
):
    portal_headers['HTTP_X_DISCORD_USER_ID'] = bad_id
    response = client.get('/library/', **portal_headers)
    assert response.status_code == 400
    assert not User.objects.exists()


def test_overlong_email_and_avatar_are_ignored_not_fatal(client, portal_headers):
    portal_headers['HTTP_X_DISCORD_EMAIL'] = 'a' * 300 + '@example.com'
    portal_headers['HTTP_X_DISCORD_AVATAR_URL'] = 'https://x.example/' + 'a' * 300
    response = client.get('/library/', **portal_headers)
    assert response.status_code == 200
    user = User.objects.get(discord_id='1001')
    assert user.email == ''
    assert user.avatar_url == ''


def test_missing_optional_headers_leave_existing_values(client, portal_headers):
    client.get('/library/', **portal_headers)
    del portal_headers['HTTP_X_DISCORD_EMAIL']
    del portal_headers['HTTP_X_DISCORD_AVATAR_URL']
    client.get('/library/', **portal_headers)
    assert User.objects.get().email == 'kira@example.com'
```

**Create `tests/test_language.py`:**

```python
import pytest
from django.contrib.auth import get_user_model

User = get_user_model()
pytestmark = pytest.mark.django_db


def test_profile_language_is_active_for_the_request(client, portal_headers):
    client.get('/library/', **portal_headers)
    profile = User.objects.get(discord_id='1001').profile
    profile.interface_language = 'ru'
    profile.save()
    response = client.get('/library/', **portal_headers)
    assert response.wsgi_request.LANGUAGE_CODE == 'ru'
    assert response['Content-Language'] == 'ru'


def test_set_language_saves_profile_and_cookie(client, portal_headers):
    client.get('/library/', **portal_headers)
    response = client.post(
        '/language/', {'language': 'ru', 'next': '/upcoming/'}, **portal_headers
    )
    assert response.status_code == 302
    assert response['Location'] == '/upcoming/'
    assert response.cookies['django_language'].value == 'ru'
    assert User.objects.get(discord_id='1001').profile.interface_language == 'ru'


def test_set_language_ignores_unknown_language(client, portal_headers):
    client.get('/library/', **portal_headers)
    client.post('/language/', {'language': 'xx'}, **portal_headers)
    assert User.objects.get(discord_id='1001').profile.interface_language == 'en'


def test_set_language_rejects_open_redirect(client, portal_headers):
    client.get('/library/', **portal_headers)
    response = client.post(
        '/language/',
        {'language': 'ru', 'next': 'https://evil.example/'},
        **portal_headers,
    )
    assert response['Location'] == '/'


def test_set_language_requires_post(client, portal_headers):
    assert client.get('/language/', **portal_headers).status_code == 405
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python -m pytest -q`
Expected: `22 failed, 12 passed`; the failures show `ModuleNotFoundError: No module named 'accounts.middleware'`.

- [ ] **Step 3: Implement portal authentication and the language view**

**Create `sources/accounts/portal.py`:**

```python
"""Helpers for the SSO portal headers."""

import ipaddress

from django.contrib.auth.models import Group

ADMIN_GROUP = 'app-admins'
EMAIL_MAX = 254
AVATAR_MAX = 200


def is_trusted_peer(
    remote_addr: str,
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network],
) -> bool:
    """Say whether a peer address belongs to a trusted proxy network.

    Args:
        remote_addr: The ``REMOTE_ADDR`` of the request.
        networks: Trusted proxy networks.

    Returns:
        True when the address parses and lies in one of the networks.
    """
    try:
        address = ipaddress.ip_address(remote_addr)
    except ValueError:
        return False
    return any(address in network for network in networks)


def is_valid_discord_id(value: str) -> bool:
    """Say whether a header value looks like a Discord snowflake id.

    Discord ids are decimal digits. Rejecting anything else keeps portal ids
    from colliding with local usernames such as ``admin``.

    Args:
        value: Value of ``X-Discord-User-Id``.

    Returns:
        True for 1 to 32 ASCII digits.
    """
    return value.isascii() and value.isdigit() and len(value) <= 32


def sync_user(user, *, email: str, avatar_url: str, access_level: str) -> None:
    """Copy portal-provided attributes onto the user.

    The role follows the portal on every request: ``admin`` makes the user
    staff and a member of ``app-admins`` (never a superuser); anything else
    removes both.

    Args:
        user: The authenticated user.
        email: Value of ``X-Discord-Email``, possibly empty.
        avatar_url: Value of ``X-Discord-Avatar-Url``, possibly empty.
        access_level: Value of ``X-Discord-Access-Level``.
    """
    is_admin = access_level == 'admin'
    changed: list[str] = []
    # Values longer than the columns are ignored rather than crashing the request.
    if email and len(email) <= EMAIL_MAX and user.email != email:
        user.email = email
        changed.append('email')
    if avatar_url and len(avatar_url) <= AVATAR_MAX and user.avatar_url != avatar_url:
        user.avatar_url = avatar_url
        changed.append('avatar_url')
    if user.is_staff != is_admin:
        user.is_staff = is_admin
        changed.append('is_staff')
    if changed:
        user.save(update_fields=changed)
    group, _ = Group.objects.get_or_create(name=ADMIN_GROUP)
    in_group = user.groups.filter(pk=group.pk).exists()
    if is_admin and not in_group:
        user.groups.add(group)
    elif not is_admin and in_group:
        user.groups.remove(group)
```

**Create `sources/accounts/backends.py`:**

```python
"""Authentication backend for portal users."""

from django.contrib.auth.backends import RemoteUserBackend


class PortalBackend(RemoteUserBackend):
    """Create users on first sight, keyed by the Discord user id."""

    create_unknown_user = True

    def configure_user(self, request, user, created=True):
        """Record the Discord id and disable password login for new users."""
        if created:
            user.discord_id = user.get_username()
            user.set_unusable_password()
            user.save(update_fields=['discord_id', 'password'])
        return user
```

**Create `sources/accounts/middleware.py`:**

```python
"""Middleware for portal authentication and per-user language."""

from django.conf import settings
from django.contrib import auth
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.utils import translation

from accounts.portal import is_trusted_peer, is_valid_discord_id, sync_user

ID_HEADER = 'HTTP_X_DISCORD_USER_ID'


class PortalAuthMiddleware:
    """Log users in from the headers set by nginx after the portal check.

    Headers are honoured only when the TCP peer is inside a trusted proxy
    network. A request without the header ends any existing session, and an
    anonymous request to a view that requires a login gets 401 (views marked
    ``login_not_required`` stay public).
    """

    sync_capable = True
    async_capable = False

    def __init__(self, get_response):
        """Store the next handler."""
        self.get_response = get_response

    def __call__(self, request):
        """Authenticate from the headers, then run the rest of the stack."""
        discord_id = request.META.get(ID_HEADER)
        if discord_id is None:
            if request.user.is_authenticated:
                auth.logout(request)
        elif not is_trusted_peer(
            request.META.get('REMOTE_ADDR', ''), settings.PORTAL_TRUSTED_NETWORKS
        ):
            return HttpResponseForbidden('Portal headers from an untrusted peer')
        elif not is_valid_discord_id(discord_id):
            return HttpResponseBadRequest('Invalid portal user id')
        else:
            self._sign_in(request, discord_id)
            if request.user.is_authenticated:
                sync_user(
                    request.user,
                    email=request.META.get('HTTP_X_DISCORD_EMAIL', ''),
                    avatar_url=request.META.get('HTTP_X_DISCORD_AVATAR_URL', ''),
                    access_level=request.META.get('HTTP_X_DISCORD_ACCESS_LEVEL', ''),
                )
        return self.get_response(request)

    def _sign_in(self, request, discord_id: str) -> None:
        """Make ``request.user`` the portal user, replacing a different session."""
        if request.user.is_authenticated:
            if request.user.get_username() == discord_id:
                return
            auth.logout(request)
        user = auth.authenticate(request, remote_user=discord_id)
        if user:
            request.user = user
            auth.login(request, user)

    def process_view(self, request, view_func, view_args, view_kwargs):
        """Reject anonymous requests to views that require a login."""
        if request.user.is_authenticated or not getattr(
            view_func, 'login_required', True
        ):
            return None
        return HttpResponse('Authentication required', status=401)


class ProfileLanguageMiddleware:
    """Activate the signed-in user's interface language."""

    def __init__(self, get_response):
        """Store the next handler."""
        self.get_response = get_response

    def __call__(self, request):
        """Switch language for authenticated users before the view runs."""
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            language = user.profile.interface_language
            translation.activate(language)
            request.LANGUAGE_CODE = language
        return self.get_response(request)
```

**Create `sources/accounts/views.py`:**

```python
"""Language view."""

from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.http import HttpResponseRedirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST


@login_not_required
@require_POST
def set_language(request):
    """Save the chosen interface language and return to the previous page.

    The language goes to the profile for signed-in users and to a cookie for
    everyone, so the login page honours it too.
    """
    code = request.POST.get('language', '')
    target = request.POST.get('next', '/')
    if not url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        target = '/'
    response = HttpResponseRedirect(target)
    if code in dict(settings.LANGUAGES):
        if request.user.is_authenticated:
            request.user.profile.interface_language = code
            request.user.profile.save(update_fields=['interface_language'])
        response.set_cookie(
            settings.LANGUAGE_COOKIE_NAME, code, max_age=settings.LANGUAGE_COOKIE_AGE
        )
    return response
```

**Create `sources/accounts/urls.py`:**

```python
"""Account URLs."""

from django.urls import path

from accounts import views

urlpatterns = [
    path('language/', views.set_language, name='set_language'),
]
```

**Create `sources/accounts/migrations/0002_app_admins_group.py`:**

```python
"""Create the ``app-admins`` group that portal admins join."""

from django.db import migrations

GROUP_NAME = 'app-admins'


def create_group(apps, schema_editor):
    """Create the group if it does not exist."""
    Group = apps.get_model('auth', 'Group')
    Group.objects.get_or_create(name=GROUP_NAME)


def delete_group(apps, schema_editor):
    """Remove the group on rollback."""
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name=GROUP_NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0001_initial'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [migrations.RunPython(create_group, delete_group)]
```

**Replace the whole file `sources/mitaroku/urls.py`:**

```python
"""Root URL configuration."""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('accounts.urls')),
    path('', include('web.urls')),
]
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `python -m pytest -q`
Expected: `34 passed`.

- [ ] **Step 5: Commit**

```bash
ruff check --fix sources tests && ruff format sources tests
git add sources tests
git commit -m "feat: add portal header authentication, admin role sync and language switch"
```

---

### Task 4: Application shell

**Files:**
- Create: `sources/web/context_processors.py`, `sources/web/templatetags/{__init__,web_extras}.py`, `sources/web/templates/web/{base,_theme_toggle,_header,_bottom_nav,library,upcoming}.html`, `sources/web/static/web/{app.css,theme.js,ds.css}`, `sources/web/static/web/vendor/{htmx.min.js,alpine.min.js}`, `sources/locale/ru/LC_MESSAGES/django.po`
- Replace: `sources/web/views.py`, `sources/web/urls.py`, `tests/conftest.py`
- Create: `tests/test_shell.py`

**Interfaces:**
- Consumes: `PortalAuthMiddleware`, `ProfileLanguageMiddleware`, URL name `set_language` from Task 3.
- Produces:
  - Template `web/base.html` with blocks `title`, `header`, `content`, `bottom`; later pages extend it.
  - Context processor `web.context_processors.shell` providing `current_nav` (the active URL name), `auth_mode`, `portal_logout_url`, `language_choices`.
  - Template filter `initials` in `web_extras`.
  - URL name `upcoming`; the `library` view now renders `web/library.html`.
  - Static assets under `web/` including the design system CSS and the theme script (`localStorage` key `mitaroku-theme`, attribute `data-theme` on `<html>`).

- [ ] **Step 1: Write the failing tests**

`tests/conftest.py` gains a session fixture that compiles the `.po` files so the Russian text can be asserted (this needs the `gettext` package).

**Replace the whole file `tests/conftest.py`:**

```python
"""Shared fixtures."""

import pytest
from django.core.management import call_command

PORTAL = {
    'HTTP_X_DISCORD_USER_ID': '1001',
    'HTTP_X_DISCORD_EMAIL': 'kira@example.com',
    'HTTP_X_DISCORD_AVATAR_URL': 'https://cdn.example.com/a.png',
    'HTTP_X_DISCORD_ACCESS_LEVEL': 'member',
}


@pytest.fixture(scope='session', autouse=True)
def compiled_messages() -> None:
    """Compile the .po files so translations can be asserted."""
    call_command('compilemessages', verbosity=0)


@pytest.fixture
def portal_headers() -> dict[str, str]:
    """Return a fresh copy of valid portal headers for a member."""
    return dict(PORTAL)
```

**Create `tests/test_shell.py`:**

```python
import pytest
from django.contrib.auth import get_user_model
from web.templatetags.web_extras import initials

User = get_user_model()
pytestmark = pytest.mark.django_db


def test_root_redirects_to_library(client, portal_headers):
    response = client.get('/', **portal_headers)
    assert response.status_code == 302
    assert response['Location'] == '/library/'


def test_library_shows_navigation_theme_toggle_and_user_menu(client, portal_headers):
    html = client.get('/library/', **portal_headers).content.decode()
    assert '<html lang="en"' in html
    assert 'aria-current="page"' in html
    assert 'data-theme-toggle' in html
    assert 'aria-label="Account"' in html
    assert 'class="mt-bottom-nav"' in html
    assert 'X-CSRFToken' in html


def test_current_nav_marks_only_the_active_page(client, portal_headers):
    html = client.get('/upcoming/', **portal_headers).content.decode()
    upcoming = html.split('href="/upcoming/"')[1][:40]
    library = html.split('href="/library/"')[1][:40]
    assert 'aria-current="page"' in upcoming
    assert 'aria-current="page"' not in library


def test_russian_profile_language_translates_the_shell(client, portal_headers):
    client.get('/library/', **portal_headers)
    profile = User.objects.get(discord_id='1001').profile
    profile.interface_language = 'ru'
    profile.save()
    html = client.get('/library/', **portal_headers).content.decode()
    assert '<html lang="ru"' in html
    assert 'Библиотека' in html
    assert 'Выйти' not in html  # no logout item without a portal logout URL


def test_logout_link_appears_only_with_portal_logout_url(
    client, portal_headers, settings
):
    settings.PORTAL_LOGOUT_URL = 'https://portal.example/logout'
    html = client.get('/library/', **portal_headers).content.decode()
    assert 'href="https://portal.example/logout"' in html


@pytest.mark.parametrize(
    ('full_name', 'username', 'expected'),
    [('Kira Mori', 'k', 'KM'), ('', '1001', '10'), ('Cher', 'c', 'CH')],
)
def test_initials(full_name, username, expected):
    user = User(username=username)
    user.first_name, _, user.last_name = full_name.partition(' ')
    assert initials(user) == expected
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python -m pytest -q`
Expected: collection error `ModuleNotFoundError: No module named 'web.templatetags'`.

- [ ] **Step 3: Add the design system CSS and the vendored scripts**

```bash
mkdir -p sources/web/static/web/vendor
cp docs/design/_ds/*/styles.css sources/web/static/web/ds.css
curl -fsSL -o sources/web/static/web/vendor/htmx.min.js https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js
curl -fsSL -o sources/web/static/web/vendor/alpine.min.js https://unpkg.com/alpinejs@3.14.9/dist/cdn.min.js
cd sources/web/static/web/vendor && sha256sum -c - <<'EOF'
e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447  htmx.min.js
3ed1eed252488921df65e363d6715deb04d7f92aaedb9e52199fdf73cb1e0ad3  alpine.min.js
EOF
cd -
```

Expected: both lines print `OK`. (`ds.css` is the design system exactly as the mockup uses it; do not edit it. Overrides go in `app.css`.)

- [ ] **Step 4: Implement the shell**

**Create `sources/web/context_processors.py`:**

```python
"""Template context shared by every page."""

from django.conf import settings


def shell(request) -> dict:
    """Expose the data the application shell needs.

    Returns:
        Navigation state, the authentication mode, the portal logout URL and
        the available languages.
    """
    match = request.resolver_match
    return {
        'current_nav': match.url_name if match else '',
        'auth_mode': settings.AUTH_MODE,
        'portal_logout_url': settings.PORTAL_LOGOUT_URL,
        'language_choices': settings.LANGUAGES,
    }
```

**Replace the whole file `sources/web/views.py`:**

```python
"""Page and health views."""

import redis
from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import redirect, render


def index(request):
    """Send the root URL to the library."""
    return redirect('library')


def library(request):
    """Render the library page (filled in by the tracking plan)."""
    return render(request, 'web/library.html')


def upcoming(request):
    """Render the upcoming page (filled in by the tracking plan)."""
    return render(request, 'web/upcoming.html')


@login_not_required
def healthz(request):
    """Report whether PostgreSQL and Redis answer.

    Returns:
        200 with ``{"status": "ok"}`` or 503 naming the failing dependency.
    """
    failing = []
    try:
        connection.ensure_connection()
    except Exception:
        failing.append('database')
    try:
        redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            password=settings.REDIS_PASSWORD,
            socket_connect_timeout=2,
        ).ping()
    except Exception:
        failing.append('redis')
    if failing:
        return JsonResponse({'status': 'error', 'failing': failing}, status=503)
    return JsonResponse({'status': 'ok'})
```

**Replace the whole file `sources/web/urls.py`:**

```python
"""Web URLs."""

from django.urls import path

from web import views

urlpatterns = [
    path('', views.index, name='index'),
    path('library/', views.library, name='library'),
    path('upcoming/', views.upcoming, name='upcoming'),
    path('healthz/', views.healthz, name='healthz'),
]
```

**Create the empty file `sources/web/templatetags/__init__.py`.**

**Create `sources/web/templatetags/web_extras.py`:**

```python
"""Template filters for the shell."""

from django import template

register = template.Library()


@register.filter
def initials(user) -> str:
    """Return up to two uppercase initials for a user.

    Args:
        user: A user with ``get_full_name`` and ``get_username``.

    Returns:
        Initials from the full name, or the first two letters of the username.
    """
    name = (user.get_full_name() or user.get_username()).strip()
    words = name.split()
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    return name[:2].upper()
```

**Create `sources/web/templates/web/base.html`:**

```html
{% load i18n static %}{% get_current_language as LANGUAGE_CODE %}<!doctype html>
<html lang="{{ LANGUAGE_CODE }}" data-theme="light">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}mitaroku{% endblock %} · mitaroku</title>
  <link rel="stylesheet" href="{% static 'web/ds.css' %}">
  <link rel="stylesheet" href="{% static 'web/app.css' %}">
  <script src="{% static 'web/theme.js' %}"></script>
  <script src="{% static 'web/vendor/htmx.min.js' %}" defer></script>
  <script src="{% static 'web/vendor/alpine.min.js' %}" defer></script>
</head>
<body hx-headers='{"X-CSRFToken": "{{ csrf_token }}"}'>
  <a class="mt-skip" href="#main">{% trans 'Skip to content' %}</a>
  {% block header %}{% include 'web/_header.html' %}{% endblock %}
  <main id="main" class="mt-page">
    {% block content %}{% endblock %}
  </main>
  {% block bottom %}{% if user.is_authenticated %}{% include 'web/_bottom_nav.html' %}{% endif %}{% endblock %}
</body>
</html>
```

**Create `sources/web/templates/web/_theme_toggle.html`:**

```html
{% load i18n %}
<button type="button" class="btn btn-secondary mt-icon-btn" data-theme-toggle
        data-label-dark="{% trans 'Switch to dark theme' %}"
        data-label-light="{% trans 'Switch to light theme' %}"
        aria-label="{% trans 'Switch to dark theme' %}" title="{% trans 'Switch to dark theme' %}">
  <svg class="mt-icon-moon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"></path></svg>
  <svg class="mt-icon-sun" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"></circle><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"></path></svg>
</button>
```

**Create `sources/web/templates/web/_header.html`:**

```html
{% load i18n web_extras %}
<header class="mt-header">
  <div class="mt-header-inner">
    <a class="mt-logo" href="{% url 'library' %}" aria-label="{% trans 'mitaroku, go to library' %}">
      <span class="mt-logo-mark">見た録</span>
      <span class="mt-logo-name">mitaroku</span>
    </a>
    {% if user.is_authenticated %}
    <nav class="mt-nav" aria-label="{% trans 'Main' %}">
      <a href="{% url 'library' %}"{% if current_nav == 'library' %} aria-current="page"{% endif %}>{% trans 'Library' %}</a>
      <a href="{% url 'upcoming' %}"{% if current_nav == 'upcoming' %} aria-current="page"{% endif %}>{% trans 'Upcoming' %}</a>
    </nav>
    {% endif %}
    <span class="mt-spacer"></span>
    {% include 'web/_theme_toggle.html' %}
    {% if user.is_authenticated %}
    <div class="mt-user" x-data="{ open: false }" @keydown.escape="open = false" @click.outside="open = false">
      <button type="button" class="mt-user-btn" aria-haspopup="menu" :aria-expanded="open"
              aria-label="{% trans 'Account' %}" title="{% trans 'Account' %}" @click="open = !open">
        <span class="mt-avatar" aria-hidden="true">{{ user|initials }}</span>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"></path></svg>
      </button>
      <div class="mt-menu" role="menu" aria-label="{% trans 'Account' %}" x-show="open" x-cloak>
        <div class="mt-menu-identity">
          <strong>{{ user.get_full_name|default:user.get_username }}</strong>
          {% if user.email %}<span>{{ user.email }}</span>{% endif %}
        </div>
        <form class="mt-lang" method="post" action="{% url 'set_language' %}" role="group" aria-label="{% trans 'Language' %}">
          {% csrf_token %}
          <input type="hidden" name="next" value="{{ request.get_full_path }}">
          <span class="mt-lang-label">{% trans 'Language' %}</span>
          <div class="mt-lang-options">
            {% get_current_language as CURRENT_LANGUAGE %}
            {% for code, name in language_choices %}
            <button type="submit" name="language" value="{{ code }}" role="menuitemradio" lang="{{ code }}"
                    aria-checked="{% if code == CURRENT_LANGUAGE %}true{% else %}false{% endif %}">{{ code|upper }}</button>
            {% endfor %}
          </div>
        </form>
        {% if auth_mode == 'local' %}
        <form method="post" action="{% url 'logout' %}">
          {% csrf_token %}
          <button type="submit" class="mt-menu-item" role="menuitem">{% trans 'Log out' %}</button>
        </form>
        {% elif portal_logout_url %}
        <a class="mt-menu-item" role="menuitem" href="{{ portal_logout_url }}">{% trans 'Log out' %}</a>
        {% endif %}
      </div>
    </div>
    {% endif %}
  </div>
</header>
```

**Create `sources/web/templates/web/_bottom_nav.html`:**

```html
{% load i18n %}
<nav class="mt-bottom-nav" aria-label="{% trans 'Main' %}">
  <a href="{% url 'library' %}"{% if current_nav == 'library' %} aria-current="page"{% endif %}>
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m16 6 4 14M12 6v14M8 8v12M4 4v16"></path></svg>
    <span>{% trans 'Library' %}</span>
  </a>
  <a href="{% url 'upcoming' %}"{% if current_nav == 'upcoming' %} aria-current="page"{% endif %}>
    <svg viewBox="0 0 24 24" aria-hidden="true"><rect width="18" height="18" x="3" y="4"></rect><path d="M16 2v4M8 2v4M3 10h18"></path></svg>
    <span>{% trans 'Upcoming' %}</span>
  </a>
</nav>
```

**Create `sources/web/templates/web/library.html`:**

```html
{% extends 'web/base.html' %}{% load i18n %}
{% block title %}{% trans 'Library' %}{% endblock %}
{% block content %}
<h1 class="mt-page-title">{% trans 'Library' %}</h1>
<p class="mt-muted">{% trans 'Your titles will appear here.' %}</p>
{% endblock %}
```

**Create `sources/web/templates/web/upcoming.html`:**

```html
{% extends 'web/base.html' %}{% load i18n %}
{% block title %}{% trans 'Upcoming' %}{% endblock %}
{% block content %}
<h1 class="mt-page-title">{% trans 'Upcoming' %}</h1>
<p class="mt-muted">{% trans 'Upcoming episodes and premieres will appear here.' %}</p>
{% endblock %}
```

**Create `sources/web/static/web/app.css`:**

```css
/* Application shell on top of the Modernist design system (ds.css). */

:root {
  --mt-muted: color-mix(in srgb, var(--color-text) 72%, transparent);
  --mt-link: oklch(0.46 0.11 255);
}

:root[data-theme='dark'] {
  --color-bg: #1c1a19;
  --color-surface: #282625;
  --color-text: #ecebea;
  --color-accent: #ff563c;
  --color-divider: color-mix(in srgb, #ecebea 30%, transparent);
  --mt-link: oklch(0.8 0.08 255);
  color-scheme: dark;
}

[x-cloak] {
  display: none !important;
}

body {
  margin: 0;
  min-height: 100vh;
  background: var(--color-bg);
  color: var(--color-text);
  font-family: var(--font-body);
}

a {
  color: var(--mt-link);
  text-decoration: none;
  text-underline-offset: 3px;
}

a:hover {
  text-decoration: underline;
}

.mt-skip {
  position: absolute;
  left: -9999px;
  top: 0;
  z-index: 100;
  padding: 12px 16px;
  background: var(--color-text);
  color: var(--color-bg);
}

.mt-skip:focus {
  left: 0;
}

.mt-header {
  border-bottom: 2px solid var(--color-divider);
}

.mt-header-inner {
  max-width: 960px;
  margin: 0 auto;
  padding: 6px 16px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.mt-logo {
  flex: none;
  min-height: 44px;
  display: flex;
  align-items: center;
  gap: 8px;
  margin-right: 12px;
  white-space: nowrap;
  color: var(--color-text);
}

.mt-logo:hover {
  text-decoration: none;
  color: var(--color-text);
}

.mt-logo-mark {
  font-weight: 800;
  font-size: 18px;
  letter-spacing: -0.01em;
}

.mt-logo-name {
  font-weight: 600;
  font-size: 13px;
  color: var(--mt-muted);
}

.mt-nav {
  display: flex;
  gap: 2px;
}

.mt-nav a {
  min-height: 44px;
  display: flex;
  align-items: center;
  padding: 0 10px;
  border-bottom: 2px solid transparent;
  color: var(--color-text);
  font-size: 14px;
  white-space: nowrap;
}

.mt-nav a:hover {
  border-bottom-color: var(--color-text);
  text-decoration: none;
}

.mt-nav a[aria-current='page'] {
  border-bottom-color: var(--color-text);
  font-weight: 800;
}

.mt-spacer {
  flex: 1;
}

.mt-icon-btn {
  flex: none;
  width: 44px;
  height: 44px;
  min-width: 44px;
  min-height: 44px;
  padding: 0;
  display: grid;
  place-items: center;
}

.mt-icon-btn svg {
  width: 18px;
  height: 18px;
  fill: none;
  stroke: currentColor;
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
}

:root:not([data-theme='dark']) .mt-icon-sun,
:root[data-theme='dark'] .mt-icon-moon {
  display: none;
}

.mt-user {
  position: relative;
  flex: none;
}

.mt-user-btn {
  height: 44px;
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 0 4px;
  background: none;
  border: 0;
  color: var(--color-text);
  cursor: pointer;
}

.mt-user-btn:hover {
  background: color-mix(in srgb, var(--color-text) 7%, transparent);
}

.mt-user-btn svg {
  width: 14px;
  height: 14px;
  fill: none;
  stroke: currentColor;
  stroke-width: 2.5;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.mt-avatar {
  width: 32px;
  height: 32px;
  display: grid;
  place-items: center;
  background: var(--color-text);
  color: var(--color-bg);
  font-size: 12px;
  font-weight: 800;
}

.mt-menu {
  position: absolute;
  right: 0;
  top: calc(100% + 6px);
  z-index: 60;
  width: 240px;
  background: var(--color-bg);
  border: 1px solid var(--color-text);
  box-shadow: var(--shadow-md);
  display: grid;
  padding: 4px 0;
}

.mt-menu-identity {
  padding: 10px 14px;
  display: grid;
  gap: 2px;
  border-bottom: 1px solid var(--color-divider);
}

.mt-menu-identity strong {
  font-size: 14px;
}

.mt-menu-identity span {
  font-size: 12px;
  color: var(--mt-muted);
  overflow-wrap: anywhere;
}

.mt-menu-item {
  min-height: 44px;
  display: flex;
  align-items: center;
  padding: 0 14px;
  width: 100%;
  box-sizing: border-box;
  background: none;
  border: 0;
  font: inherit;
  font-size: 14px;
  color: var(--color-text);
  text-align: left;
  cursor: pointer;
}

.mt-menu-item:hover {
  background: color-mix(in srgb, var(--color-text) 7%, transparent);
  color: var(--color-text);
  text-decoration: none;
}

.mt-lang {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 14px;
  border-top: 1px solid var(--color-divider);
  border-bottom: 1px solid var(--color-divider);
}

.mt-lang-label {
  flex: 1;
  font-size: 14px;
}

.mt-lang-options {
  display: flex;
  border: 1px solid var(--color-divider);
}

.mt-lang-options button {
  min-width: 44px;
  height: 44px;
  padding: 0 8px;
  border: 0;
  background: transparent;
  color: var(--color-text);
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
}

.mt-lang-options button[aria-checked='true'] {
  background: var(--color-text);
  color: var(--color-bg);
}

.mt-page {
  max-width: 960px;
  margin: 0 auto;
  padding: 24px 16px 32px;
}

.mt-page-title {
  margin: 0 0 8px;
  font-size: 40px;
  font-weight: 800;
  letter-spacing: -0.02em;
}

.mt-muted {
  color: var(--mt-muted);
}

.mt-bottom-nav {
  display: none;
}

@media (max-width: 719.98px) {
  .mt-logo-name,
  .mt-nav {
    display: none;
  }

  .mt-page {
    padding-bottom: 88px;
  }

  .mt-bottom-nav {
    position: fixed;
    left: 0;
    right: 0;
    bottom: 0;
    z-index: 50;
    background: var(--color-bg);
    border-top: 2px solid var(--color-divider);
    display: grid;
    grid-auto-flow: column;
    grid-auto-columns: minmax(0, 1fr);
    padding-bottom: env(safe-area-inset-bottom);
  }

  .mt-bottom-nav a {
    min-height: 56px;
    display: grid;
    justify-items: center;
    align-content: center;
    gap: 3px;
    padding: 4px 2px;
    margin-top: -2px;
    border-top: 2px solid transparent;
    color: var(--color-text);
    font-size: 11px;
    line-height: 1.1;
    text-align: center;
  }

  .mt-bottom-nav a[aria-current='page'] {
    border-top-color: var(--color-text);
    font-weight: 800;
  }

  .mt-bottom-nav svg {
    width: 22px;
    height: 22px;
    fill: none;
    stroke: currentColor;
    stroke-width: 2;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
}

.mt-login {
  max-width: 400px;
  margin: 48px auto;
  padding: 0 16px;
  display: grid;
  gap: 16px;
}

.mt-login form {
  display: grid;
  gap: 16px;
}

.mt-error {
  margin: 0;
  padding: 10px 12px;
  border: 2px solid var(--color-accent-700);
  font-size: 14px;
}

/* Red surfaces that carry text use accent-700 (6.6:1 with light text). */
.btn-primary {
  background: var(--color-accent-700);
  color: var(--color-neutral-100);
}

.btn-primary:hover {
  background: var(--color-accent-800);
}

.btn-primary:active {
  background: var(--color-accent-900);
}
```

**Create `sources/web/static/web/theme.js`:**

```javascript
/* Theme: apply the stored or preferred theme before first paint, then wire the toggles. */
(function () {
  var KEY = 'mitaroku-theme';

  function stored() {
    try {
      return localStorage.getItem(KEY);
    } catch (e) {
      return null;
    }
  }

  function preferred() {
    var saved = stored();
    if (saved === 'dark' || saved === 'light') {
      return saved;
    }
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  function apply(theme) {
    document.documentElement.dataset.theme = theme;
    document.querySelectorAll('[data-theme-toggle]').forEach(function (button) {
      var label = theme === 'dark' ? button.dataset.labelLight : button.dataset.labelDark;
      button.setAttribute('aria-label', label);
      button.title = label;
    });
  }

  apply(preferred());

  document.addEventListener('DOMContentLoaded', function () {
    apply(preferred());
    document.querySelectorAll('[data-theme-toggle]').forEach(function (button) {
      button.addEventListener('click', function () {
        var next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
        try {
          localStorage.setItem(KEY, next);
        } catch (e) {
          /* storage unavailable: the choice lasts for this page only */
        }
        apply(next);
      });
    });
  });
})();
```

**Create `sources/locale/ru/LC_MESSAGES/django.po`:**

```text
msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\n"
"Language: ru\n"
"Plural-Forms: nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);\n"

msgid "Skip to content"
msgstr "Перейти к содержимому"

msgid "mitaroku, go to library"
msgstr "mitaroku, перейти в библиотеку"

msgid "Main"
msgstr "Главное меню"

msgid "Library"
msgstr "Библиотека"

msgid "Upcoming"
msgstr "Скоро"

msgid "Account"
msgstr "Аккаунт"

msgid "Language"
msgstr "Язык"

msgid "Log out"
msgstr "Выйти"

msgid "Switch to dark theme"
msgstr "Включить тёмную тему"

msgid "Switch to light theme"
msgstr "Включить светлую тему"

msgid "Your titles will appear here."
msgstr "Здесь появятся ваши тайтлы."

msgid "Upcoming episodes and premieres will appear here."
msgstr "Здесь появятся ближайшие серии и премьеры."
```

- [ ] **Step 5: Run the tests and watch them pass**

Run: `python -m pytest -q`
Expected: `42 passed`.

- [ ] **Step 6: Look at it**

`config.yaml` starts in local mode, so override the mode through the environment to act as the portal sees it:

```bash
python sources/manage.py migrate
python sources/manage.py compilemessages
MITAROKU_AUTH__MODE=portal python sources/manage.py runserver
```

In another terminal send the headers the way nginx would:

```bash
curl -s -H 'X-Discord-User-Id: 1001' -H 'X-Discord-Email: kira@example.com' \
  -H 'X-Discord-Access-Level: member' http://127.0.0.1:8000/library/ | head -20
```

Expected: HTML with `<html lang="en"`, the `mt-header` markup and the `見た録` logo. `curl` without the two `-H` lines returns `Authentication required` (401). To see the page in a browser, use a header-injecting extension with the same headers, or wait for Task 5 and use local mode. Stop the server with Ctrl+C.

- [ ] **Step 7: Commit**

```bash
ruff check --fix sources tests && ruff format sources tests
git add sources tests
git commit -m "feat: add application shell with navigation, theme toggle and Russian translation"
```

---

### Task 5: Local login

**Files:**
- Create: `sources/accounts/templates/accounts/login.html`, `tests/test_local_auth.py`
- Replace: `sources/accounts/views.py`, `sources/accounts/urls.py`, `sources/locale/ru/LC_MESSAGES/django.po`

**Interfaces:**
- Consumes: `middleware_for('local')`, `backends_for('local')` from Task 2; `web/base.html` from Task 4.
- Produces: URL names `login` (local form; redirects to `/` in portal mode) and `logout` (POST only, redirects to `/login/`); template `accounts/login.html`.

- [ ] **Step 1: Write the failing tests**

**Create `tests/test_local_auth.py`:**

```python
import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from mitaroku.settings import backends_for, middleware_for

User = get_user_model()
pytestmark = pytest.mark.django_db

LOCAL = {
    'AUTH_MODE': 'local',
    'MIDDLEWARE': middleware_for('local'),
    'AUTHENTICATION_BACKENDS': backends_for('local'),
}


@override_settings(**LOCAL)
def test_anonymous_visitor_is_redirected_to_login(client):
    response = client.get('/library/')
    assert response.status_code == 302
    assert response['Location'].startswith('/login/')


@override_settings(**LOCAL)
def test_login_page_renders_without_a_session(client):
    response = client.get('/login/')
    assert response.status_code == 200
    assert 'name="username"' in response.content.decode()


@override_settings(**LOCAL)
def test_valid_credentials_sign_in(client):
    User.objects.create_user('kira', password='correct horse')
    response = client.post(
        '/login/',
        {'username': 'kira', 'password': 'correct horse', 'next': '/library/'},
    )
    assert response.status_code == 302
    assert response['Location'] == '/library/'
    assert client.get('/library/').status_code == 200


@override_settings(**LOCAL)
def test_wrong_password_shows_an_error(client):
    User.objects.create_user('kira', password='correct horse')
    response = client.post('/login/', {'username': 'kira', 'password': 'nope'})
    assert response.status_code == 200
    assert 'role="alert"' in response.content.decode()


@override_settings(**LOCAL)
def test_logout_requires_post_and_signs_out(client):
    User.objects.create_user('kira', password='pw')
    client.login(username='kira', password='pw')
    assert client.get('/logout/').status_code == 405
    assert client.post('/logout/').status_code == 302
    assert client.get('/library/').status_code == 302


@override_settings(**LOCAL)
def test_local_mode_shows_a_logout_form(client):
    User.objects.create_user('kira', password='pw')
    client.login(username='kira', password='pw')
    html = client.get('/library/').content.decode()
    assert 'action="/logout/"' in html


def test_portal_mode_redirects_login_to_the_library(client, portal_headers):
    response = client.get('/login/', **portal_headers)
    assert response.status_code == 302
    assert response['Location'] == '/'
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python -m pytest -q`
Expected: `6 failed, 43 passed`; the failures show `assert 404 == 200` for `/login/`.

- [ ] **Step 3: Implement local login**

**Replace the whole file `sources/accounts/views.py`:**

```python
"""Login, logout and language views."""

from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.views import LoginView
from django.http import HttpResponseRedirect
from django.shortcuts import redirect
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST


@method_decorator(login_not_required, name='dispatch')
class MitarokuLoginView(LoginView):
    """Local login form; in portal mode there is nothing to log in to."""

    template_name = 'accounts/login.html'
    redirect_authenticated_user = True

    def dispatch(self, request, *args, **kwargs):
        """Send portal-mode visitors to the library."""
        if settings.AUTH_MODE == 'portal':
            return redirect('/')
        return super().dispatch(request, *args, **kwargs)


@login_not_required
@require_POST
def set_language(request):
    """Save the chosen interface language and return to the previous page.

    The language goes to the profile for signed-in users and to a cookie for
    everyone, so the login page honours it too.
    """
    code = request.POST.get('language', '')
    target = request.POST.get('next', '/')
    if not url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        target = '/'
    response = HttpResponseRedirect(target)
    if code in dict(settings.LANGUAGES):
        if request.user.is_authenticated:
            request.user.profile.interface_language = code
            request.user.profile.save(update_fields=['interface_language'])
        response.set_cookie(
            settings.LANGUAGE_COOKIE_NAME, code, max_age=settings.LANGUAGE_COOKIE_AGE
        )
    return response
```

**Replace the whole file `sources/accounts/urls.py`:**

```python
"""Account URLs."""

from django.contrib.auth.views import LogoutView
from django.urls import path

from accounts import views

urlpatterns = [
    path('login/', views.MitarokuLoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('language/', views.set_language, name='set_language'),
]
```

**Create `sources/accounts/templates/accounts/login.html`:**

```html
{% extends 'web/base.html' %}{% load i18n %}
{% block title %}{% trans 'Sign in' %}{% endblock %}
{% block bottom %}{% endblock %}
{% block content %}
<div class="mt-login">
  <h1 class="mt-page-title">{% trans 'Sign in' %}</h1>
  <p class="mt-muted">{% trans 'Local account. Use this when the portal sign-in is not available.' %}</p>
  <form method="post" novalidate>
    {% csrf_token %}
    <input type="hidden" name="next" value="{{ next }}">
    {% if form.errors %}
    <p class="mt-error" role="alert">{% trans 'The username or password is wrong. Try again.' %}</p>
    {% endif %}
    <div class="field">
      <label for="id_username">{% trans 'Username' %}</label>
      <input id="id_username" class="input" name="username" autocomplete="username" required
             style="min-height:44px" value="{{ form.username.value|default:'' }}">
    </div>
    <div class="field">
      <label for="id_password">{% trans 'Password' %}</label>
      <input id="id_password" class="input" type="password" name="password"
             autocomplete="current-password" required style="min-height:44px">
    </div>
    <button type="submit" class="btn btn-primary" style="min-height:44px">{% trans 'Sign in' %}</button>
  </form>
  <p class="mt-muted">{% trans 'Forgot your password? Ask the administrator of this server to reset it.' %}</p>
</div>
{% endblock %}
```

**Replace the whole file `sources/locale/ru/LC_MESSAGES/django.po`:**

```text
msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\n"
"Language: ru\n"
"Plural-Forms: nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);\n"

msgid "Skip to content"
msgstr "Перейти к содержимому"

msgid "mitaroku, go to library"
msgstr "mitaroku, перейти в библиотеку"

msgid "Main"
msgstr "Главное меню"

msgid "Library"
msgstr "Библиотека"

msgid "Upcoming"
msgstr "Скоро"

msgid "Account"
msgstr "Аккаунт"

msgid "Language"
msgstr "Язык"

msgid "Log out"
msgstr "Выйти"

msgid "Switch to dark theme"
msgstr "Включить тёмную тему"

msgid "Switch to light theme"
msgstr "Включить светлую тему"

msgid "Your titles will appear here."
msgstr "Здесь появятся ваши тайтлы."

msgid "Upcoming episodes and premieres will appear here."
msgstr "Здесь появятся ближайшие серии и премьеры."

msgid "Sign in"
msgstr "Войти"

msgid "Local account. Use this when the portal sign-in is not available."
msgstr "Локальная учётная запись. Используйте её, когда вход через портал недоступен."

msgid "The username or password is wrong. Try again."
msgstr "Неверное имя пользователя или пароль. Попробуйте ещё раз."

msgid "Username"
msgstr "Имя пользователя"

msgid "Password"
msgstr "Пароль"

msgid "Forgot your password? Ask the administrator of this server to reset it."
msgstr "Забыли пароль? Попросите администратора сервера сбросить его."
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `python -m pytest -q`
Expected: `49 passed`.

- [ ] **Step 5: Try it in a browser**

```bash
python sources/manage.py migrate
python sources/manage.py createsuperuser        # any username and password
python sources/manage.py runserver
```

Open http://127.0.0.1:8000/library/ (your `config.yaml` uses `auth.mode: local`). Expected: a redirect to `/login/`, a wrong password shows the red alert, the right one lands on the Library page, and "Log out" in the user menu returns to `/login/`. Also check the dark theme and the 390 px width.

- [ ] **Step 6: Check the worker end to end**

With `runserver` stopped, in one terminal run `python sources/manage.py run_huey`, and in another:

```bash
python sources/manage.py shell -c "from web.tasks import ping; print(ping().get(blocking=True, timeout=10))"
```

Expected: `pong`, and the `run_huey` terminal logs `Executing web.tasks.ping`. (`config.example.yaml` sets `huey_immediate: false`, so this goes through Redis.)

- [ ] **Step 7: Commit**

```bash
ruff check --fix sources tests && ruff format sources tests
git add sources tests
git commit -m "feat: add local login and logout"
```

---

### Task 6: Continuous integration and documentation

**Files:**
- Create: `.github/workflows/tests.yml`
- Replace: `.github/workflows/lint.yml`, `.pre-commit-config.yaml`, `README.md`

**Interfaces:**
- Consumes: the full test suite from Tasks 1 to 5.
- Produces: a `Tests` workflow that runs pytest against PostgreSQL and Redis service containers on the same ports as `docker-compose.dev.yml`; a lint check that covers `tests/` as well as `sources/`.

- [ ] **Step 1: Add the test workflow**

**Create `.github/workflows/tests.yml`:**

```yaml
---
name: Tests

on:
  push:
  pull_request:
    branches: ["main"]

permissions:
  contents: read

jobs:
  pytest:
    name: pytest
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:17-alpine
        env:
          POSTGRES_USER: mitaroku
          POSTGRES_PASSWORD: mitaroku
          POSTGRES_DB: mitaroku
        ports:
          - 55432:5432
        options: >-
          --health-cmd "pg_isready -U mitaroku"
          --health-interval 5s
          --health-timeout 3s
          --health-retries 10
      redis:
        image: redis:7-alpine
        ports:
          - 56379:6379
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 5s
          --health-timeout 3s
          --health-retries 10
    steps:
      - uses: actions/checkout@v7

      - uses: actions/setup-python@v7
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: |
            sources/requirements.txt
            tests/requirements.txt

      - name: Install gettext (compiles the translations)
        run: sudo apt-get update && sudo apt-get install -y gettext

      - name: Install dependencies
        run: pip install -r tests/requirements.txt

      - name: Run tests
        run: python -m pytest
```

- [ ] **Step 2: Extend linting to the tests**

**Replace the whole file `.github/workflows/lint.yml`:**

```yaml
---
name: Lint

on:
  push:
  pull_request:
    branches: ["main"]

permissions:
  contents: read

jobs:
  ruff:
    name: ruff
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7

      - name: Run ruff check
        uses: astral-sh/ruff-action@v3
        with:
          args: "check sources/ tests/"

      - name: Run ruff format check
        uses: astral-sh/ruff-action@v3
        with:
          args: "format --check sources/ tests/"
```

**Replace the whole file `.pre-commit-config.yaml`:**

```yaml
---
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.17.0
    hooks:
      - id: ruff
        files: ^(sources|tests)/
      - id: ruff-format
        files: ^(sources|tests)/
```

- [ ] **Step 3: Replace the template README**

**Replace the whole file `README.md`:**

````markdown
# mitaroku (見た録)

A self-hosted tracker for movies, TV series and anime. Tracking follows the
MyShows and Yamtrack model; notifications about new episodes and release dates,
and import/export from other trackers, are on top.

- Requirements and design: `docs/superpowers/specs/2026-10-09-mitaroku-requirements-design.md`
- UI mockup (open `docs/design/export/Mitaroku.html`): `docs/design/`
- Implementation plans: `docs/superpowers/plans/`

## Development

You need Python 3.12, Docker with Compose, and `gettext` (for the translations).

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
docker compose -f docker-compose.dev.yml up -d --wait   # PostgreSQL :55432, Redis :56379
cp config.example.yaml config.yaml                      # local mode, git-ignored
.venv/bin/python sources/manage.py migrate
.venv/bin/python sources/manage.py compilemessages
.venv/bin/python sources/manage.py createsuperuser
.venv/bin/python sources/manage.py runserver            # http://127.0.0.1:8000
.venv/bin/python sources/manage.py run_huey             # background worker, second terminal
```

Run the tests with `.venv/bin/python -m pytest`. They need the same PostgreSQL and
Redis containers.

## Configuration

Settings come from `config.yaml` (override the path with `MITAROKU_CONFIG`) and from
environment variables prefixed `MITAROKU_`, with `__` between nested keys, for example
`MITAROKU_DATABASE__PASSWORD`. The environment wins over the file. See
`config.example.yaml` for every key.

## Authentication

- `auth.mode: portal` (production): nginx authenticates through the SSO portal and
  passes `X-Discord-User-Id`, `X-Discord-Email`, `X-Discord-Avatar-Url` and
  `X-Discord-Access-Level`. They are trusted only from addresses in
  `auth.trusted_proxies`.
- `auth.mode: local` (development, or when the portal is down): Django accounts and
  the `/login/` page.

An `admin` access level makes the user staff and a member of the `app-admins` group;
it never grants superuser rights.
````

- [ ] **Step 4: Run everything locally**

```bash
ruff check sources tests
ruff format --check sources tests
python -m pytest -q
python sources/manage.py makemigrations --check --dry-run
```

Expected: ruff reports `All checks passed!` and `36 files already formatted`, pytest reports `49 passed`, and `makemigrations --check` prints `No changes detected`.

- [ ] **Step 5: Commit**

```bash
git add .github .pre-commit-config.yaml README.md
git commit -m "ci: run tests with PostgreSQL and Redis and lint the tests; write the README"
```

- [ ] **Step 6: Ask for the push and confirm CI**

Tell the user the branch is ready and ask whether to push. After the push, open the Actions tab and confirm that `Lint`, `Tests` and `CodeQL` are green. CI is the only check this plan could not run locally: if `Tests` fails on the service containers, read the job log and fix `.github/workflows/tests.yml`.

---

## Definition of done

- `python -m pytest -q` reports 49 passed; ruff is clean.
- In local mode a user can sign in, see the Library and Upcoming pages in both themes and both languages, and sign out.
- In portal mode (`MITAROKU_AUTH__MODE=portal`) a request with valid headers from `127.0.0.1` shows the shell, a request without headers gets 401, and headers from other addresses get 403.
- `ping` runs through the real Huey worker.
- CI (`Lint`, `Tests`, `CodeQL`) is green after the user pushes.

## Spec coverage

| Spec section | Where |
| --- | --- |
| 2 Architecture (Django, PostgreSQL, Redis, Huey, Pydantic config) | Tasks 1 and 2 |
| 5 Screens: global navigation and shell | Task 4 (other screens belong to later plans) |
| 9 Authentication and authorisation | Tasks 3 and 5 |
| 10 Security: trusted headers, CSRF for HTMX, scoped errors | Tasks 3 and 4 |
| 11 Localisation foundation | Tasks 3, 4 and 5 |
| 12 Deployment: development dependencies only | Task 2 (`docker-compose.dev.yml`); the production image, nginx and Ansible belong to plan 6 |

Not in this plan: the catalog and providers (plan 2), tracking (plan 3), notifications (plan 4), import and export (plan 5), production deployment (plan 6).
