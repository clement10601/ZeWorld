"""導演 HTTP 服務（只用標準函式庫）。遊戲端的 director_client.gd 連這裡。

POST /v1/packs/generate   {"state": {...}, "save_id": "...", "followup": {...}?, "hint": "..."?, "region": "..."?,
                           "tag": "..."?, "gate": [condition, ...]?, "expires_after_days": n?}
                          -> 200 {"pack": {...}} | 422 {"error": "...", "details": [...]} | 502 LLM 失敗
POST /v1/dialogue/rewrite {"text": "...", "node_id": "...", "pack": {...}, "state": {...}}
                          -> 200 {"text": "...", "fallback": bool}
POST /v1/judge/rank       {"state": {...}, "packs": [pack, ...], "moment": {...}?}
                          -> 200 {"scores": {pack_id: {"fit": 0-4, "confidence": 0-1}}, "model": "..."}
                          | 503 沒設決策模型 | 502 決策模型失敗
GET  /v1/health           -> 200 {"ok": true, "game": "...", "auth": bool}

給了 access（director/access.py 的 AccessCodes）時，POST 要帶 Authorization: Bearer <存取碼>，
沒帶或無效回 401，超過當日上限回 429。生成算 packs、台詞改寫算 lines、評分算 judges。
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .access import AccessDenied
from .core import DirectorError
from .judge import JudgeError, rank

MAX_BODY = 2 * 1024 * 1024
LOOPBACK = ("127.0.0.1", "localhost", "::1")


ROUTES = {"/v1/packs/generate": "packs", "/v1/dialogue/rewrite": "lines", "/v1/judge/rank": "judges"}


def make_server(director, host="127.0.0.1", port=8765, access=None, judge=None):
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
                self._send(200, {"ok": True, "game": director.game["name"], "auth": access is not None,
                                 "judge": judge is not None})
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
            if self.path not in ROUTES:
                return self._send(404, {"error": "not found"})
            if self.path == "/v1/judge/rank" and judge is None:
                return self._send(503, {"error": "no decision model configured"})
            if not self._charge(ROUTES[self.path]):
                return
            if self.path == "/v1/judge/rank":
                try:
                    scores = rank(judge, body.get("state", {}), director.game["ruleset"],
                                  body.get("packs", []), body.get("moment"))
                except JudgeError as e:
                    return self._send(502, {"error": str(e)})
                return self._send(200, {"scores": scores, "model": judge.model})
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
