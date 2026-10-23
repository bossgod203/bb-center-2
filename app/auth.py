import base64
import hashlib
import hmac
import math
import secrets
import struct
import threading
import time
from datetime import datetime, timedelta, timezone

from . import db


SESSION_IDLE_SECONDS = 12 * 60 * 60
SESSION_MAX_SECONDS = 7 * 24 * 60 * 60
CHALLENGE_TTL = 300
_challenges = {}
_challenge_lock = threading.Lock()
_login_lock = threading.Lock()


def utc_now():
    return datetime.now(timezone.utc)


def iso_now(value=None):
    return (value or utc_now()).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _password_record(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return "scrypt$16384$8$1$%s$%s" % (
        base64.urlsafe_b64encode(salt).decode(),
        base64.urlsafe_b64encode(digest).decode(),
    )


def verify_password(password, record):
    try:
        method, n, r, p, salt_text, digest_text = record.split("$")
        if method != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode())
        expected = base64.urlsafe_b64decode(digest_text.encode())
        actual = hashlib.scrypt(password.encode(), salt=salt, n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def new_totp_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_code(secret, timestamp=None):
    timestamp = int(time.time() if timestamp is None else timestamp)
    counter = timestamp // 30
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1000000:06d}"


def verify_totp(secret, code):
    now = time.time()
    return any(hmac.compare_digest(totp_code(secret, now + shift * 30), code) for shift in (-1, 0, 1))


def cleanup_challenges():
    now = time.time()
    with _challenge_lock:
        for token, item in list(_challenges.items()):
            if item["expires"] <= now:
                _challenges.pop(token, None)


def create_challenge(operator_id, remember):
    cleanup_challenges()
    token = secrets.token_urlsafe(32)
    with _challenge_lock:
        _challenges[token] = {"operator_id": operator_id, "remember": bool(remember), "expires": time.time() + CHALLENGE_TTL}
    return token


def verify_challenge(token, code, dev_mode=False):
    cleanup_challenges()
    with _challenge_lock:
        challenge = _challenges.get(token)
        if challenge is None:
            return None
        operator = db.get_connection().execute(
            "SELECT totp_secret FROM operators WHERE id=?", (challenge["operator_id"],)
        ).fetchone()
        if operator is None or not ((dev_mode and code == "000000") or verify_totp(operator[0], code)):
            return None
        return _challenges.pop(token)


def lockout_seconds(email, ip):
    rows = db.get_connection().execute(
        "SELECT at, success FROM login_attempts WHERE email=? AND ip=? ORDER BY id",
        (email, ip),
    ).fetchall()
    failures = 0
    locked_until = None
    for row in rows:
        at = datetime.fromisoformat(row["at"].replace("Z", "+00:00"))
        if locked_until is not None and at >= locked_until:
            failures = 0
            locked_until = None
        failures = 0 if row["success"] else failures + 1
        if failures == 5:
            locked_until = at + timedelta(seconds=30)
    return max(0, math.ceil((locked_until - utc_now()).total_seconds())) if locked_until else 0


def record_attempt(email, ip, success):
    connection = db.get_connection()
    connection.execute("INSERT INTO login_attempts(email, ip, at, success) VALUES (?, ?, ?, ?)", (email, ip, iso_now(), int(success)))
    connection.commit()


def operator_for_login(email, password, ip):
    with _login_lock:
        return _operator_for_login(email, password, ip)


def _operator_for_login(email, password, ip):
    retry = lockout_seconds(email, ip)
    if retry:
        return None, retry
    connection = db.get_connection()
    row = connection.execute("SELECT * FROM operators WHERE email=?", (email,)).fetchone()
    valid = row is not None and verify_password(password, row["password_hash"])
    record_attempt(email, ip, valid)
    return (row if valid else None), (0 if valid else lockout_seconds(email, ip))


def create_session(operator_id, remember):
    now = utc_now()
    expires = now + timedelta(seconds=SESSION_MAX_SECONDS if remember else SESSION_IDLE_SECONDS)
    token = secrets.token_urlsafe(32)
    connection = db.get_connection()
    connection.execute(
        "INSERT INTO sessions(operator_id, token_hash, created_at, last_seen_at, expires_at) VALUES (?, ?, ?, ?, ?)",
        (operator_id, hashlib.sha256(token.encode()).hexdigest(), iso_now(now), iso_now(now), iso_now(expires)),
    )
    connection.commit()
    return token


def session_operator(token):
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    connection = db.get_connection()
    row = connection.execute(
        "SELECT sessions.*, operators.email FROM sessions JOIN operators ON operators.id=sessions.operator_id WHERE token_hash=?",
        (token_hash,),
    ).fetchone()
    if row is None:
        return None
    now = utc_now()
    last_seen = datetime.fromisoformat(row["last_seen_at"].replace("Z", "+00:00"))
    created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
    expires = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
    if now >= expires or (now - last_seen).total_seconds() > SESSION_IDLE_SECONDS:
        connection.execute("DELETE FROM sessions WHERE id=?", (row["id"],))
        connection.commit()
        return None
    if expires - created < timedelta(seconds=SESSION_MAX_SECONDS):
        expires = now + timedelta(seconds=SESSION_IDLE_SECONDS)
    connection.execute("UPDATE sessions SET last_seen_at=?, expires_at=? WHERE id=?", (iso_now(now), iso_now(expires), row["id"]))
    connection.commit()
    return row


def delete_session(token):
    if token:
        db.get_connection().execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
        db.get_connection().commit()


def password_hash(password):
    return _password_record(password)
