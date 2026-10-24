"""Oddcall Map: application factory and CLI."""
import datetime as dt
import logging
import click
from flask import Flask, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix
from .config import Config, TestConfig
from . import db as _db


def create_app(config=None):
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config.from_object(config or Config)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.permanent_session_lifetime = dt.timedelta(days=app.config["SESSION_TTL_DAYS"])
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      SESSION_COOKIE_SECURE=app.config["SITE_URL"].startswith("https"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    _db.init_app(app)

    from .routers import public, submit, hotline, mod
    app.register_blueprint(public.bp)
    app.register_blueprint(submit.bp)
    app.register_blueprint(hotline.bp)
    app.register_blueprint(mod.bp)

    @app.get("/media/<path:name>")
    def media(name):
        # Caddy serves this in production; this route covers local dev.
        return send_from_directory(app.config["MEDIA_DIR"], name)

    @app.after_request
    def headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        # Squarespace embeds the map in an iframe on the hotline page; everyone else is refused.
        resp.headers.setdefault("Content-Security-Policy",
                                "frame-ancestors 'self' https://appalachianparanormal.org https://*.squarespace.com")
        return resp

    @app.template_filter("nice_date")
    def nice_date(v, precision=None):
        if not v:
            return "not recorded"
        try:
            if len(v) == 4:
                return v
            if len(v) == 7:
                return dt.date.fromisoformat(v + "-01").strftime("%B %Y")
            return dt.date.fromisoformat(v[:10]).strftime("%b %-d, %Y")
        except ValueError:
            return v

    register_cli(app)
    return app


def register_cli(app):
    @app.cli.command("import-crawler")
    @click.argument("path")
    @click.option("--table", default="reports", help="table name when PATH is a SQLite file")
    @click.option("--source", default=None, help="default source name when rows lack one")
    def import_crawler(path, table, source):
        """Import strangeness-crawler CSV / GeoJSON / SQLite output."""
        from .importers.crawler import rows_from_file, import_rows
        res = import_rows(rows_from_file(path, table=table), default_source=source, actor="cli")
        click.echo(f"inserted {res['inserted']}, skipped {res['skipped']}")

    @app.cli.command("add-moderator")
    @click.argument("email")
    @click.option("--name", default=None)
    def add_moderator(email, name):
        d = _db.get_db()
        if d.pg:
            d.execute("INSERT INTO moderators (email, name, created_at) VALUES (?,?,?) ON CONFLICT (email) DO NOTHING", (email.lower(), name, _db.now()))
        else:
            d.execute("INSERT OR IGNORE INTO moderators (email, name, created_at) VALUES (?,?,?)", (email.lower(), name, _db.now()))
        d.commit()
        click.echo(f"moderator {email} added; they can request a magic link at /mod/login")

    @app.cli.command("purge")
    def purge():
        """Retention: rejected reports older than 90 days, stale hotline sessions, old rate-limit rows."""
        d = _db.get_db()
        cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=90)).isoformat()
        d.execute("DELETE FROM reports WHERE status='rejected' AND submitted_at < ?", (cutoff,))
        d.execute("DELETE FROM hotline_sessions WHERE updated_at < ?", ((dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)).isoformat(),))
        d.execute("DELETE FROM rate_limits WHERE at < ?", ((dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat(),))
        d.commit()
        click.echo("purged")

    @app.cli.command("stats")
    def stats():
        d = _db.get_db()
        for row in d.query("SELECT status, COUNT(*) AS n FROM reports GROUP BY status"):
            click.echo(f"{row['status']:10} {row['n']}")
        for row in d.query("SELECT state, COUNT(*) AS n FROM reports WHERE status='approved' GROUP BY state ORDER BY n DESC"):
            click.echo(f"  {row['state'] or '--'} {row['n']}")
