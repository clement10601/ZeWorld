"""導演測試，全部用 FakeLLM，不需要模型。執行：python -m pytest director"""
import copy
import json
import threading
import urllib.request
from pathlib import Path

import pytest

from director._validate import validate
from director.core import Director, DirectorError, clean_line, extract_json, previous_line
from director.llm import FakeLLM
from director import prompts
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


def test_clean_line_strips_narration_and_quotes():
    actor = {"name": "白芷"}
    assert clean_line("白芷冷笑一聲，眼神如刀：「這筆帳，青丘記著。", actor) == "這筆帳，青丘記著。"
    assert clean_line("厲崇冷聲道：交出狐妖。", {"name": "韓錚"}) == "交出狐妖。"
    assert clean_line("恩公，小心。\n（她退後一步）", actor) == "恩公，小心。"
    # 台詞本身有冒號、但前面不是講者或「道」就保留
    assert clean_line("記住一句話：人心難測。", actor) == "記住一句話：人心難測。"


def test_previous_line_is_sent_as_context():
    prev = previous_line(RESCUE, "a2")
    assert prev == {"speaker": "Stranger", "text": "You helped me before."}
    llm = FakeLLM(dialogue=["Thanks."])
    Director(GAME, llm).rewrite_line(RESCUE["dialogue"]["nodes"]["a2"]["text"], "a2", RESCUE, STATE)
    ctx = json.loads(llm.calls[0][1][-1]["content"])
    assert ctx.get("line_just_before") == prev
    assert list(ctx)[-1] == "authored_line"


ZH_GAME = dict(GAME, language="zh-Hant（繁體中文）")


def test_simplified_line_falls_back_in_traditional_game():
    d = Director(ZH_GAME, FakeLLM(dialogue=["这位道友，听说你来了。", "這位道友，聽說你來了。"]))
    text = RESCUE["dialogue"]["nodes"]["a1"]["text"]
    r = d.rewrite_line(text, "a1", RESCUE, STATE)
    assert r["fallback"] is True and r["text"] == text and "这" in r["reason"]
    assert d.rewrite_line(text, "a1", RESCUE, STATE)["fallback"] is False
    assert "Traditional Chinese" in d.llm.calls[0][1][0]["content"]
    # 英文遊戲不加繁體規則
    llm = FakeLLM(dialogue=["Hi."])
    Director(GAME, llm).rewrite_line(text, "a1", RESCUE, STATE)
    assert "Traditional" not in llm.calls[0][1][0]["content"]


def test_reply_about_the_task_falls_back():
    d = Director(ZH_GAME, FakeLLM(dialogue=["要改寫的是她的台詞，內容不能增減。"]))
    text = "恩公，鎮妖司的人下山了，你快走吧。"
    r = d.rewrite_line(text, "a1", RESCUE, STATE)
    assert r["fallback"] is True and r["text"] == text and r["reason"] == "reply talks about the task"


def test_simplified_pack_is_sent_back_for_repair():
    bad = json.loads(REPLY)
    bad["meta"]["title"] = "山贼来了"
    llm = FakeLLM(director=[json.dumps(bad, ensure_ascii=False), REPLY])
    Director(ZH_GAME, llm).generate(STATE)
    assert len(llm.calls) == 2
    assert "贼" in llm.calls[1][1][-1]["content"] and "Traditional" in llm.calls[1][1][-1]["content"]


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


def test_profile_carries_recent_history_and_prompt_drops_raw_copy():
    state = {"history": [{"day": d, "pack": f"p{d}", "title": f"t{d}", "result": "complete",
                          "choices": ["c"], "tags": []} for d in range(12)]}
    prof = build_profile(state, {"tiers": ["novice"]})
    assert [h["day"] for h in prof["history"]] == list(range(4, 12))
    assert "pack" not in prof["history"][0]
    game = dict(GAME)
    game["banned_words"] = ["phone"]
    msgs = prompts.director_messages(game, state, prof, {"kind": "fresh"})
    req = json.loads(msgs[-1]["content"])
    assert "history" not in req["world_state"] and len(req["player_profile"]["history"]) == 8
    assert "phone" in msgs[0]["content"]


