(() => {
"use strict";
const $ = (t, a = {}, ...k) => {
  const e = document.createElement(t);
  for (const [n, v] of Object.entries(a)) {
    if (n === "class") e.className = v;
    else if (n.startsWith("on")) e.addEventListener(n.slice(2), v);
    else e.setAttribute(n, v);
  }
  k.flat().forEach(c => e.append(c));
  return e;
};
const utc = d => d.toISOString().slice(11, 19);
const NAV = [["◈", "Command Center"], ["◎", "Players"], ["◇", "Appeals"], ["≡", "Matches"], ["↗", "Bots"], ["~", "Server"]];
const WIN = { "1h": "−1h", "24h": "−24h", "7d": "−7d" };
let page = "Command Center", tab = "24h", snap = null;
const view = document.getElementById("view");

function go(p) { page = p; renderNav(); render(); }

function renderNav() {
  const n = document.getElementById("nav");
  n.replaceChildren(
    ...NAV.map(([ic, l]) => $("button", { class: "i", "aria-label": l, title: l, ...(l === page ? { "aria-current": "page" } : {}), onclick: () => go(l) }, ic)),
    $("button", { class: "i out", "aria-label": "Sign out", title: "Sign out", onclick: signOut }, "⏻"),
    $("div", { class: "av" }, "CM")
  );
}

async function signOut() {
  try { await Api.logout(); } catch {}
  location.replace("login.html");
}

const Stat = (l, ic, v, sub) => $("div", { class: "card stat" }, $("div", { class: "lbl" }, l), $("span", { class: "ic" }, ic), $("div", { class: "v" }, v), sub);
const Cell = (l, v, s, g) => $("div", {}, $("div", { class: "lbl" }, l), $("b", {}, String(v)), $("span", { class: g ? "g" : "" }, s));
const clock = iso => utc(new Date(iso));

function render() {
  document.getElementById("crumb").textContent = "Control Plane / " + page;
  if (page !== "Command Center") {
    view.replaceChildren($("div", { class: "card empty" }, $("h1", {}, page), "This section is outside the current scope. Return to Command Center."));
    return;
  }
  if (!snap) { view.replaceChildren($("div", { class: "card empty" }, "Loading…")); return; }

  const d = snap, b = d.bots, h = d.host, db = d.db, p = d.population;
  const ok = db.uptime_pct >= 99.9 && b.active >= b.expected && h.cpu_pct < 85;
  const healthy = b.active >= b.expected;
  const cancelPct = p.matches ? (p.cancelled / p.matches * 100).toFixed(1) : "0.0";

  view.replaceChildren(
    $("div", { class: "card hero" },
      $("div", {},
        $("div", { class: "lbl " + (ok ? "ok" : "warn") }, $("span", { class: "dot", style: ok ? "" : "background:var(--amber)" }), ok ? "All systems nominal" : "Attention required"),
        $("h1", {}, "Game operations, at a glance."),
        $("p", {}, "Database, server resources, player activity, and moderation work in one view.")),
      $("div", { class: "meta" },
        ...[["DB sample", clock(db.sampled_at)], ["Resource sample", clock(h.sampled_at)], ["Latency", db.latency_ms + " ms"]]
          .map(([a, v]) => $("div", {}, $("div", { class: "lbl" }, a), $("b", { style: a === "Latency" ? "color:var(--g)" : "" }, v))))),
    $("div", { class: "stats" },
      Stat("Registered players", "↗", d.players.registered, $("div", { class: "s" }, $("b", {}, "+" + d.players.today + " today"), " · " + d.players.week + " this week")),
      Stat("Games today", "○", d.games.today, $("div", { class: "s" }, $("b", {}, d.games.completed + " completed"), " · " + d.games.cancelled + " cancelled")),
      Stat("Pending appeals", "◇", String(d.appeals.pending).padStart(2, "0"), $("a", { href: "#", onclick: e => { e.preventDefault(); go("Appeals"); } }, "Open queue →")),
      Stat("DB uptime / 24h", "●", [db.uptime_pct.toFixed(2), $("small", {}, "%")], $("div", { class: "s" }, $("b", {}, db.avg_ms + " ms average"), " · " + (db.failed_checks_24h ? db.failed_checks_24h + " failed checks" : "no incidents"))),
      Stat("Host CPU / Memory", "~", [String(h.cpu_pct), $("small", {}, "%")], $("div", { class: "s" }, $("b", {}, h.mem_gb + " / " + h.mem_max_gb + " GB"), " · load " + h.load))),
    $("div", { class: "cols" },
      $("div", { class: "card pn" },
        $("div", { class: "top" }, $("span", { class: "lbl" }, "Population"), $("span", { class: "lbl" }, "Counts as of " + clock(d.sampled_at) + " UTC")),
        $("h3", {}, "Players, bots & matches"),
        $("div", { class: "grid" },
          Cell("Human players", p.humans, p.humans + " active", 1),
          Cell("Bots", p.bots, p.bots + " active", 1),
          Cell("All accounts", p.accounts, "humans + bots, incl. deleted"),
          Cell("Total matches", p.matches, "since " + p.since),
          Cell("Completed", p.completed, p.private + " private"),
          Cell("Cancelled", p.cancelled, cancelPct + "% of all"))),
      $("div", { class: "card pn" },
        $("div", { class: "top" }, $("span", { class: "lbl" }, "Bot health"), $("span", { class: "badge" + (healthy ? "" : " bad") }, healthy ? "HEALTHY" : "DEGRADED")),
        $("h3", {}, b.active + " of " + b.expected + " expected active"),
        $("div", { class: "tabs", role: "tablist" },
          ...[["1h", b.played_1h, "Played 1h"], ["24h", b.played_24h, "Played 24h"], ["7d", b.played_7d, "Played 7d"]]
            .map(([k, n, l]) => $("button", { role: "tab", "aria-selected": String(tab === k), onclick: () => { tab = k; render(); } }, $("b", {}, String(n)), $("span", {}, l)))),
        (() => { const s = b.series[tab] || [], m = Math.max(1, ...s);
          return $("div", { class: "chart", role: "img", "aria-label": "Bot matches, " + tab }, ...s.map(n => $("i", { style: "height:" + Math.round(n / m * 100) + "%" }))); })(),
        $("div", { class: "axis" }, ...[WIN[tab], "7 days · " + b.bot_share_pct_7d + "% of matches had a bot", "Now"].map(t => $("span", { class: "lbl" }, t))),
        $("div", { class: "foot" }, "Last bot match ", $("b", {}, b.last_match_minutes_ago + " min ago"), " · " + b.matches_24h + " in 24h"))));
}

async function tick() {
  if (document.hidden) return;
  const chip = document.getElementById("chip");
  try {
    snap = await Api.overview();
    chip.classList.remove("err");
    document.getElementById("sampled").textContent = clock(snap.sampled_at);
    render();
  } catch (e) {
    if (e.status === 401) { location.replace("login.html"); return; }
    chip.classList.add("err");
    document.getElementById("sampled").textContent = "error";
  }
}

document.getElementById("date").textContent =
  new Date().toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "UTC" }) +
  " · " + utc(new Date()).slice(0, 5) + " UTC";
renderNav(); render(); tick();
setInterval(tick, (window.CP_CONFIG && window.CP_CONFIG.pollMs) || 5000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) tick(); });
})();
