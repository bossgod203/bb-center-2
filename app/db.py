import sqlite3
import threading
from pathlib import Path


_local = threading.local()
_db_path = None


def configure(path):
    global _db_path
    close_connection()
    _db_path = str(Path(path))


def initialize(path=None):
    if path is not None:
        configure(path)
    if _db_path is None:
        raise RuntimeError("database path is not configured")
    connection = sqlite3.connect(_db_path, timeout=30, check_same_thread=True)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(Path(__file__).with_name("schema.sql").read_text())
    connection.commit()
    connection.close()


def get_connection():
    connection = getattr(_local, "connection", None)
    if connection is None:
        if _db_path is None:
            raise RuntimeError("database path is not configured")
        connection = sqlite3.connect(_db_path, timeout=30, check_same_thread=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        _local.connection = connection
    return connection


def close_connection():
    connection = getattr(_local, "connection", None)
    if connection is not None:
        connection.close()
        _local.connection = None
