import argparse
import json
import os
import urllib.parse
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.cookies import CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app import auth, db, metrics


ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
MAX_BODY = 16 * 1024
DEV_MODE = os.environ.get("CP_DEV") == "1"


def json_bytes(data):
    return json.dumps(data, separators=(",", ":")).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "ControlPlane/1.0"

    def finish(self):
        try:
            super().finish()
        finally:
            db.close_connection()

    def send_error(self, code, message=None, explain=None):
        if urllib.parse.urlsplit(self.path).path.startswith("/api"):
            self.send_json(code, {"error": message or HTTPStatus(code).phrase})
        else:
            super().send_error(code, message, explain)

    def log_message(self, format, *args):
        return

    def send_json(self, status, data=None, headers=None):
        body = b"" if data is None else json_bytes(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def cookie(self, name):
        try:
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
        except CookieError:
            return None
        return cookies[name].value if name in cookies else None

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Invalid request body.")
        if self.headers.get("Transfer-Encoding") or length < 0:
            raise ValueError("Invalid request body.")
        if length > MAX_BODY:
            raise OverflowError
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Invalid request body.")
        if not isinstance(data, dict):
            raise ValueError("Invalid request body.")
        return data

    def request_ip(self):
        return self.client_address[0]

    def session(self):
        return auth.session_operator(self.cookie("cp_session"))

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/api" or parsed.path.startswith("/api/"):
            self.api_get(parsed.path)
            return
        self.static(parsed.path)

    def do_POST(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path != "/api" and not parsed.path.startswith("/api/"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            body = self.read_json()
        except OverflowError:
            self.send_json(413, {"error": "Request body too large."})
            return
        except ValueError as error:
            self.send_json(400, {"error": str(error)})
            return
        self.api_post(parsed.path, body)

    def api_get(self, path):
        if path == "/api/auth/me":
            operator = self.session()
            if operator is None:
                self.send_json(401, {"error": "Unauthorized"})
            else:
                self.send_json(200, {"email": operator["email"]})
            return
        if path == "/api/overview":
            if self.session() is None:
                self.send_json(401, {"error": "Unauthorized"})
            else:
                self.send_json(200, metrics.overview())
            return
        self.send_json(404, {"error": "Not found"})

    def api_post(self, path, body):
        if path == "/api/auth/login":
            email = str(body.get("email", "")).strip().lower()
            password = body.get("password", "")
            remember = bool(body.get("remember", False))
            if not isinstance(password, str):
                password = ""
            operator, retry = auth.operator_for_login(email, password, self.request_ip())
            if retry:
                self.send_json(429, {"error": f"Too many attempts. Retry in {retry}s.", "retry_after": retry})
            elif operator is None:
                self.send_json(401, {"error": "Invalid email or password."})
            else:
                token = auth.create_challenge(operator["id"], remember)
                self.send_json(200, {"challenge": "mfa", "challenge_token": token})
            return
        if path == "/api/auth/verify":
            token = body.get("challenge_token")
            code = body.get("code", "")
            if not isinstance(token, str):
                token = ""
            if not isinstance(code, str) or len(code) != 6 or any(character not in "0123456789" for character in code):
                self.send_json(400, {"error": "Enter the 6-digit code."})
                return
            challenge = auth.verify_challenge(token, code, DEV_MODE)
            if challenge is None:
                self.send_json(401, {"error": "Invalid or expired code."})
                return
            session = auth.create_session(challenge["operator_id"], challenge["remember"])
            cookie = f"cp_session={session}; HttpOnly; SameSite=Strict; Path=/"
            if challenge["remember"]:
                cookie += "; Max-Age=604800"
            self.send_json(200, {"ok": True}, {"Set-Cookie": cookie})
            return
        if path == "/api/auth/logout":
            auth.delete_session(self.cookie("cp_session"))
            self.send_json(204, None, {"Set-Cookie": "cp_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})
            return
        self.send_json(404, {"error": "Not found"})

    def static(self, path):
        path = urllib.parse.unquote(path)
        if "\x00" in path:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        relative = "index.html" if path in ("", "/") else path.lstrip("/")
        candidate = (WEB / relative).resolve()
        try:
            candidate.relative_to(WEB.resolve())
        except ValueError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_types = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}
        data = candidate.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_types.get(candidate.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--db", default="./controlplane.db")
    args = parser.parse_args()
    db.initialize(args.db)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    metrics.start_sampler()
    if DEV_MODE:
        print("WARNING: CP_DEV=1 enabled; MFA code 000000 is accepted.", flush=True)
    try:
        print(f"Control Plane listening on http://{args.host}:{args.port}", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        metrics.stop_sampler()
        db.close_connection()


if __name__ == "__main__":
    main()
