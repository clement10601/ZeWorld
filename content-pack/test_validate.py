"""validate.py 的測試：範例要通過，改壞的要被擋。執行：python -m pytest content-pack"""
import copy
import json
import tempfile
from pathlib import Path

import validate

FIX = Path(__file__).parent / "fixtures"
RULESET = json.loads((FIX / "ruleset.json").read_text(encoding="utf-8"))
RESCUE = json.loads((FIX / "packs" / "rescue.json").read_text(encoding="utf-8"))


def run(pack):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(pack, f, ensure_ascii=False)
    return validate.validate(f.name, RULESET)


def broken(mutate):
    pack = copy.deepcopy(RESCUE)
    mutate(pack)
    return run(pack)


def test_fixtures_pass():
    for p in (FIX / "packs").glob("*.json"):
        assert validate.validate(p, RULESET) == [], p


def test_unknown_effect_rejected():
    errs = broken(lambda p: p["outcomes"]["on_complete"].append({"type": "set_hp", "value": 9999}))
    assert errs and "outcomes/on_complete" in errs[0]


def test_reward_tier_outside_enum_rejected():
    errs = broken(lambda p: p["outcomes"]["on_complete"][3].update(tier="legendary"))
    assert errs


def test_tier_not_in_ruleset_rejected():
    errs = broken(lambda p: p["meta"]["tier_range"].update(max="grandmaster"))
    assert any("ruleset.tiers" in e for e in errs)


def test_karma_axis_not_in_ruleset_rejected():
    errs = broken(lambda p: p["dialogue"]["nodes"]["a3"]["choices"][0]["effects"][0].update(axis="chaos"))
    assert any("karma_axes" in e for e in errs)


def test_dangling_dialogue_rejected():
    errs = broken(lambda p: p["dialogue"]["nodes"]["a1"].update(next="nope"))
    assert errs == ["dialogue a1: 指向不存在的節點 nope"]


def test_unknown_actor_rejected():
    errs = broken(lambda p: p["stages"][1]["complete_when"][0].update(actor="ghost"))
    assert any("ghost" in e for e in errs)


GAME = validate.load_game(FIX / "zeworld.json")


def test_game_manifest_loads():
    assert GAME["ruleset"] == RULESET
    assert "chunk.test_street" in GAME["catalog"]["chunks"]
    assert GAME["style"].startswith("Low-fantasy")
    for p in GAME["packs_dir"].glob("*.json"):
        assert validate.validate(p, GAME["ruleset"], GAME["catalog"]) == [], p


def catalog_errors(mutate):
    pack = copy.deepcopy(RESCUE)
    mutate(pack)
    return validate.validate_pack(pack, GAME["ruleset"], GAME["catalog"])


def test_unknown_chunk_rejected():
    errs = catalog_errors(lambda p: p["map"]["chunks"][0].update(chunk="chunk.moon"))
    assert any("chunk.moon" in e for e in errs)


def test_point_outside_chosen_chunks_rejected():
    def mutate(p):
        p["map"]["points"].append("pt.arena")
    assert any("pt.arena" in e for e in catalog_errors(mutate))


def test_unknown_map_tag_template_and_loot_rejected():
    def mutate(p):
        p["trigger"]["all"][2]["map_tag"] = "volcano"
        p["actors"][0]["template"] = "npc.dragon"
        p["outcomes"]["on_complete"][3]["loot_table"] = "loot.nope"
    errs = catalog_errors(mutate)
    assert len(errs) == 3, errs


CAPPED = dict(RULESET, loot_caps={"novice": "common", "master": "epic"}, exp_caps={"novice": "small"},
              max_loot_per_outcome=1)


def test_loot_cap_follows_lowest_tier():
    errs = validate.validate_pack(copy.deepcopy(RESCUE), CAPPED)
    assert any("rare" in e and "common" in e for e in errs)
    pack = copy.deepcopy(RESCUE)
    pack["meta"]["tier_range"]["min"] = "adept"  # adept 沒列，沿用 novice 的上限
    assert any("rare" in e for e in validate.validate_pack(pack, CAPPED))
    pack["meta"]["tier_range"]["min"] = pack["meta"]["tier_range"]["max"] = "master"
    assert not [e for e in validate.validate_pack(pack, CAPPED) if "掉落" in e]


def test_exp_cap_and_loot_count():
    pack = copy.deepcopy(RESCUE)
    pack["outcomes"]["on_complete"] += [{"type": "grant_exp", "amount": "large"},
                                        {"type": "give_loot", "loot_table": "loot.test", "tier": "common"}]
    errs = validate.validate_pack(pack, CAPPED)
    assert any("large" in e for e in errs)
    assert any("give_loot 有 2 個" in e for e in errs)


def test_no_caps_means_no_balance_errors():
    assert validate.validate_pack(copy.deepcopy(RESCUE), RULESET) == []


def test_banned_words_found_in_any_text():
    pack = copy.deepcopy(RESCUE)
    node = next(iter(pack["dialogue"]["nodes"].values()))
    node["text"] += " phone"
    pack["stages"][0]["objective"] += " laser"
    errs = validate.validate_pack(pack, RULESET, banned_words=["phone", "laser", "absent"])
    assert len(errs) == 2 and "phone" in errs[0] + errs[1] and "laser" in errs[0] + errs[1]
    assert "absent" not in " ".join(errs)


def test_walk_text_covers_player_facing_fields():
    places = [w for w, _ in validate.walk_text(RESCUE)]
    assert "meta.title" in places and any(p.startswith("dialogue") for p in places)
    assert any(p.startswith("stage") for p in places)


def test_manifest_banned_words_file(tmp_path):
    for f in FIX.iterdir():
        if f.is_file():
            (tmp_path / f.name).write_bytes(f.read_bytes())
    (tmp_path / "banned.txt").write_text("# 註解\nphone\n\n laser \n", encoding="utf-8")
    m = json.loads((FIX / "zeworld.json").read_text(encoding="utf-8"))
    m["banned_words"] = "banned.txt"
    m["packs"] = str(FIX / "packs")
    (tmp_path / "zeworld.json").write_text(json.dumps(m), encoding="utf-8")
    assert validate.load_game(tmp_path / "zeworld.json")["banned_words"] == ["phone", "laser"]
