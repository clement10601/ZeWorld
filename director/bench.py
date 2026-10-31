"""用真模型跑導演，量通過率、重試、延遲、token 與簡體字混入。不進 CI，要花 API 額度。

python -m director.bench --game path/to/zeworld.json --scenarios path/to/scenarios.json [--runs 2] [--out dir]

scenarios.json：{"scenarios": [{"name", "state", "followup": N?, "hint"?, "rewrite": [{"pack", "node"}]}]}
pack 路徑相對於 scenarios.json 所在的遊戲根目錄（zeworld.json 那層）。
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
                pack = director.generate(state, "save.bench", followup, sc.get("hint"))
                entry.update(ok=True, id=pack["id"], title=pack["meta"].get("title"),
                             summary=pack["meta"].get("summary"), simplified=simplified_chars(pack_text(pack), game["language"]))
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
         "lines": len(lines), "line_fallbacks": sum(1 for x in lines if x["fallback"]),
         "line_secs_median": statistics.median(x["secs"] for x in lines) if lines else 0,
         "lines_with_simplified": sum(1 for x in lines if x.get("simplified"))}
    return s


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m director.bench", description=__doc__.splitlines()[0])
    ap.add_argument("--game", required=True)
    ap.add_argument("--scenarios", required=True)
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--only", help="只跑這個名稱的情境")
    ap.add_argument("--out", help="產出的內容包與 report.json 寫到這裡")
    args = ap.parse_args(argv)
    llm = llm_from_env()
    if llm is None:
        sys.exit("沒有 LLM 設定，見 director/llm.py")
    game = validate.load_game(args.game)
    scenarios = json.loads(Path(args.scenarios).read_text(encoding="utf-8"))["scenarios"]
    if args.only:
        scenarios = [s for s in scenarios if s["name"] == args.only]
    report = run(game, scenarios, Path(args.game).resolve().parent, args.runs, args.out, llm)
    if args.out:
        Path(args.out).mkdir(parents=True, exist_ok=True)
        (Path(args.out) / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
