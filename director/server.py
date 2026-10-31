"""導演 HTTP 服務（只用標準函式庫）。遊戲端的 director_client.gd 連這裡。

POST /v1/packs/generate   {"state": {...}, "save_id": "...", "followup": {...}?, "hint": "..."?, "region": "..."?,
                           "tag": "..."?, "gate": [condition, ...]?, "expires_after_days": n?}
                          -> 200 {"pack": {...}} | 422 {"error": "...", "details": [...]} | 502 LLM 失敗
POST /v1/dialogue/rewrite {"text": "...", "node_id": "...", "pack": {...}, "state": {...}}
                          -> 200 {"text": "...", "fallback": bool}
GET  /v1/health           -> 200 {"ok": true, "game": "...", "auth": bool}

給了 access（director/access.py 的 AccessCodes）時，POST 要帶 Authorization: Bearer <存取碼>，
沒帶或無效回 401，超過當日上限回 429。生成算 packs、台詞改寫算 lines。
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .access import AccessDenied
from .core import DirectorError

MAX_BODY = 2 * 1024 * 1024
LOOPBACK = ("127.0.0.1", "localhost", "::1")


def make_server(director, host="127.0.0.1", port=8765, access=None):
    if access is None and host not in LOOPBACK:
        raise ValueError("serving beyond localhost needs access codes (--access-codes)")

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/v1/health":
                self._send(200, {"ok": True, "game": director.game["name"], "auth": access is not None})
            else:
                self._send(404, {"error": "not found"})

        def _charge(self, kind):
            if access is None:
                return True
            auth = self.headers.get("Authorization", "")
            code = auth[7:] if auth.startswith("Bearer ") else ""
            try:
                self.tester = access.charge(code, kind)
            except AccessDenied as e:
                self._send(e.status, {"error": str(e)})
                return False
            return True

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length > MAX_BODY:
                return self._send(413, {"error": "body too large"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._send(400, {"error": "body is not JSON"})
            if self.path not in ("/v1/packs/generate", "/v1/dialogue/rewrite"):
                return self._send(404, {"error": "not found"})
            if not self._charge("packs" if self.path == "/v1/packs/generate" else "lines"):
                return
            if self.path == "/v1/packs/generate":
                try:
                    pack = director.generate(body.get("state", {}), body.get("save_id", ""),
                                             body.get("followup"), body.get("hint"), body.get("region"),
                                             body.get("tag"), body.get("gate"), body.get("expires_after_days"))
                except DirectorError as e:
                    code = 422 if e.errors else 502
                    return self._send(code, {"error": str(e), "details": e.errors})
                return self._send(200, {"pack": pack})
            self._send(200, director.rewrite_line(body.get("text", ""), body.get("node_id", ""),
                                                  body.get("pack", {}), body.get("state", {})))

        def log_message(self, fmt, *args):
            tester = getattr(self, "tester", "")
            print("director:", f"[{tester}]" if tester else "", fmt % args, flush=True)

    return ThreadingHTTPServer((host, port), Handler)
