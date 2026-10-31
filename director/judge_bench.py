"""用真的決策模型實測判斷器第二層：貼合度排序是否合理、延遲、輸入 token 與花費。不進 CI，要花額度。

python -m director.judge_bench --game path/to/zeworld.json --scenarios path/to/judge_scenarios.json [--runs 3]

judge_scenarios.json：{"scenarios": [{"name", "state" 或 "state_from": [另一個情境檔, 情境名],
  "candidates": [內容包路徑], "expect_top"?: 內容包 id, "expect_not_top"?: 內容包 id}]}
路徑相對於遊戲根目錄（zeworld.json 那層）。--fake 用假模型跑一遍，只檢查情境檔與流程。
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

from ._validate import validate
from .judge import DecisionJudge, FakeJudge, JudgeError, fit_request


def load_scenarios(path, root):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for s in data["scenarios"]:
        state = s.get("state")
        if state is None:
            other, name = s["state_from"]
            ref = json.loads((root / other).read_text(encoding="utf-8"))["scenarios"]
            state = next(x["state"] for x in ref if x["name"] == name)
        packs = [json.loads((root / p).read_text(encoding="utf-8")) for p in s["candidates"]]
        out.append(dict(s, state=state, packs=packs))
    return out


def run(game, scenarios, judge, runs):
    report = []
    for s in scenarios:
        body, ids = fit_request(s["state"], game["ruleset"], s["packs"], s.get("moment"))
        rows = []
        for _ in range(runs):
            t = time.monotonic()
            try:
                reply = judge.ask(body)
            except JudgeError as e:
                rows.append({"error": str(e), "secs": time.monotonic() - t})
                continue
            fits = {ids[c]: a["score"] for c, a in reply["answers"].items() if c in ids}
            top = max(fits, key=fits.get)
            usage = reply.get("usage", {})
            rows.append({"secs": time.monotonic() - t, "fits": fits, "top": top,
                         "confidence": {ids[c]: a.get("confidence") for c, a in reply["answers"].items() if c in ids},
                         "input_tokens": usage.get("input_tokens"), "cost": usage.get("cost")})
        ok = [r for r in rows if "error" not in r]
        passed = [(not s.get("expect_top") or r["top"] == s["expect_top"])
                  and (not s.get("expect_not_top") or r["top"] != s["expect_not_top"]) for r in ok]
        report.append({"name": s["name"], "runs": rows, "passed": sum(passed), "ok": len(ok),
                       "titles": {p["id"]: p["meta"]["title"] for p in s["packs"]}})
    return report


def summary(report, model):
    lines = [f"model: {model}"]
    secs = [r["secs"] for s in report for r in s["runs"] if "error" not in r]
    toks = [r["input_tokens"] for s in report for r in s["runs"] if r.get("input_tokens")]
    costs = [r["cost"] for s in report for r in s["runs"] if r.get("cost") is not None]
    for s in report:
        lines.append(f"\n{s['name']}: {s['passed']}/{s['ok']} 符合預期" + (f"，{len(s['runs']) - s['ok']} 次失敗" if s['ok'] < len(s['runs']) else ""))
        for r in s["runs"]:
            if "error" in r:
                lines.append(f"  錯誤：{r['error']}")
                continue
            ranked = sorted(r["fits"].items(), key=lambda kv: -kv[1])
            lines.append("  " + "  >  ".join(f"{s['titles'][pid]} {fit:.2f}" for pid, fit in ranked) + f"  ({r['secs']:.2f}s)")
    if secs:
        lines.append(f"\n延遲中位數 {statistics.median(secs):.2f}s，最慢 {max(secs):.2f}s")
    if toks:
        lines.append(f"輸入 token 平均 {statistics.mean(toks):.0f}")
    if costs:
        lines.append(f"每次花費平均 ${statistics.mean(costs):.6f}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m director.judge_bench", description=__doc__.splitlines()[0])
    ap.add_argument("--game", required=True)
    ap.add_argument("--scenarios", required=True)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--fake", action="store_true", help="用假模型，只檢查情境檔與流程")
    ap.add_argument("--json", help="完整結果寫到這個檔案")
    args = ap.parse_args(argv)
    game = validate.load_game(args.game)
    scenarios = load_scenarios(args.scenarios, Path(args.game).parent)
    judge = FakeJudge({}) if args.fake else DecisionJudge.from_env()
    if judge is None:
        sys.exit("沒有決策模型設定：請設 ZEWORLD_JUDGE_API_KEY（OpenRouter 金鑰）。")
    report = run(game, scenarios, judge, args.runs)
    print(summary(report, judge.model))
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
