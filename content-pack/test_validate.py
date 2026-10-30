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
