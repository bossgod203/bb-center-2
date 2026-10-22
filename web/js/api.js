(() => {
"use strict";
const cfg = window.CP_CONFIG || { mock: true };
const wait = ms => new Promise(r => setTimeout(r, ms));
const err = (msg, status, data) => Object.assign(new Error(msg), { status, data });

async function http(method, path, body) {
  const r = await fetch(path, {
    method, credentials: "same-origin",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined
  });
  let data = null; try { data = await r.json(); } catch {}
  if (!r.ok) throw err((data && data.error) || "Request failed (" + r.status + ")", r.status, data);
  return data;
}

const real = {
  login:   (email, password, remember) => http("POST", "/api/auth/login", { email, password, remember }),
  verify:  (challenge_token, code)     => http("POST", "/api/auth/verify", { challenge_token, code }),
  me:      ()                          => http("GET",  "/api/auth/me"),
  logout:  ()                          => http("POST", "/api/auth/logout"),
  overview:()                          => http("GET",  "/api/overview"),
};

const mock = (() => {
  let fails = 0, lockUntil = 0;
  const KEY = "cp_mock_session";
  const s = { players: 8, today: 7, week: 8, games: 155, cancelled: 0, appeals: 0, cpu: 47, load: 5.33, matches: 156, done: 156 };
  const gen = n => Array.from({ length: n }, () => Math.round(Math.random() * 12));
  const series = { "1h": gen(12), "24h": gen(24), "7d": [10, 9, 11, 10, 12, 11, 12] };
  return {
    async login(email, pw) {
      await wait(400);
      if (Date.now() < lockUntil) { const n = Math.ceil((lockUntil - Date.now()) / 1000); throw err("Too many attempts. Retry in " + n + "s.", 429, { retry_after: n }); }
      if (pw.length < 8) { if (++fails >= 5) { lockUntil = Date.now() + 30000; fails = 0; } throw err("Invalid email or password.", 401); }
      fails = 0; return { challenge: "mfa", challenge_token: "mock-" + Date.now() };
    },
    async verify(token, code) {
      await wait(400);
      if (!/^\d{6}$/.test(code)) throw err("Enter the 6-digit code.", 400);
      sessionStorage.setItem(KEY, "1"); return { ok: true };
    },
    async me() { await wait(60); if (!sessionStorage.getItem(KEY)) throw err("Unauthorized", 401); return { email: "operator@example.com" }; },
    async logout() { sessionStorage.removeItem(KEY); return null; },
    async overview() {
      await wait(60 + Math.random() * 120);
      if (!sessionStorage.getItem(KEY)) throw err("Unauthorized", 401);
      s.cpu = Math.max(20, Math.min(95, s.cpu + Math.round((Math.random() - .5) * 6)));
      s.load = +(s.load + (Math.random() - .5) * .4).toFixed(2);
      if (Math.random() < .5) { s.games++; s.matches++; s.done++; }
      const now = new Date().toISOString();
      return {
        sampled_at: now,
        players: { registered: s.players, today: s.today, week: s.week },
        games: { today: s.games, completed: s.games, cancelled: s.cancelled },
        appeals: { pending: s.appeals },
        db: { uptime_pct: 100, avg_ms: 444, latency_ms: 100 + Math.round(Math.random() * 40), failed_checks_24h: 0, sampled_at: now },
        host: { cpu_pct: s.cpu, mem_gb: 0.1, mem_max_gb: 0.5, load: s.load, sampled_at: now },
        population: { humans: 8, bots: 12, accounts: 20, matches: s.matches, completed: s.done, private: 2, cancelled: s.cancelled, since: "2026-10-17" },
        bots: { active: 12, expected: 12, played_1h: 6, played_24h: 12, played_7d: 12, last_match_minutes_ago: 6, matches_24h: 152, bot_share_pct_7d: 99, series }
      };
    }
  };
})();

window.Api = cfg.mock ? mock : real;
})();
