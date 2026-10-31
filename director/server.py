"""導演 HTTP 服務（只用標準函式庫）。遊戲端的 director_client.gd 連這裡。

POST /v1/packs/generate   {"state": {...}, "save_id": "...", "followup": {...}?, "hint": "..."?, "region": "..."?}
                          -> 200 {"pack": {...}} | 422 {"error": "...", "details": [...]} | 502 LLM 失敗
POST /v1/dialogue/rewrite {"text": "...", "node_id": "...", "pack": {...}, "state": {...}}
                          -> 200 {"text": "...", "fallback": bool}
GET  /v1/health           -> 200 {"ok": true, "game": "..."}
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .core import DirectorError


def make_server(director, host="127.0.0.1", port=8765):
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
                self._send(200, {"ok": True, "game": director.game["name"]})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            except ValueError:
                return self._send(400, {"error": "body is not JSON"})
            if self.path == "/v1/packs/generate":
                try:
                    pack = director.generate(body.get("state", {}), body.get("save_id", ""),
                                             body.get("followup"), body.get("hint"), body.get("region"))
                except DirectorError as e:
                    code = 422 if e.errors else 502
                    return self._send(code, {"error": str(e), "details": e.errors})
                return self._send(200, {"pack": pack})
            if self.path == "/v1/dialogue/rewrite":
                return self._send(200, director.rewrite_line(body.get("text", ""), body.get("node_id", ""),
                                                             body.get("pack", {}), body.get("state", {})))
            self._send(404, {"error": "not found"})

        def log_message(self, fmt, *args):
            print("director:", fmt % args, flush=True)

    return ThreadingHTTPServer((host, port), Handler)
