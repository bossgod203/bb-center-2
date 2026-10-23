import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from app import auth, db, metrics
from seed import EMAIL, seed
import server
from server import Handler


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.credentials = seed(str(Path(cls.temp.name) / "api.db"))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        metrics.start_sampler()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        metrics.stop_sampler()
        db.close_connection()
        cls.temp.cleanup()

    def request(self, method, path, data=None, cookie=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        headers = {}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if cookie:
            headers["Cookie"] = cookie
        connection.request(method, path, json.dumps(data).encode() if data is not None else None, headers)
        response = connection.getresponse()
        body = response.read()
        payload = json.loads(body) if body and response.getheader("Content-Type", "").startswith("application/json") else body
        result = (response.status, payload, dict(response.getheaders()))
        connection.close()
        return result

    def login(self, remember=False):
        status, result, headers = self.request("POST", "/api/auth/login", {
            "email": EMAIL, "password": self.credentials["password"], "remember": remember,
        })
        self.assertEqual(status, 200)
        self.assertEqual(result["challenge"], "mfa")
        status, result, headers = self.request("POST", "/api/auth/verify", {
            "challenge_token": result["challenge_token"],
            "code": auth.totp_code(self.credentials["totp_secret"]),
        })
        self.assertEqual(status, 200)
        self.assertEqual(result, {"ok": True})
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        return headers["Set-Cookie"].split(";", 1)[0]

    def test_login_verify_and_me(self):
        cookie = self.login()
        status, result, headers = self.request("GET", "/api/auth/me", cookie=cookie)
        self.assertEqual((status, result), (200, {"email": EMAIL}))
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_wrong_password_and_lockout(self):
        status, result, _ = self.request("POST", "/api/auth/login", {"email": EMAIL, "password": "wrong-password"})
        self.assertEqual((status, result), (401, {"error": "Invalid email or password."}))
        wrong_email = "wrong@example.com"
        for attempt in range(5):
            status, result, _ = self.request("POST", "/api/auth/login", {"email": wrong_email, "password": "wrong-password"})
            self.assertEqual(status, 429 if attempt == 4 else 401)
        self.assertGreater(result["retry_after"], 0)
        status, result, _ = self.request("POST", "/api/auth/login", {"email": wrong_email, "password": "wrong-password"})
        self.assertEqual(status, 429)

    def test_unauthorized_and_logout(self):
        status, result, _ = self.request("GET", "/api/overview")
        self.assertEqual((status, result), (401, {"error": "Unauthorized"}))
        cookie = self.login()
        status, result, headers = self.request("POST", "/api/auth/logout", cookie=cookie)
        self.assertEqual(status, 204)
        self.assertEqual(result, b"")
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        status, result, _ = self.request("GET", "/api/overview", cookie=cookie)
        self.assertEqual((status, result), (401, {"error": "Unauthorized"}))

    def test_overview_shape_and_types(self):
        status, result, _ = self.request("GET", "/api/overview", cookie=self.login(True))
        self.assertEqual(status, 200)
        self.assertEqual(set(result), {"sampled_at", "players", "games", "appeals", "db", "host", "population", "bots"})
        keys = {
            "players": "registered today week",
            "games": "today completed cancelled",
            "appeals": "pending",
            "db": "uptime_pct avg_ms latency_ms failed_checks_24h sampled_at",
            "host": "cpu_pct mem_gb mem_max_gb load sampled_at",
            "population": "humans bots accounts matches completed private cancelled since",
            "bots": "active expected played_1h played_24h played_7d last_match_minutes_ago matches_24h bot_share_pct_7d series",
        }
        for section, fields in keys.items():
            self.assertEqual(set(result[section]), set(fields.split()))
        self.assertTrue(result["sampled_at"].endswith("Z"))
        self.assertEqual(result["population"]["accounts"], 20)
        self.assertEqual(result["population"]["matches"], 156)
        for section, length in (("1h", 12), ("24h", 24), ("7d", 7)):
            values = result["bots"]["series"][section]
            self.assertEqual(len(values), length)
            self.assertTrue(all(type(value) is int for value in values))
            self.assertGreater(sum(values), 0)
        for section in ("players", "games", "appeals", "population", "bots"):
            for key, value in result[section].items():
                if key not in ("since", "series"):
                    self.assertIs(type(value), int)
        for key in ("uptime_pct", "avg_ms", "latency_ms", "failed_checks_24h"):
            self.assertIsInstance(result["db"][key], (int, float))
        for key in ("cpu_pct", "mem_gb", "mem_max_gb", "load"):
            self.assertIsInstance(result["host"][key], (int, float))

    def test_mfa_and_static_handling(self):
        status, response, _ = self.request("POST", "/api/auth/login", {"email": EMAIL, "password": self.credentials["password"]})
        token = response["challenge_token"]
        self.assertEqual(self.request("POST", "/api/auth/verify", {"challenge_token": token, "code": "12"})[0], 400)
        self.assertEqual(self.request("POST", "/api/auth/verify", {"challenge_token": token, "code": "000000"})[0], 401)
        self.assertEqual(self.request("POST", "/api/auth/verify", {"challenge_token": token, "code": auth.totp_code(self.credentials["totp_secret"])})[0], 200)
        self.assertEqual(self.request("GET", "/api/missing")[0], 404)
        self.assertEqual(self.request("GET", "/login.html")[0], 200)
        self.assertEqual(self.request("GET", "/../README.md")[0], 404)

    def test_dev_mode_code(self):
        status, result, _ = self.request("POST", "/api/auth/login", {
            "email": EMAIL, "password": self.credentials["password"],
        })
        self.assertEqual(status, 200)
        with patch.object(server, "DEV_MODE", True):
            status, result, _ = self.request("POST", "/api/auth/verify", {
                "challenge_token": result["challenge_token"], "code": "000000",
            })
        self.assertEqual((status, result), (200, {"ok": True}))


if __name__ == "__main__":
    unittest.main()
