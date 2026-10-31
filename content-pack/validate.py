"""內容包驗證器：Schema 檢查 + ruleset 名稱檢查 + 內部參照檢查。

用法：python validate.py --ruleset path/to/ruleset.json pack1.json pack2.json ...
導演產出的內容包必須通過這裡才能進預生成池。
數值平衡、世界觀一致性、素材是否存在等檢查之後再加。
"""
import argparse
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

HERE = Path(__file__).parent
SCHEMA = json.loads((HERE / "schema.json").read_text(encoding="utf-8"))


def walk_effects(pack):
    """列出內容包裡所有 effect，連同出處。"""
    for key, effects in pack["outcomes"].items():
        for e in effects:
            yield f"outcomes.{key}", e
    for st in pack["stages"]:
        for key in ("on_enter", "on_complete"):
            for e in st.get(key, []):
                yield f"stage {st['id']}.{key}", e
    for nid, node in pack.get("dialogue", {}).get("nodes", {}).items():
        for e in node.get("effects", []):
            yield f"dialogue {nid}", e
        for c in node.get("choices", []):
            for e in c.get("effects", []):
                yield f"dialogue {nid} choice", e


def walk_conditions(pack):
    for c in pack["trigger"]["all"] + pack["trigger"].get("any", []):
        yield "trigger", c
    for st in pack["stages"]:
        for key in ("complete_when", "fail_when"):
            for c in st.get(key, []):
                yield f"stage {st['id']}.{key}", c
    for nid, node in pack.get("dialogue", {}).get("nodes", {}).items():
        for ch in node.get("choices", []):
            for c in ch.get("show_if", []):
                yield f"dialogue {nid} choice", c


def check_ruleset(pack, ruleset):
    """題材名稱（階級、karma 軸、時段）必須是遊戲 ruleset 裡定義的。"""
    errors = []
    tiers, axes, times = ruleset["tiers"], ruleset["karma_axes"], ruleset["times_of_day"]
    rr = pack["meta"]["tier_range"]
    for k in ("min", "max"):
        if rr[k] not in tiers:
            errors.append(f"meta.tier_range.{k}: {rr[k]} 不在 ruleset.tiers")
    if not errors and tiers.index(rr["min"]) > tiers.index(rr["max"]):
        errors.append("meta.tier_range: min 高於 max")
    for where, c in walk_conditions(pack):
        tier = c.get("tier") or (c.get("to") if c["type"] == "tier_up" else None)
        if tier and tier not in tiers:
            errors.append(f"{where}: 階級 {tier} 不在 ruleset.tiers")
        if c["type"] == "karma" and c["axis"] not in axes:
            errors.append(f"{where}: karma 軸 {c['axis']} 不在 ruleset.karma_axes")
        for t in c.get("in", []) if c["type"] == "time_of_day" else []:
            if t not in times:
                errors.append(f"{where}: 時段 {t} 不在 ruleset.times_of_day")
    for where, e in walk_effects(pack):
        if e["type"] == "add_karma" and e["axis"] not in axes:
            errors.append(f"{where}: karma 軸 {e['axis']} 不在 ruleset.karma_axes")
    return errors


def check_refs(pack):
    errors = []
    actors = {a["id"] for a in pack.get("actors", [])}
    points = set(pack.get("map", {}).get("points", []))
    nodes = pack.get("dialogue", {}).get("nodes", {})

    if "dialogue" in pack and pack["dialogue"]["entry"] not in nodes:
        errors.append(f"dialogue.entry 指向不存在的節點 {pack['dialogue']['entry']}")
    for nid, node in nodes.items():
        sp = node["speaker"]
        if sp not in actors and sp not in ("player", "narrator"):
            errors.append(f"dialogue {nid}: speaker {sp} 不是已定義的 actor")
        targets = [node.get("next")] + [c["next"] for c in node.get("choices", [])]
        for t in filter(None, targets):
            if t not in nodes:
                errors.append(f"dialogue {nid}: 指向不存在的節點 {t}")

    map_ = pack.get("map")
    if map_ and points and map_["spawn"] not in points:
        errors.append(f"map.spawn {map_['spawn']} 不在 map.points")
    for a in pack.get("actors", []):
        if points and a.get("spawn_at") and a["spawn_at"] not in points:
            errors.append(f"actor {a['id']}: spawn_at {a['spawn_at']} 不在 map.points")

    for where, e in walk_effects(pack):
        if e["type"] in ("spawn_actor", "despawn_actor") and e["actor"] not in actors:
            errors.append(f"{where}: {e['type']} 指向不存在的 actor {e['actor']}")
        if e["type"] == "start_dialogue" and e["node"] not in nodes:
            errors.append(f"{where}: start_dialogue 指向不存在的節點 {e['node']}")
        if e["type"] == "teleport" and points and e["point"] not in points:
            errors.append(f"{where}: teleport 指向不存在的點 {e['point']}")
    for where, c in walk_conditions(pack):
        if c["type"] == "actor_defeated" and c["actor"] not in actors:
            errors.append(f"{where}: actor_defeated 指向不存在的 actor {c['actor']}")
        if c["type"] == "dialogue_reached" and c["node"] not in nodes:
            errors.append(f"{where}: dialogue_reached 指向不存在的節點 {c['node']}")
        if c["type"] == "reach_point" and points and c["point"] not in points:
            errors.append(f"{where}: reach_point 指向不存在的點 {c['point']}")
    return errors


def validate(path, ruleset):
    pack = json.loads(Path(path).read_text(encoding="utf-8"))
    validator = Draft202012Validator(SCHEMA)
    errors = [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
              for e in validator.iter_errors(pack)]
    if not errors:
        errors = check_ruleset(pack, ruleset) + check_refs(pack)
    return errors


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ruleset", required=True)
    ap.add_argument("packs", nargs="+")
    args = ap.parse_args()
    ruleset = json.loads(Path(args.ruleset).read_text(encoding="utf-8"))
    failed = 0
    for p in args.packs:
        errs = validate(p, ruleset)
        print(("OK   " if not errs else "FAIL ") + p)
        for e in errs:
            print("     - " + e)
        failed += bool(errs)
    sys.exit(1 if failed else 0)
