"""One tiny DB layer. SQLite for local work and tests, Postgres on the droplet.

Queries are written with `?` placeholders; on Postgres they are rewritten to `%s`.
Rows come back as dicts. Keep SQL portable: TEXT, REAL, INTEGER, no vendor functions.
"""
import sqlite3
import uuid
import datetime as dt
from pathlib import Path
from flask import current_app, g

SCHEMA = Path(__file__).with_name("schema.sql")


def new_id():
    return str(uuid.uuid4())


def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


class DB:
    def __init__(self, url):
        self.url = url
        self.pg = url.startswith("postgres")
        if self.pg:
            import psycopg  # type: ignore
            from psycopg.rows import dict_row  # type: ignore
            self.conn = psycopg.connect(url, row_factory=dict_row)
        else:
            path = url.replace("sqlite:///", "", 1)
            if path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.execute("PRAGMA journal_mode=WAL") if path != ":memory:" else None

    def _q(self, sql):
        return sql.replace("?", "%s") if self.pg else sql

    def execute(self, sql, params=()):
        cur = self.conn.execute(self._q(sql), params)
        return cur

    def query(self, sql, params=()):
        cur = self.execute(sql, params)
        rows = cur.fetchall()
        return [dict(r) for r in rows]

    def one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()

    def init_schema(self):
        sql = SCHEMA.read_text()
        if self.pg:
            with self.conn.cursor() as cur:
                cur.execute(sql)
        else:
            self.conn.executescript(sql)
        self.conn.commit()


_memory_db = None


def get_db():
    """One connection per request. The in-memory test DB is shared across requests."""
    global _memory_db
    if "db" not in g:
        url = current_app.config["DATABASE_URL"]
        if url == "sqlite:///:memory:":
            if _memory_db is None:
                _memory_db = DB(url)
                _memory_db.init_schema()
            g.db = _memory_db
        else:
            g.db = DB(url)
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None and db.url != "sqlite:///:memory:":
        db.close()


def reset_memory_db():
    global _memory_db
    _memory_db = None


def init_app(app):
    app.teardown_appcontext(close_db)
    with app.app_context():
        url = app.config["DATABASE_URL"]
        if url != "sqlite:///:memory:":
            db = DB(url)
            db.init_schema()
            db.close()
