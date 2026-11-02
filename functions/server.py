"""Loopback-only HTTP transport for the local game table."""

from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import secrets
import threading
from urllib.parse import urlsplit
import webbrowser

from assets.asset_import_manager import AssetError, Catalog
from classes.game import RuleError
from classes.match import Action, Match


LOGGER = logging.getLogger(__name__)
WEB_ROOT = Path(__file__).resolve().parents[1] / "web"
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


def _integer(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise RuleError(f"{name} must be a nonnegative integer")
    return value


def parse_action(raw: dict[str, object]) -> Action:
    kind = raw.get("kind")
    fields = {
        "play": {"kind", "card", "target"},
        "attack": {"kind", "source", "target"},
        "sacrifice": {"kind", "source"},
        "dismiss": {"kind", "source"},
        "end_turn": {"kind"},
        "ready": {"kind"},
    }
    if not isinstance(kind, str) or kind not in fields:
        raise RuleError("Unknown action")
    if set(raw) - fields[kind]:
        raise RuleError("Unexpected action fields")
    card = raw.get("card")
    if kind == "play" and (not isinstance(card, str) or not card):
        raise RuleError("Play requires a card ID")
    source = _integer(raw.get("source"), "source") if kind in {"attack", "sacrifice", "dismiss"} else None
    target = _integer(raw["target"], "target") if raw.get("target") is not None else None
    return Action(kind, card if isinstance(card, str) else None, source, target)


class GameServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, catalog: Catalog, port: int = 8765) -> None:
        self.match = Match(catalog)
        self.lock = threading.Lock()
        self.token = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), GameHandler)


class GameHandler(BaseHTTPRequestHandler):
    @property
    def table(self) -> GameServer:
        if not isinstance(self.server, GameServer):
            raise TypeError("GameHandler requires a GameServer")
        return self.server

    def _send(self, status: int, content: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
        )
        self.end_headers()
        self.wfile.write(content)

    def _json(self, status: int, body: dict[str, object]) -> None:
        self._send(status, json.dumps(body).encode("utf-8"), "application/json; charset=utf-8")

    def _local_request(self) -> bool:
        port = self.table.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in hosts:
            self._json(403, {"error": "Use the local game URL printed in your terminal."})
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {f"http://{host}" for host in hosts}:
            self._json(403, {"error": "Cross-origin game requests are not allowed."})
            return False
        return True

    def do_GET(self) -> None:
        if not self._local_request():
            return
        path = urlsplit(self.path).path
        if path == "/api/state":
            with self.table.lock:
                body = self.table.match.state()
                body["token"] = self.table.token
            self._json(200, body)
        elif path in STATIC:
            name, content_type = STATIC[path]
            self._send(200, (WEB_ROOT / name).read_bytes(), content_type)
        elif path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
        else:
            self._json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        if not self._local_request():
            return
        token = self.headers.get("X-Azelia-Token", "")
        if not secrets.compare_digest(token, self.table.token):
            self._json(403, {"error": "Refresh the page before changing the game."})
            return
        path = urlsplit(self.path).path
        if path not in {"/api/start", "/api/action"}:
            self._json(404, {"error": "Not found"})
            return
        if self.headers.get_content_type() != "application/json":
            self._json(415, {"error": "Expected application/json"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(400, {"error": "Invalid request length"})
            return
        if not 0 < length <= 8192:
            self._json(413, {"error": "Request body must be between 1 and 8192 bytes"})
            return
        try:
            raw: object = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "Invalid JSON request"})
            return
        if not isinstance(raw, dict):
            self._json(400, {"error": "Expected a JSON object"})
            return
        with self.table.lock:
            try:
                frames: list[dict[str, object]] = []
                revision = _integer(raw.get("revision"), "revision")
                if path == "/api/start":
                    if set(raw) != {"mode", "first", "second", "revision"}:
                        raise RuleError("Choose a mode and two starter decks")
                    mode, first, second = raw["mode"], raw["first"], raw["second"]
                    if not all(isinstance(value, str) for value in (mode, first, second)):
                        raise RuleError("Deck selections and mode must be text")
                    if revision != self.table.match.revision:
                        raise RuleError("This table changed. Refresh before starting a new duel.")
                    self.table.match.start(str(mode), str(first), str(second))
                else:
                    if set(raw) != {"action", "revision"} or not isinstance(raw.get("action"), dict):
                        raise RuleError("Supply an action and the current revision")
                    frames = self.table.match.act(parse_action(raw["action"]), revision)
            except (RuleError, AssetError) as error:
                self._json(409, {"error": str(error), "state": self.table.match.state()})
            else:
                body = self.table.match.state()
                body["frames"] = frames
                self._json(200, body)

    def log_message(self, format: str, *args: object) -> None:
        LOGGER.info("%s %s", self.address_string(), format % args)


def serve(catalog: Catalog, port: int = 8765, open_browser: bool = True) -> None:
    with GameServer(catalog, port) as server:
        url = f"http://127.0.0.1:{server.server_port}"
        print(f"\nHeroes of Azelia is ready at {url}\nPress Ctrl+C to stop.", flush=True)
        timer = None
        if open_browser:
            timer = threading.Timer(0.3, partial(webbrowser.open, url))
            timer.start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nThe local game server has stopped.")
        finally:
            if timer is not None:
                timer.cancel()
