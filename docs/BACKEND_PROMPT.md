# TASK
Build the backend for "Control Plane", an internal game-operations admin console. The frontend is finished and lives in `./web` (static HTML/CSS/JS). Do not change it except `web/js/config.js` (set `mock:false`). Deliver a project started with `python server.py` that serves `./web` and the JSON API below at http://127.0.0.1:8000, backed by SQLite.

# HARD CONSTRAINTS
- Python 3.11+, standard library only. No pip installs, no frameworks. `python server.py` must work on a clean machine.
- SQLite via `sqlite3`. WAL mode, `PRAGMA foreign_keys=ON`, one connection per thread (threading.local), parameterised queries only.
- `http.server.ThreadingHTTPServer` with a small router. Bind 127.0.0.1 by default; flags `--host --port --db`.
- Password hashing: `hashlib.scrypt` (per-user salt, constant-time compare via `hmac.compare_digest`).
- TOTP: implement RFC 6238 with `hmac`/`hashlib` (SHA1, 6 digits, 30 s step, accept ±1 step, reject replay of an already-used step).

# LAYOUT
```
server.py            entry: parse args, init DB, start sampler thread, serve
app/config.py        settings (env + flags): HOST, PORT, DB_PATH, BOTS_EXPECTED=12, SESSION_IDLE=12h, REMEMBER=7d
app/db.py            connection helper, migrations runner (executes schema.sql once, tracks user_version)
app/schema.sql       tables + indexes
app/auth.py          scrypt, TOTP, challenge tokens, sessions, rate limiting
app/metrics.py       overview aggregation + host metrics + DB sampler
app/routes.py        router, handlers, JSON helpers, auth decorator
app/static.py        static file serving (path traversal safe, correct MIME, no directory listing)
seed.py              creates operator + demo data; prints the TOTP secret and otpauth:// URI
tests/test_api.py    unittest suite (spins server on ephemeral port, temp DB)
README.md            run, seed, test, endpoints
```

# SCHEMA (minimum)
- operators(id, email UNIQUE, password_hash, totp_secret, totp_last_step, created_at, disabled)
- sessions(id, operator_id, token_hash, created_at, last_seen_at, expires_at, remember, ip, user_agent)  -- store SHA-256 of token only
- login_attempts(id, email, ip, at, success)
- audit_log(id, at, operator_id NULL, event, ip, detail)  -- every login success/failure, MFA result, logout
- accounts(id, kind CHECK IN ('human','bot'), created_at, deleted_at NULL, last_active_at)
- matches(id, started_at, ended_at NULL, status CHECK IN ('active','completed','cancelled'), is_private)
- match_players(match_id, account_id)
- appeals(id, account_id, status CHECK IN ('pending','accepted','rejected'), created_at)
- db_samples(at, ok, latency_ms)
Indexes on every column used in the aggregations below.

# AUTH FLOW (two steps)
Session cookie: `cp_session`, HttpOnly, SameSite=Strict, Path=/, Secure when served over HTTPS. Max-Age 7d if `remember`, otherwise session cookie. Server-side sliding idle expiry 12h. Token = `secrets.token_urlsafe(32)`.

1. `POST /api/auth/login`  body `{email, password, remember}`
   - 200 `{"challenge":"mfa","challenge_token":"<opaque, single-use, 5 min TTL>"}`
   - 401 `{"error":"Invalid email or password."}`  (identical for unknown email; run a dummy scrypt to equalise timing)
   - 429 `{"error":"Too many attempts. Retry in Ns.","retry_after":N}`  (5 failures per email+IP -> 30 s lock, doubling on repeat, cap 15 min)
2. `POST /api/auth/verify`  body `{challenge_token, code}`
   - 200 `{"ok":true}` + Set-Cookie
   - 400 `{"error":"Enter the 6-digit code."}` (malformed)
   - 401 `{"error":"Invalid or expired code."}`
   - 429 as above (count MFA failures against the challenge; burn the challenge after 5)
3. `GET /api/auth/me` -> 200 `{"email":"..."}` | 401 `{"error":"Unauthorized"}`
4. `POST /api/auth/logout` -> 204, deletes session, clears cookie.

