# mitaroku (見た録)

A self-hosted tracker for movies, TV series and anime. Tracking follows the
MyShows and Yamtrack model; notifications about new episodes and release dates,
and import/export from other trackers, are on top.

- Requirements and design: `docs/superpowers/specs/2026-10-09-mitaroku-requirements-design.md`
- UI mockup (open `docs/design/export/Mitaroku.html`): `docs/design/`
- Implementation plans: `docs/superpowers/plans/`

## Development

You need Python 3.12, Docker with Compose, and `gettext` (for the translations).
Run every command from the repository root: `config.yaml` is resolved relative to
the working directory.

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
