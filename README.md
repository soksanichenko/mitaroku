# service-repo-template

Baseline `.github/` workflows and root tooling files for a new zelgray.work
service repo, kept in sync with the fleet-wide audit across all
soksanichenko repos (reference: `discord-meow-bot`).

## What's here

- `.github/dependabot.yml` — pip + github-actions, grouped major/minor/patch
  into one PR per ecosystem. Add an `npm`/`gomod` block (same shape) if the
  project gains a `package.json`/`go.mod`.
- `.github/workflows/lint.yml` — ruff check + ruff format on `sources/`.
- `.github/workflows/codeql.yml` — CodeQL Advanced (Python + Actions). **Only
  works on a public repo** — delete it if this repo will be private (code
  scanning needs GitHub Advanced Security, not purchased for private repos on
  this account). It will also fail outright until there's at least one real
  `.py` file under `sources/` ("no source code seen during build") — expect
  it red until the app skeleton has real Python in it, and only add `CodeQL`
  to `quality-code-restrictions`'s required checks once it's passing (this
  template's own ruleset intentionally requires Socket Security only, not
  CodeQL, for that reason).
- `pyproject.toml`, `.pre-commit-config.yaml`, `requirements.txt`,
  `requirements.yml`, `LICENSE`, `.gitignore` — replace every `REPLACE_ME*`
  placeholder.

## What's NOT here

Dockerfile, Ansible deploy skeleton, and the three GitHub rulesets
(`only-signed-commits`, `default-restricts`, `quality-code-restrictions`) +
security toggles (secret scanning, push protection, Dependabot alerts) —
those need per-project answers (Postgres? Redis? HTTP port? public/private?)
that a static template can't ask. Use the **`new-service-repo`** Claude Code
skill instead of (or after) this template — it generates the same files
already parameterized, plus the Ansible/Dockerfile skeleton, and applies the
rulesets/security baseline via `gh api` once the repo is pushed.
