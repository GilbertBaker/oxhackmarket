"""A tiny Python backend and static file server. No pip packages required."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
import json
import mimetypes

from conditions import conditions
from predict import forecasts, predict

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
HOST = "127.0.0.1"
PORT = 8000


class Handler(BaseHTTPRequestHandler):
    def respond(self, status, body, content_type="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path

        if path == "/api/health":
            return self.respond(200, {"ok": True, "message": "Python backend is running"})

        if path == "/api/conditions":
            return self.respond(200, conditions.snapshot())

        if path == "/api/flag":
            try:
                return self.respond(200, {"green": bool(predict())})
            except RuntimeError as error:
                return self.respond(503, {"error": str(error)})

        if path == "/api/forecast":
            return self.respond(200, forecasts.snapshot())

        if path == "/":
            path = "/index.html"

        # Resolve only files inside frontend/; never serve server.py or other project files.
        file = (FRONTEND / path.lstrip("/")).resolve()
        if not file.is_relative_to(FRONTEND.resolve()) or not file.is_file():
            return self.respond(404, {"error": "Not found"})

        mime_type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        if mime_type.startswith("text/") or mime_type == "application/javascript":
            mime_type += "; charset=utf-8"
        self.respond(200, file.read_bytes(), mime_type)

if __name__ == "__main__":
    conditions.start()
    forecasts.start()
    print(f"Open http://{HOST}:{PORT} in your browser. Press Ctrl+C to stop.", flush=True)
    try:
        ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