All other `/api/*` routes require a valid session, else 401 `{"error":"Unauthorized"}`. For every POST: require `Content-Type: application/json`, cap body at 16 KB, and reject if `Origin` is present and its host != `Host`. Errors are always `{"error": "<message>"}` with the correct status. Unknown API route -> 404 JSON. Add headers: `Cache-Control: no-store` on /api, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, and a CSP allowing only self plus `fonts.googleapis.com` (style) and `fonts.gstatic.com` (font).

# OVERVIEW ENDPOINT
`GET /api/overview` -> 200, exact shape (snake_case, ISO-8601 UTC timestamps):
```json
{
  "sampled_at": "2026-10-21T09:14:05Z",
  "players":    {"registered": 8, "today": 7, "week": 8},
  "games":      {"today": 155, "completed": 155, "cancelled": 0},
  "appeals":    {"pending": 0},
  "db":         {"uptime_pct": 100.0, "avg_ms": 444, "latency_ms": 118, "failed_checks_24h": 0, "sampled_at": "..."},
  "host":       {"cpu_pct": 47, "mem_gb": 0.1, "mem_max_gb": 0.5, "load": 5.33, "sampled_at": "..."},
  "population": {"humans": 8, "bots": 12, "accounts": 20, "matches": 156, "completed": 156, "private": 2, "cancelled": 0, "since": "2026-10-17"},
  "bots":       {"active": 12, "expected": 12, "played_1h": 6, "played_24h": 12, "played_7d": 12,
                 "last_match_minutes_ago": 6, "matches_24h": 152, "bot_share_pct_7d": 99,
                 "series": {"1h": [/*12 x 5-min buckets*/], "24h": [/*24 x 1-h buckets*/], "7d": [/*7 x 1-day buckets*/]}}
}
```
Definitions (all times UTC):
- players.registered = accounts kind='human' AND deleted_at IS NULL; today = created since 00:00 UTC; week = last 7 days.
- games.today = matches started since 00:00 UTC; completed/cancelled by status.
- appeals.pending = appeals.status='pending'.
- db: a background thread every 5 s runs `SELECT 1` on its own connection, timing it, and inserts into db_samples; prune rows older than 7 d. uptime_pct = ok samples / all samples in last 24 h (100.0 if none); avg_ms = mean latency over 24 h (int); latency_ms = latest; failed_checks_24h = count of ok=0.
- host: cpu_pct from /proc/stat delta between two samples; mem_gb = (MemTotal-MemAvailable)/1024^3 rounded to 1 dp, mem_max_gb = MemTotal rounded to 1 dp; load = os.getloadavg()[0] rounded 2 dp. Fall back to 0 values on platforms without /proc. Sampled by the same background thread.
- population.humans/bots = non-deleted accounts of that kind; accounts = all rows incl. deleted; matches/completed/cancelled = lifetime counts; private = is_private=1; since = date of earliest match.
- bots.active = non-deleted bot accounts; expected = config BOTS_EXPECTED; played_* = DISTINCT bot accounts with a match started in the window; matches_24h = matches in last 24 h with >=1 bot; bot_share_pct_7d = round(100 * matches with >=1 bot / all matches) over 7 d; last_match_minutes_ago = minutes since latest match with a bot; series = count of bot-containing matches per bucket, oldest -> newest, last bucket is the current one.
Cache the computed payload for 2 s to protect SQLite under many dashboards.

# SEED
`python seed.py` (idempotent, `--reset` wipes): create operator `operator@example.com` with a generated password and TOTP secret (print both once, plus the otpauth:// URI), 8 humans, 12 bots, ~156 matches over the last 4 days with realistic bot participation, 2 private matches, 0 appeals.

# TESTS (unittest)
Login happy path, wrong password, lockout + retry_after, MFA wrong/valid/replayed code, expired challenge, session required on /api/overview, logout invalidates session, overview schema keys and types, path traversal on static (`/../server.py`, `/%2e%2e/`) returns 404, CSRF Origin mismatch returns 403.

# ACCEPTANCE
1. `python seed.py && python server.py` then open http://127.0.0.1:8000 -> redirects to login.html when signed out.
2. Sign in with seeded credentials + current TOTP code -> dashboard renders live numbers refreshing every 5 s.
3. Sign out returns to login; back button does not show data (API returns 401).
4. `python -m unittest discover tests` passes.
5. No third-party imports anywhere.
