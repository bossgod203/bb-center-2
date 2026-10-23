import os
import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

from . import db

EXPECTED_BOTS = 12
_stop = threading.Event()
_thread = None
_cpu_lock = threading.Lock()
_previous_cpu = None
_host = None


def now():
    return datetime.now(timezone.utc)


def iso(value):
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _cpu_ticks():
    try:
        with open("/proc/stat", encoding="ascii") as source:
            parts = source.readline().split()[1:]
        values = [int(x) for x in parts]
        return sum(values[:8]), values[3] + (values[4] if len(values) > 4 else 0)
    except (OSError, ValueError, IndexError):
        return None


def host_sample():
    global _previous_cpu
    cpu = 0
    current = _cpu_ticks()
    with _cpu_lock:
        if current and _previous_cpu:
            total_delta = current[0] - _previous_cpu[0]
            idle_delta = current[1] - _previous_cpu[1]
            if total_delta > 0:
                cpu = round(max(0, min(100, 100 * (total_delta - idle_delta) / total_delta)))
        _previous_cpu = current
    mem = 0.0
    mem_max = 0.0
    try:
        values = {}
        with open("/proc/meminfo", encoding="ascii") as source:
            for line in source:
                key, value = line.split(":", 1)
                values[key] = int(value.strip().split()[0])
        mem = round((values.get("MemTotal", 0) - values.get("MemAvailable", 0)) / 1024 / 1024, 1)
        mem_max = round(values.get("MemTotal", 0) / 1024 / 1024, 1)
    except (OSError, ValueError):
        pass
    try:
        load = round(os.getloadavg()[0], 2)
    except (OSError, AttributeError):
        load = 0
    return {"cpu_pct": cpu, "mem_gb": mem, "mem_max_gb": mem_max, "load": load, "sampled_at": iso(now())}


def sample_once():
    started = time.perf_counter()
    ok = 1
    try:
        db.get_connection().execute("SELECT 1").fetchone()
    except sqlite3.Error:
        ok = 0
    latency = max(1, round((time.perf_counter() - started) * 1000))
    connection = db.get_connection()
    connection.execute("INSERT INTO db_samples(at, ok, latency_ms) VALUES (?, ?, ?)", (iso(now()), ok, latency))
    cutoff = iso(now() - timedelta(days=7))
    connection.execute("DELETE FROM db_samples WHERE at < ?", (cutoff,))
    connection.commit()
    return latency, ok


def _run():
    global _host
    try:
        while not _stop.wait(5):
            _host = host_sample()
            try:
                sample_once()
            except sqlite3.Error:
                logging.exception("Could not persist database sample")
                db.close_connection()
    finally:
        db.close_connection()


def start_sampler():
    global _thread, _host
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    sample_once()
    _host = host_sample()
    _thread = threading.Thread(target=_run, name="control-plane-sampler", daemon=True)
    _thread.start()


def stop_sampler():
    _stop.set()
    if _thread:
        _thread.join(timeout=2)


def _count(connection, query, params=()):
    return connection.execute(query, params).fetchone()[0]


