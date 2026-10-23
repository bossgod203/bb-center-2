# Control Plane

Standard-library Python 3.11+ backend for the static game-operations console in `web/`. All data lives in SQLite; no packages or build step are needed.

## Run

```sh
python seed.py
python server.py
```

Save the generated operator password, TOTP secret, and `otpauth://` URI printed by the first seed. The operator email is `operator@example.com`. Subsequent `python seed.py` runs retain the existing credentials and data; `python seed.py --reset` wipes and reseeds, printing fresh credentials.

Open <http://127.0.0.1:8000/login.html>. Import the `otpauth://` URI into an authenticator and sign in using its six-digit code. After signing in the dashboard polls `/api/overview` every five seconds. Log out using the sidebar button.

Options: `python server.py --host 127.0.0.1 --port 8000 --db ./controlplane.db` and `python seed.py --db ./controlplane.db`. Use the same database path with both commands. The database is initialized automatically if missing. The server defaults to loopback.

## Development mode

```sh
CP_DEV=1 python server.py
```

**Development only:** accepting TOTP code `000000` is disabled by default; this flag enables it and prints a warning. Real authenticator codes work in either mode. Never use this setting for production access.

## Tests

```sh
python -m unittest discover tests
```

Tests use a temporary SQLite database and an ephemeral loopback HTTP port.