def test_restock_request_sets_region(tmp_path):
    d = Director(GAME, FakeLLM(director=lambda m: REPLY), out_dir=tmp_path)
    pack = d.generate(STATE, "save.r", region="region.test_town")
    assert pack["meta"]["region"] == "region.test_town"
    sent = json.loads(d.llm.calls[-1][1][-1]["content"])
    assert sent["request"]["region"] == "region.test_town"
    assert "region.test_town" in prompts.catalog_text(GAME["catalog"])


def test_tag_gate_and_expiry_are_enforced(tmp_path):
    d = Director(GAME, FakeLLM(director=lambda m: REPLY), out_dir=tmp_path)
    gate = [{"type": "tier_up", "to": "adept"}]
    pack = d.generate(STATE, "save.g", tag="heart_demon", gate=gate, expires_after_days=1)
    assert pack["meta"]["request_tag"] == "heart_demon"
    assert pack["trigger"]["all"][0] == gate[0]
    assert pack["trigger"]["expires_after_days"] == 1
    assert validate.validate_pack(pack, GAME["ruleset"], GAME["catalog"]) == []
    sent = json.loads(d.llm.calls[-1][1][-1]["content"])["request"]
    assert sent["required_trigger"] == gate and sent["expires_after_days"] == 1


def test_access_codes_and_daily_limits(tmp_path):
    from director.access import AccessCodes
    day = ["2026-10-31"]
    access = AccessCodes(tmp_path / "codes.json", clock=lambda: day[0])
    code = access.add("amin", daily_packs=2, daily_lines=1)
    other = access.add("bao", daily_packs=5, daily_lines=5)
    access.total_daily_packs = 3
    assert code not in (tmp_path / "codes.json").read_text(encoding="utf-8")  # 只存雜湊
    d = Director(GAME, FakeLLM(director=lambda m: REPLY, dialogue=lambda m: "Well met."))
    server = make_server(d, host="0.0.0.0", port=0, access=access)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}/v1"

    def post(path, body, key=None):
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = "Bearer " + key
        req = urllib.request.Request(base + path, json.dumps(body).encode(), headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    gen = {"state": STATE, "followup": STATE["followups"][0]}
    line = {"text": "Hello.", "node_id": "a1", "pack": RESCUE, "state": STATE}
    try:
        with urllib.request.urlopen(base + "/health", timeout=5) as r:
            assert json.loads(r.read())["auth"] is True
        assert post("/packs/generate", gen) == 401
        assert post("/packs/generate", gen, "zw-wrong") == 401
        assert [post("/packs/generate", gen, code) for _ in range(3)] == [200, 200, 429]
        assert [post("/dialogue/rewrite", line, code) for _ in range(2)] == [200, 429]
        assert post("/packs/generate", gen, other) == 200
        assert post("/packs/generate", gen, other) == 429  # 合計上限 3
        day[0] = "2026-11-01"
        assert post("/packs/generate", gen, code) == 200
        access.codes[0]["disabled"] = True
        assert post("/packs/generate", gen, code) == 401
        assert AccessCodes(tmp_path / "codes.json").used("amin", "packs", "2026-10-31") == 2
    finally:
        server.shutdown()
    with pytest.raises(ValueError):
        make_server(d, host="0.0.0.0", port=0)


def test_lore_reference_follows_region_tier_and_tag():
    from director import lore
    town = lore.select(GAME["lore"], dict(STATE, region="region.test_town"), GAME["ruleset"], k=8)
    assert [k for k, _ in town][0] == "lore.grey_wolf"
    assert "lore.mirror_spirit" not in dict(town)  # 別的地區
    trial = dict(STATE, region="region.test_trial")
    assert "lore.mirror_spirit" not in dict(lore.select(GAME["lore"], trial, GAME["ruleset"]))  # 階級不夠
    adept = lore.select(GAME["lore"], dict(trial, tier_index=1), GAME["ruleset"], tag="heart_demon", k=1)
    assert adept[0][0] == "lore.mirror_spirit"
    ref = lore.reference(town, GAME["lore"]["sources"])
    wolf = ref["entries"][0]
    assert wolf["source"] == "The Old Bestiary · Woods" and wolf["verified"] and wolf["usage_idea"]
    assert "Never invent a source" in ref["note"]


def test_lore_goes_in_the_request_not_the_cached_system_prompt():
    llm = FakeLLM(director=lambda m: REPLY)
    d = Director(GAME, llm, max_retries=0)
    for region in ("region.test_town", "region.test_trial"):
        try:
            d.generate(STATE, "s", region=region)
        except DirectorError:
            pass  # 範例回覆不屬於試煉地區，驗證失敗無妨；這裡只看送出的提示
    first, second = llm.calls[0][1], llm.calls[1][1]
    assert first[0]["content"] == second[0]["content"]
    assert "lore.grey_wolf" in first[-1]["content"] and "lore.grey_wolf" not in second[-1]["content"]


def test_check_lore_reports_bad_refs():
    bad = {"sources": {}, "entries": {"lore.x": {"name": "X", "summary": "",
           "quote": {"source": "src.none"}, "hooks": {"regions": ["region.nowhere"], "tier_min": "god"}}}}
    errs = validate.check_lore(bad, GAME["ruleset"], GAME["catalog"])
    assert len(errs) == 4
    assert validate.check_lore(GAME["lore"], GAME["ruleset"], GAME["catalog"]) == []


def test_judge_ranks_candidates_over_http(tmp_path):
    from director.access import AccessCodes
    from director.judge import FakeJudge, JudgeError, fit_request
    rescue = copy.deepcopy(RESCUE)
    other = copy.deepcopy(RESCUE)
    other["id"], other["meta"]["title"] = "pack.test.other", "Other"
    body, ids = fit_request(STATE, GAME["ruleset"], [rescue, other], {"region": "region.test_town"})
    assert set(body["questions"]) == {"c0", "c1"} and ids["c1"] == "pack.test.other"
    q = body["questions"]["c0"]
    assert q["type"] == "score" and len(q["criteria"]) == 5 and "`candidates.c0`" in q["instructions"]
    assert body["state"]["player"]["history"] == [] and body["state"]["candidates"]["c1"]["title"] == "Other"

    judge = FakeJudge({"Other": 3.6})
    access = AccessCodes(tmp_path / "codes.json")
    code = access.add("t")
    d = Director(GAME, FakeLLM())
    server = make_server(d, port=0, access=access, judge=judge)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}/v1"
    try:
        req = urllib.request.Request(base + "/judge/rank", json.dumps({"state": STATE, "packs": [rescue, other]}).encode(),
                                     {"Content-Type": "application/json", "Authorization": "Bearer " + code})
        with urllib.request.urlopen(req, timeout=5) as r:
            scores = json.loads(r.read())["scores"]
        assert scores["pack.test.other"]["fit"] == 3.6 and scores[rescue["id"]]["fit"] == 2.0
        assert access.used("t", "judges") == 1

        def boom(body):
            raise JudgeError("down")
        judge.ask = boom
        try:
            urllib.request.urlopen(req, timeout=5)
            assert False
        except urllib.error.HTTPError as e:
            assert e.code == 502
    finally:
        server.shutdown()
    plain = make_server(d, port=0)
    threading.Thread(target=plain.serve_forever, daemon=True).start()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{plain.server_port}/v1/judge/rank", b"{}",
                                     {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
        assert False
    except urllib.error.HTTPError as e:
        assert e.code == 503
    finally:
        plain.shutdown()


def test_judge_bench_runs_with_fake_judge(tmp_path):
    from director import judge_bench
    from director.judge import FakeJudge
    root = FIX
    scen = [{"name": "rescued", "state": STATE, "candidates": ["packs/rescue.json", "packs/trial.json"],
             "expect_top": "pack.test.trial"}]
    loaded = [dict(scen[0], packs=[json.loads((root / p).read_text(encoding="utf-8")) for p in scen[0]["candidates"]])]
    trial_title = loaded[0]["packs"][1]["meta"]["title"]
    report = judge_bench.run(GAME, loaded, FakeJudge({trial_title: 3.5}), runs=2)
    assert report[0]["passed"] == 2 and report[0]["runs"][0]["top"] == "pack.test.trial"
    assert "2/2" in judge_bench.summary(report, "fake")


def test_bench_check_expect():
    from director.bench import check_expect
    pack = json.loads(REPLY)
    pack["meta"]["region"] = "region.test_town"
    gate = [{"type": "tier_up", "to": "adept"}]
    pack["trigger"]["all"][:0] = gate
    ok = {"chunk_tag": "forest", "templates": ["mob.test"], "sets_flag": "gang_broken",
          "forbid_conditions": ["tier_up", "flag"], "region_map_tags": True,
          "mentions_any": ["Scar", "nobody"], "mentions_none": ["dragon"], "max_loot": 1}
    # gate 是引擎加的，不算進 forbid_conditions
    assert check_expect(pack, ok, GAME, {"region": "region.test_town", "gate": gate}) == []
    bad = {"chunk_tag": "trial", "templates": ["boss.test"], "sets_flag": "other", "no_meta_region": True,
           "no_extra_conditions": True,
           "forbid_conditions": ["time_of_day"], "region_map_tags": True,
           "mentions_any": ["dragon"], "mentions_none": ["Scar"], "max_loot": 0}
    unmet = check_expect(pack, bad, GAME, {"region": "region.test_trial", "gate": gate})
    assert [u.split(":")[0] for u in unmet] == [
        "chunk_tag", "templates", "sets_flag", "forbid_conditions", "no_extra_conditions", "region_map_tags",
        "no_meta_region", "mentions_any", "mentions_none", "max_loot"]


def test_access_usage_can_live_outside_readonly_codes_dir(tmp_path):
    import os
    import stat
    from director.access import AccessCodes
    secret = tmp_path / "secret"
    secret.mkdir()
    code = AccessCodes(secret / "codes.json").add("amin")
    (secret / "codes.json").chmod(stat.S_IRUSR)
    secret.chmod(stat.S_IRUSR | stat.S_IXUSR)  # 像 Kubernetes Secret 一樣唯讀
    try:
        usage = tmp_path / "data" / "usage.json"
        usage.parent.mkdir()
        access = AccessCodes(secret / "codes.json", usage_path=usage)
        assert access.charge(code, "packs") == "amin"
        assert usage.exists() and not (secret / "codes.json.usage.json").exists()
        assert AccessCodes(secret / "codes.json", usage_path=usage).used("amin", "packs") == 1
    finally:
        secret.chmod(stat.S_IRWXU)
        os.chmod(secret / "codes.json", stat.S_IRUSR | stat.S_IWUSR)


def test_cors_preflight_and_headers():
    d = Director(GAME, FakeLLM(director=[], dialogue=lambda m: "x"))
    server = make_server(d, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}/v1"
    try:
        req = urllib.request.Request(base + "/packs/generate", method="OPTIONS",
                                     headers={"Origin": "https://play.example", "Access-Control-Request-Method": "POST"})
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 204
            assert r.headers["Access-Control-Allow-Origin"] == "*"
            assert "Authorization" in r.headers["Access-Control-Allow-Headers"]
        with urllib.request.urlopen(base + "/health", timeout=5) as r:
            assert r.headers["Access-Control-Allow-Origin"] == "*"
    finally:
        server.shutdown()
    quiet = make_server(d, port=0, cors_origin="")
    threading.Thread(target=quiet.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{quiet.server_port}/v1/health", timeout=5) as r:
            assert r.headers.get("Access-Control-Allow-Origin") is None
    finally:
        quiet.shutdown()


def test_catalog_lists_wild_monsters():
    catalog = json.loads(json.dumps(GAME["catalog"]))
    catalog["regions"]["region.test_town"]["wild"] = [
        {"template": "mob.test", "name": "野狗", "chunk": "chunk.street", "times": ["night"]}]
    text = prompts.catalog_text(catalog)
    assert "wild monsters" in text and "mob.test（野狗，chunk.street，night）" in text
    assert "wild monsters" not in prompts.catalog_text(GAME["catalog"])
