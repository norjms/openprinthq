# Contributing to OpenPrintHQ

Thank you for helping. Bug reports, fixes and new integrations are all welcome.

## Before you start

- For anything larger than a small fix, open an issue first so we can agree on the approach.
- Security problems: do not open a public issue. See [SECURITY.md](SECURITY.md).
- Pull requests need the contributor licence agreement in [CLA.md](CLA.md). The CLA bot asks you to sign it
  with a comment on your first pull request. You sign once.

## Run the hub locally

With Docker:

```sh
cp compose.example.yml compose.yml      # set the two CHANGE ME values
docker compose up -d --build
docker compose logs web worker | grep -i "setup token"
```

Then open `http://localhost:8080/setup`.

For work on the web UI, run the backend as above and the frontend dev server, which proxies `/api` to port 8080:

```sh
cd frontend
npm install
npm run dev
```

## Run the tests

The backend tests use SQLite and need no other service. Python 3.12:

```sh
python -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements.txt
cd backend && python -m pytest -q tests
```

CI runs the same tests in the `python:3.12-slim` image (with `libxml2` and `libxslt1.1`), builds the frontend
(`npm run build` in `frontend/`), runs a gitleaks secret scan and checks provenance
(`python tools/check_provenance.py`, tests in `tools/tests`).

Install the pre-commit hooks so secrets are caught before they reach a commit:

```sh
pip install pre-commit && pre-commit install
```

## Conventions

- **Layout.** HTTP routes in `backend/app/routers/`, logic in `backend/app/services/`, clients for other
  services in `backend/app/clients/`, model-site importers in `backend/app/sources/`, background jobs in
  `backend/app/tasks.py` (run by `app.worker`).
- **Settings** come only from `app.config.settings()`, never from `os.environ` in feature code. A new setting
  gets a neutral default (empty or off), a line in `docs/configuration.md`, and the feature says "not
  configured" rather than failing when it is empty.
- **Database changes** are forward-only. New tables come from the models; changes to existing tables are a new
  `(version, sql)` entry at the end of `MIGRATIONS` in `backend/app/db.py`. Never edit an old entry.
- **Style.** Python 3.12 with type hints (`str | None`), lines up to about 120 characters, docstrings that say
  what a function is for and why. A broad `except` is marked `# noqa: BLE001` and logs or explains itself.
- **Outbound requests** to addresses that come from users or other sites go through
  `services/safe_fetch.py`, which blocks private addresses and DNS rebinding.
- **Tests** for every change in behaviour, in `backend/tests/`. Fake the other services; tests never reach the
  network.
- **Words.** Plain, short sentences in docs, UI text and messages.

## Commits and pull requests

- One topic per pull request, with a clear description of what changes and why.
- Commit messages, pull request text, issues and comments are public. Never include internal host names or
  addresses, file share paths, credentials, tokens, keys, customer data or anything from a real deployment.
  Use `example.com`, `192.0.2.x` or similar placeholders.
- Never commit an env file or a filled-in `compose.yml`.

## Licence

OpenPrintHQ is licensed under the GNU AGPL-3.0. By contributing you agree to the terms in [CLA.md](CLA.md).
