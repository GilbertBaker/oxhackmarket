"""OxMarket Flask app: wiring for all three streams (owner B).

Run from the repo root:  python server/app.py
"""
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

SERVER_DIR = Path(__file__).resolve().parent
ROOT_DIR = SERVER_DIR.parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

load_dotenv(ROOT_DIR / ".env")


def _configure_logging():
    (SERVER_DIR / "logs").mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        root.addHandler(console)
        logfile = logging.FileHandler(SERVER_DIR / "logs" / "app.log", encoding="utf-8")
        logfile.setFormatter(fmt)
        root.addHandler(logfile)


_configure_logging()
log = logging.getLogger("app")

from flask import Flask, jsonify, request  # noqa: E402

import content  # noqa: E402
import content_api  # noqa: E402
import data_api  # noqa: E402
import db  # noqa: E402
import jobs  # noqa: E402
import trading  # noqa: E402
import trading_api  # noqa: E402


def _safe(label, fn, *args):
    """Run a startup step; log and carry on if another stream's code is broken."""
    try:
        return fn(*args)
    except Exception:
        log.exception("startup step failed: %s", label)
        return None


def create_app():
    app = Flask(__name__, static_folder=str(ROOT_DIR / "web"), static_url_path="")
    app.secret_key = os.environ.get("SECRET_KEY", "dev-insecure-secret")

    @app.route("/")
    def index():
        return app.send_static_file("index.html")

    _safe("db.ensure_schema", db.ensure_schema)
    _safe("db.ensure_bot_user", db.ensure_bot_user)

    app.register_blueprint(trading_api.bp)
    app.register_blueprint(data_api.bp)
    app.register_blueprint(content_api.bp)

    @app.errorhandler(404)
    def not_found(e):
        if request.path.startswith("/api/"):
            return jsonify(error="not found"), 404
        return e

    @app.errorhandler(500)
    def server_error(e):
        log.exception("unhandled error on %s", request.path)
        if request.path.startswith("/api/"):
            return jsonify(error="internal server error"), 500
        return e

    _safe("trading.startup", trading.startup)
    if os.environ.get("DISABLE_JOBS") != "1":
        _safe("trading.start_background", trading.start_background)
        _safe("jobs.start_background", jobs.start_background)
        _safe("content.start_background", content.start_background)
    else:
        log.info("DISABLE_JOBS=1: background jobs not started")
    return app


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    create_app().run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
