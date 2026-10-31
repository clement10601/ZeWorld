"""導演測試，全部用 FakeLLM，不需要模型。執行：python -m pytest director"""
import copy
import json
import threading
import urllib.request
from pathlib import Path

import pytest

from director._validate import validate
from director.core import Director, DirectorError, extract_json
from director.llm import FakeLLM
from director.profile import build_profile
from director.server import make_server

FIX = Path(__file__).resolve().parent.parent / "content-pack" / "fixtures"
GAME = validate.load_game(FIX / "zeworld.json")
REPLY = (FIX / "director" / "bandit_grudge.json").read_text(encoding="utf-8")
RESCUE = json.loads((FIX / "packs" / "rescue.json").read_text(encoding="utf-8"))
STATE = {
    "day": 6, "time_of_day": "day", "tier_index": 0, "flags": {"helped_stranger": True},
    "karma": {"order": 12, "mercy": -3}, "relations": {"npc.stranger": 20},
    "memories": {"npc.stranger": ["You stood between me and the bandits."]},
    "kills": {"bandit": 2}, "seen_packs": {"pack.test.rescue": 3},
    "followups": [{"hint": "The bandit gang holds a grudge.", "after_days": 5,
                   "from_pack": "pack.test.rescue", "day": 3, "flag": "followup:pack.test.rescue"}],
}


def test_extract_json_tolerates_fences_and_chatter():
    assert extract_json('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_profile_reads_generic_state():
    p = build_profile(STATE, GAME["ruleset"])
    assert p["tier"] == "novice"
    assert p["karma"][0] == {"axis": "order", "value": 12, "leaning": "+order"}
    assert p["karma"][1]["leaning"] is None
    assert p["npcs"][0]["memories"] == ["You stood between me and the bandits."]
    assert p["total_kills"] == 2


def test_generate_followup_pack(tmp_path):
    llm = FakeLLM(director=[REPLY])
    pack = Director(GAME, llm, out_dir=tmp_path).generate(STATE, "save.7", STATE["followups"][0])
    assert pack["id"].startswith("pack.gen.bandit_grudge.")
    assert pack["schema_version"] == "0.2"
    assert pack["meta"]["personalized_for"] == "save.7"
    assert pack["meta"]["source_model"] == "fake-director"
    assert pack["trigger"]["once"] is True
    assert pack["trigger"]["all"][:2] == [
        {"type": "flag", "key": "followup:pack.test.rescue", "equals": True},
        {"type": "days_since_flag", "key": "followup:pack.test.rescue", "days": 5}]
    assert (tmp_path / (pack["id"] + ".json")).exists()
    # 提示詞裡有素材目錄、側寫與後續請求
    prompt = json.dumps(llm.calls[0][1], ensure_ascii=False)
    assert "chunk.test_woods" in prompt and "The bandit gang holds a grudge." in prompt
    assert "pack.test.rescue" in prompt


def test_invalid_reply_is_sent_back_for_repair():
    bad = json.loads(REPLY)
    bad["map"]["chunks"][0]["chunk"] = "chunk.volcano"
    llm = FakeLLM(director=["not json at all", json.dumps(bad), REPLY])
    pack = Director(GAME, llm).generate(STATE)
    assert pack["id"].startswith("pack.gen.")
    assert len(llm.calls) == 3
    repair = llm.calls[2][1][-1]["content"]
    assert "chunk.volcano" in repair


def test_gives_up_after_retries():
    llm = FakeLLM(director=["{}"] * 3)
    with pytest.raises(DirectorError) as e:
        Director(GAME, llm, max_retries=2).generate(STATE)
    assert e.value.errors and len(llm.calls) == 3


def test_generated_ids_do_not_collide(tmp_path):
    d = Director(GAME, FakeLLM(director=[REPLY, REPLY]), out_dir=tmp_path)
    a, b = d.generate(STATE), d.generate(STATE)
    assert a["id"] != b["id"]


def test_rewrite_line_and_fallbacks():
    d = Director(GAME, FakeLLM(dialogue=["「Thank you again, friend.」", "", "x" * 500]))
    text = RESCUE["dialogue"]["nodes"]["a1"]["text"]
    speaker_node = "a1"
    r = d.rewrite_line(text, speaker_node, RESCUE, STATE)
    assert r == {"text": "Thank you again, friend.", "fallback": False}
    assert d.rewrite_line(text, speaker_node, RESCUE, STATE)["fallback"] is True
    assert d.rewrite_line(text, speaker_node, RESCUE, STATE)["text"] == text
    # LLM 掛掉也退回原句
    assert Director(GAME, FakeLLM()).rewrite_line(text, speaker_node, RESCUE, STATE)["text"] == text


def test_http_server_round_trip():
    d = Director(GAME, FakeLLM(director=[REPLY, "{}", "{}", "{}"], dialogue=lambda m: "Well met."))
    server = make_server(d, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}/v1"

    def post(path, body):
        req = urllib.request.Request(base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    try:
        with urllib.request.urlopen(base + "/health", timeout=5) as r:
            assert json.loads(r.read())["game"] == "zeworld-fixture"
        code, body = post("/packs/generate", {"state": STATE, "save_id": "s", "followup": STATE["followups"][0]})
        assert code == 200 and body["pack"]["meta"]["personalized_for"] == "s"
        code, body = post("/packs/generate", {"state": STATE})
        assert code == 422 and body["details"]
        code, body = post("/dialogue/rewrite", {"text": "Hello.", "node_id": "a1", "pack": RESCUE, "state": STATE})
        assert code == 200 and body["text"] == "Well met."
    finally:
        server.shutdown()
