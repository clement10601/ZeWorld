"""Godot 導演用戶端 ↔ Python 導演服務的端到端測試。找不到 Godot 時跳過（設 GODOT=路徑 指定）。"""
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from director._validate import validate
from director.access import AccessCodes
from director.core import Director
from director.judge import FakeJudge
from director.llm import FakeLLM
from director.server import make_server

ROOT = Path(__file__).resolve().parent.parent
GODOT = os.environ.get("GODOT") or shutil.which("godot")


@pytest.mark.skipif(not GODOT, reason="Godot not found; set GODOT=/path/to/godot")
def test_godot_client_talks_to_director(tmp_path):
    game = validate.load_game(ROOT / "content-pack" / "fixtures" / "zeworld.json")
    reply = (ROOT / "content-pack" / "fixtures" / "director" / "bandit_grudge.json").read_text(encoding="utf-8")
    access = AccessCodes(tmp_path / "codes.json")
    code = access.add("e2e")
    server = make_server(Director(game, FakeLLM(director=lambda m: reply, dialogue=lambda m: "Well met again.")),
                         port=0, access=access, judge=FakeJudge({}))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        env = dict(os.environ, ZEWORLD_DIRECTOR_URL=f"http://127.0.0.1:{server.server_port}",
                   ZEWORLD_DIRECTOR_CODE=code)
        out = subprocess.run([GODOT, "--headless", "--path", str(ROOT / "godot"), "-s", "res://tests/director_e2e.gd"],
                             env=env, capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stdout + out.stderr
        assert "director e2e: 0 failed" in out.stdout
    finally:
        server.shutdown()