def _bot_series(connection, current):
    series = {}
    windows = [("1h", timedelta(hours=1), 12, timedelta(minutes=5)), ("24h", timedelta(days=1), 24, timedelta(hours=1)), ("7d", timedelta(days=7), 7, timedelta(days=1))]
    bot_match = "SELECT DISTINCT m.id, m.started_at FROM matches m JOIN match_players mp ON mp.match_id=m.id JOIN accounts a ON a.id=mp.account_id AND a.kind='bot' WHERE m.started_at >= ? AND m.started_at <= ?"
    for name, duration, buckets, step in windows:
        bucket_seconds = int(step.total_seconds())
        current_bucket = datetime.fromtimestamp(int(current.timestamp()) // bucket_seconds * bucket_seconds, timezone.utc)
        start = current_bucket - step * (buckets - 1)
        rows = connection.execute(bot_match, (iso(start), iso(current))).fetchall()
        values = [0] * buckets
        for row in rows:
            timestamp = datetime.fromisoformat(row["started_at"].replace("Z", "+00:00"))
            index = int((timestamp - start).total_seconds() // step.total_seconds())
            if 0 <= index < buckets:
                values[index] += 1
        series[name] = values
    return series


def overview():
    connection = db.get_connection()
    current = now()
    day = current.replace(hour=0, minute=0, second=0, microsecond=0)
    week = current - timedelta(days=7)
    hour = current - timedelta(hours=1)
    day_ago = current - timedelta(days=1)
    seven_days = current - timedelta(days=7)
    human_filter = "kind='human' AND deleted_at IS NULL"
    registered = _count(connection, f"SELECT COUNT(*) FROM accounts WHERE {human_filter}")
    today_players = _count(connection, f"SELECT COUNT(*) FROM accounts WHERE {human_filter} AND created_at >= ?", (iso(day),))
    week_players = _count(connection, f"SELECT COUNT(*) FROM accounts WHERE {human_filter} AND created_at >= ?", (iso(week),))
    games_today = _count(connection, "SELECT COUNT(*) FROM matches WHERE started_at >= ?", (iso(day),))
    completed_today = _count(connection, "SELECT COUNT(*) FROM matches WHERE started_at >= ? AND status='completed'", (iso(day),))
    cancelled_today = _count(connection, "SELECT COUNT(*) FROM matches WHERE started_at >= ? AND status='cancelled'", (iso(day),))
    pending = _count(connection, "SELECT COUNT(*) FROM appeals WHERE status='pending'")
    samples = connection.execute("SELECT ok, latency_ms FROM db_samples WHERE at >= ? ORDER BY at", (iso(day_ago),)).fetchall()
    latest = connection.execute("SELECT latency_ms, at FROM db_samples ORDER BY at DESC LIMIT 1").fetchone()
    db_total = len(samples)
    db_ok = sum(row["ok"] for row in samples)
    avg = int(sum(row["latency_ms"] for row in samples) / db_total) if db_total else 0
    db_data = {"uptime_pct": round(100 * db_ok / db_total, 1) if db_total else 100.0, "avg_ms": avg, "latency_ms": int(latest["latency_ms"]) if latest else 0, "failed_checks_24h": db_total - db_ok, "sampled_at": latest["at"] if latest else iso(current)}
    host = _host or {"cpu_pct": 0, "mem_gb": 0.0, "mem_max_gb": 0.0, "load": 0.0, "sampled_at": iso(current)}
    humans = _count(connection, "SELECT COUNT(*) FROM accounts WHERE kind='human' AND deleted_at IS NULL")
    bots = _count(connection, "SELECT COUNT(*) FROM accounts WHERE kind='bot' AND deleted_at IS NULL")
    matches = _count(connection, "SELECT COUNT(*) FROM matches")
    completed = _count(connection, "SELECT COUNT(*) FROM matches WHERE status='completed'")
    private = _count(connection, "SELECT COUNT(*) FROM matches WHERE is_private=1")
    cancelled = _count(connection, "SELECT COUNT(*) FROM matches WHERE status='cancelled'")
    earliest = connection.execute("SELECT MIN(started_at) FROM matches").fetchone()[0]
    bot_join = "JOIN match_players mp ON mp.match_id=m.id JOIN accounts a ON a.id=mp.account_id AND a.kind='bot'"
    def distinct_bots(since):
        return _count(connection, f"SELECT COUNT(DISTINCT a.id) FROM matches m {bot_join} WHERE m.started_at >= ?", (iso(since),))
    played_1h, played_24h, played_7d = distinct_bots(hour), distinct_bots(day_ago), distinct_bots(seven_days)
    matches_24h = _count(connection, f"SELECT COUNT(DISTINCT m.id) FROM matches m {bot_join} WHERE m.started_at >= ?", (iso(day_ago),))
    bot_7d = _count(connection, f"SELECT COUNT(DISTINCT m.id) FROM matches m {bot_join} WHERE m.started_at >= ?", (iso(seven_days),))
    all_7d = _count(connection, "SELECT COUNT(*) FROM matches WHERE started_at >= ?", (iso(seven_days),))
    latest_bot = connection.execute(f"SELECT MAX(m.started_at) FROM matches m {bot_join}").fetchone()[0]
    minutes = max(0, int((current - datetime.fromisoformat(latest_bot.replace("Z", "+00:00"))).total_seconds() // 60)) if latest_bot else 0
    return {"sampled_at": iso(current), "players": {"registered": registered, "today": today_players, "week": week_players}, "games": {"today": games_today, "completed": completed_today, "cancelled": cancelled_today}, "appeals": {"pending": pending}, "db": db_data, "host": host, "population": {"humans": humans, "bots": bots, "accounts": _count(connection, "SELECT COUNT(*) FROM accounts"), "matches": matches, "completed": completed, "private": private, "cancelled": cancelled, "since": earliest[:10] if earliest else current.date().isoformat()}, "bots": {"active": bots, "expected": EXPECTED_BOTS, "played_1h": played_1h, "played_24h": played_24h, "played_7d": played_7d, "last_match_minutes_ago": minutes, "matches_24h": matches_24h, "bot_share_pct_7d": round(100 * bot_7d / all_7d) if all_7d else 0, "series": _bot_series(connection, current)}}
