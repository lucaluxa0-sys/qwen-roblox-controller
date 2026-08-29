from __future__ import annotations

import argparse
import hmac
import json
import os
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

HOST = "127.0.0.1"
DEFAULT_PORT = 8766

LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
STATE_DIR = LOCALAPPDATA / "QwenRobloxEnforcedProxy"
TELEMETRY_DIR = STATE_DIR / "telemetry"
SECRET_FILE = STATE_DIR / "telemetry_secret.txt"

JSON_ENDPOINTS = {
    "status": TELEMETRY_DIR / "status.json",
    "latest-failure": TELEMETRY_DIR / "latest_failure.json",
    "controller-health": TELEMETRY_DIR / "controller_health.json",
    "test-results": TELEMETRY_DIR / "test_results.json",
}

JSONL_ENDPOINTS = {
    "actions": TELEMETRY_DIR / "action_history.jsonl",
    "failures": TELEMETRY_DIR / "failure_history.jsonl",
    "autopilot": TELEMETRY_DIR / "autopilot_runs.jsonl",
}

MAX_TAIL_ROWS = 100
MAX_RESPONSE_BYTES = 2_000_000


def load_secret() -> str:
    env_secret = os.environ.get("QWEN_TELEMETRY_SECRET", "").strip()
    if env_secret:
        return env_secret

    if SECRET_FILE.exists():
        value = SECRET_FILE.read_text(encoding="utf-8", errors="replace").strip()
        if value:
            return value

    return ""


def init_secret(force: bool = False) -> str:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if SECRET_FILE.exists() and not force:
        existing = SECRET_FILE.read_text(encoding="utf-8", errors="replace").strip()
        if existing:
            return existing

    value = secrets.token_urlsafe(48)
    tmp = SECRET_FILE.with_suffix(".tmp")
    tmp.write_text(value + "\n", encoding="utf-8")
    tmp.replace(SECRET_FILE)
    return value


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def tail_jsonl(path: Path, limit: int):
    if not path.exists():
        raise FileNotFoundError(path)

    limit = max(1, min(int(limit), MAX_TAIL_ROWS))
    # Telemetry files are intentionally small/bounded in V6. For robustness,
    # still cap how much data is read into memory.
    raw = path.read_bytes()
    if len(raw) > MAX_RESPONSE_BYTES * 4:
        raw = raw[-MAX_RESPONSE_BYTES * 4:]

    rows = []
    for line in raw.decode("utf-8", errors="replace").splitlines()[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            rows.append({"unparsed": line[:4000]})
    return rows


class Handler(BaseHTTPRequestHandler):
    server_version = "QwenTelemetry/1.0"

    def log_message(self, fmt, *args):
        # Do not print request URLs because they contain the secret path.
        return

    def _json(self, status: int, payload) -> None:
        raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        if len(raw) > MAX_RESPONSE_BYTES:
            raw = json.dumps(
                {"error": "response_too_large", "max_bytes": MAX_RESPONSE_BYTES},
                ensure_ascii=False,
            ).encode("utf-8")
            status = 507

        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        self._json(405, {"error": "read_only"})

    def do_PUT(self):
        self._json(405, {"error": "read_only"})

    def do_DELETE(self):
        self._json(405, {"error": "read_only"})

    def do_PATCH(self):
        self._json(405, {"error": "read_only"})

    def do_GET(self):
        parsed = urlsplit(self.path)
        parts = [x for x in parsed.path.split("/") if x]

        # Expected form: /api/<secret>/<endpoint>
        if len(parts) != 3 or parts[0] != "api":
            self._json(404, {"error": "not_found"})
            return

        supplied = parts[1]
        endpoint = parts[2]
        expected = self.server.telemetry_secret  # type: ignore[attr-defined]

        if not expected or not hmac.compare_digest(supplied, expected):
            self._json(404, {"error": "not_found"})
            return

        try:
            if endpoint == "ping":
                self._json(200, {
                    "ok": True,
                    "service": "qwen-roblox-telemetry",
                    "read_only": True,
                })
                return

            path = JSON_ENDPOINTS.get(endpoint)
            if path is not None:
                if not path.exists():
                    self._json(404, {"error": "telemetry_not_ready", "endpoint": endpoint})
                    return
                self._json(200, read_json(path))
                return

            path = JSONL_ENDPOINTS.get(endpoint)
            if path is not None:
                # Fixed small tail keeps remote reads cheap and bounded.
                self._json(200, {"endpoint": endpoint, "rows": tail_jsonl(path, 30)})
                return

            self._json(404, {"error": "unknown_endpoint"})
        except FileNotFoundError:
            self._json(404, {"error": "telemetry_not_ready", "endpoint": endpoint})
        except Exception as exc:
            self._json(500, {"error": "telemetry_read_failed", "detail": str(exc)[:500]})


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only HTTPS-tunnel origin for Qwen Roblox V6 telemetry.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--init-secret", action="store_true", help="Create a strong local secret if one does not exist.")
    parser.add_argument("--rotate-secret", action="store_true", help="Replace the local secret with a new random value.")
    parser.add_argument("--show-secret", action="store_true", help="Print the current secret to this local terminal.")
    ns = parser.parse_args(argv)

    if ns.rotate_secret:
        secret = init_secret(force=True)
    elif ns.init_secret:
        secret = init_secret(force=False)
    else:
        secret = load_secret()

    if not secret:
        print("No telemetry secret configured.", file=sys.stderr)
        print(f"Run: python {Path(__file__).name} --init-secret --show-secret", file=sys.stderr)
        return 2

    if ns.show_secret:
        print("TELEMETRY SECRET (keep private):")
        print(secret)
        print()
        print("Local test URL:")
        print(f"http://{HOST}:{ns.port}/api/{secret}/ping")
        if ns.init_secret or ns.rotate_secret:
            return 0

    TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)

    server = ThreadingHTTPServer((HOST, ns.port), Handler)
    server.telemetry_secret = secret  # type: ignore[attr-defined]

    print(f"Qwen telemetry server listening on http://{HOST}:{ns.port}")
    print("Read-only origin. Do not bind this directly to 0.0.0.0.")
    print("Use Cloudflare Tunnel to provide the public HTTPS URL.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping telemetry server.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())