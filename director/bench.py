"""用真模型跑導演，量通過率、重試、延遲、token 與簡體字混入。不進 CI，要花 API 額度。

python -m director.bench --game path/to/zeworld.json --scenarios path/to/scenarios.json [--runs 2] [--out dir]

scenarios.json：{"scenarios": [{"name", "state", "followup": N?, "hint"?, "request"?, "expect"?, "rewrite": [{"pack", "node"}]}]}
pack 路徑相對於 scenarios.json 所在的遊戲根目錄（zeworld.json 那層）。
request 是遊戲主動請求時帶的選項：{"region", "tag", "gate", "expires_after_days"}，照原樣傳給 Director.generate。
expect 是通過驗證以外、這種請求該滿足的條件，不符的寫進 entry.unmet（見 check_expect）。
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

from ._validate import validate
from .core import Director, DirectorError, pack_text
from .llm import llm_from_env
from .script import simplified_chars

class Meter:
    """包住 LLM，記每次呼叫的角色、秒數與 token。"""

    def __init__(self, llm):
        self.llm = llm
        self.log = []

    def model_for(self, role):
        return self.llm.model_for(role)

    def chat(self, messages, role="director", **kw):
        t = time.monotonic()
        try:
            return self.llm.chat(messages, role=role, **kw)
        finally:
            self.log.append({"role": role, "secs": time.monotonic() - t,
                             "usage": getattr(self.llm, "last_usage", None)})


def run(game, scenarios, root, runs, out_dir, llm):
    meter = Meter(llm)
    director = Director(game, meter, out_dir=out_dir)
    report = {"director_model": llm.model_for("director"), "dialogue_model": llm.model_for("dialogue"),
              "packs": [], "lines": []}
    for sc in scenarios:
        state = sc["state"]
        followup = state["followups"][sc["followup"]] if sc.get("followup") is not None else None
        for i in range(runs):
            start = len(meter.log)
            t = time.monotonic()
            entry = {"scenario": sc["name"], "run": i}
            try:
                pack = director.generate(state, "save.bench", followup, sc.get("hint"), **sc.get("request", {}))
                entry.update(ok=True, id=pack["id"], title=pack["meta"].get("title"),
                             summary=pack["meta"].get("summary"), simplified=simplified_chars(pack_text(pack), game["language"]))
                if sc.get("expect"):
                    entry["unmet"] = check_expect(pack, sc["expect"], game, sc.get("request", {}))
                pack_for_lines = pack
            except DirectorError as e:
                entry.update(ok=False, error=str(e), errors=e.errors[:10])
                pack_for_lines = None
            calls = [c for c in meter.log[start:] if c["role"] == "director"]
            entry.update(attempts=len(calls), secs=round(time.monotonic() - t, 1),
                         tokens_in=sum((c["usage"] or {}).get("input", 0) for c in calls),
                         tokens_out=sum((c["usage"] or {}).get("output", 0) for c in calls),
                         cache_read=sum((c["usage"] or {}).get("cache_read", 0) for c in calls))
            report["packs"].append(entry)
            print(json.dumps(entry, ensure_ascii=False), file=sys.stderr, flush=True)
            if pack_for_lines:
                live = [nid for nid, n in pack_for_lines["dialogue"]["nodes"].items() if n.get("live_rewrite")]
                for nid in live[:3]:
                    report["lines"].append(rewrite(director, meter, sc["name"] + "/gen", pack_for_lines, nid, state))
        for r in sc.get("rewrite", []):
            pack = json.loads((root / r["pack"]).read_text(encoding="utf-8"))
            report["lines"].append(rewrite(director, meter, sc["name"], pack, r["node"], state))
    report["summary"] = summarize(report)
    return report


def check_expect(pack, expect, game, request=None):
    """回傳不符的條件說明。expect 的鍵：
    chunk_tag          地圖每個區塊都要有這個標籤
    templates          這些角色模板至少要用到一個
    sets_flag          on_complete 要立這個旗標
    forbid_conditions  觸發條件不能用這些種類（gate 由引擎加的不算）
    no_extra_conditions 觸發條件只能有引擎加的 gate（gate 是一次性時機時，多加條件可能讓事件永遠觸發不了）
    region_map_tags    觸發的 enter_map 只能用請求地區區塊有的標籤
    no_meta_region     meta 不能有 region
    mentions_any       內容包文字至少要提到其中一個（檢查有沒有接上玩家經歷）
    mentions_none      內容包文字不能提到這些（玩家沒經歷過的事）
    max_loot           每個結局最多幾件獎勵"""
    request = request or {}
    region = request.get("region")
    chunks = game["catalog"]["chunks"]
    unmet = []
    used = [c["chunk"] for c in pack.get("map", {}).get("chunks", [])]
    tag = expect.get("chunk_tag")
    if tag:
        bad = [c for c in used if tag not in chunks.get(c, {}).get("tags", [])]
        if bad:
            unmet.append(f"chunk_tag: {bad} 沒有標籤 {tag}")
    want = expect.get("templates")
    if want and not any(a.get("template") in want for a in pack.get("actors", [])):
        unmet.append(f"templates: 沒用到 {want}")
    flag = expect.get("sets_flag")
    if flag and not any(e.get("type") == "set_flag" and e.get("key") == flag
                        for e in pack.get("outcomes", {}).get("on_complete", [])):
        unmet.append(f"sets_flag: on_complete 沒立 {flag}")
    gate = request.get("gate") or []
    conds = [c for c in pack.get("trigger", {}).get("all", []) if c not in gate]
    forbid = set(expect.get("forbid_conditions", []))
    bad = [c for c in conds if c.get("type") in forbid]
    if bad:
        unmet.append(f"forbid_conditions: {bad}")
    if expect.get("no_extra_conditions") and conds:
        unmet.append(f"no_extra_conditions: {conds}")
    if expect.get("region_map_tags") and region:
        tags = {t for c in game["catalog"]["regions"][region].get("chunks", []) for t in chunks.get(c, {}).get("tags", [])}
        bad = [c["map_tag"] for c in conds if c.get("type") == "enter_map" and c["map_tag"] not in tags]
        if bad:
            unmet.append(f"region_map_tags: {bad} 不在 {region} 的區塊標籤 {sorted(tags)}")
    if expect.get("no_meta_region") and pack.get("meta", {}).get("region"):
        unmet.append(f"no_meta_region: meta.region = {pack['meta']['region']}")
    text = pack_text(pack)
    any_of = expect.get("mentions_any")
    if any_of and not any(w in text for w in any_of):
        unmet.append(f"mentions_any: 沒提到 {any_of}")
    hit = [w for w in expect.get("mentions_none", []) if w in text]
    if hit:
        unmet.append(f"mentions_none: 提到了 {hit}")
    cap = expect.get("max_loot")
    if cap is not None:
        for key, effects in pack.get("outcomes", {}).items():
            n = sum(1 for e in effects if e.get("type") == "give_loot")
            if n > cap:
                unmet.append(f"max_loot: {key} 給了 {n} 件獎勵")
    return unmet


def rewrite(director, meter, label, pack, node_id, state):
    text = pack["dialogue"]["nodes"][node_id]["text"]
    t = time.monotonic()
    res = director.rewrite_line(text, node_id, pack, state)
    entry = {"scenario": label, "node": node_id, "authored": text, "rewritten": res["text"],
             "fallback": res["fallback"], "reason": res.get("reason"), "rejected": res.get("rejected"), "secs": round(time.monotonic() - t, 2),
             "simplified": simplified_chars(res["text"], director.game["language"])}
    print(json.dumps(entry, ensure_ascii=False), file=sys.stderr, flush=True)
    return entry


def summarize(report):
    packs, lines = report["packs"], report["lines"]
    ok = [p for p in packs if p["ok"]]
    s = {"packs": len(packs), "valid": len(ok),
         "first_try": sum(1 for p in ok if p["attempts"] == 1),
         "mean_attempts": round(statistics.mean(p["attempts"] for p in packs), 2) if packs else 0,
         "pack_secs_median": statistics.median(p["secs"] for p in packs) if packs else 0,
         "pack_secs_max": max((p["secs"] for p in packs), default=0),
         "tokens_in_mean": round(statistics.mean(p["tokens_in"] for p in packs)) if packs else 0,
         "cache_read_mean": round(statistics.mean(p["cache_read"] for p in packs)) if packs else 0,
         "tokens_out_mean": round(statistics.mean(p["tokens_out"] for p in packs)) if packs else 0,
         "packs_with_simplified": sum(1 for p in ok if p.get("simplified")),
         "packs_with_unmet": sum(1 for p in ok if p.get("unmet")),
         "lines": len(lines), "line_fallbacks": sum(1 for x in lines if x["fallback"]),
         "line_secs_median": statistics.median(x["secs"] for x in lines) if lines else 0,
         "lines_with_simplified": sum(1 for x in lines if x.get("simplified"))}
    return s


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m director.bench", description=__doc__.splitlines()[0])
    ap.add_argument("--game", required=True)
    ap.add_argument("--scenarios", required=True)
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--only", help="只跑這些名稱的情境，逗號分隔")
    ap.add_argument("--out", help="產出的內容包與 report.json 寫到這裡")
    args = ap.parse_args(argv)
    llm = llm_from_env()
    if llm is None:
        sys.exit("沒有 LLM 設定，見 director/llm.py")
    game = validate.load_game(args.game)
    scenarios = json.loads(Path(args.scenarios).read_text(encoding="utf-8"))["scenarios"]
    if args.only:
        scenarios = [s for s in scenarios if s["name"] in args.only.split(",")]
    report = run(game, scenarios, Path(args.game).resolve().parent, args.runs, args.out, llm)
    if args.out:
        Path(args.out).mkdir(parents=True, exist_ok=True)
        (Path(args.out) / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
