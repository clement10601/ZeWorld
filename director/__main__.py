"""python -m director {serve,generate,profile} --game path/to/zeworld.json ...

LLM 設定見 director/llm.py（環境變數）。--fake-pack/--fake-line 用固定回覆跑，不需要模型，給離線開發與測試用。
"""
import argparse
import json
import os
import sys
from pathlib import Path

from ._validate import validate
from .access import AccessCodes
from .core import Director, DirectorError
from .judge import DecisionJudge
from .llm import FakeLLM, llm_from_env
from .profile import build_profile
from .server import make_server


def make_llm(args):
    if args.fake_pack or args.fake_line:
        pack = Path(args.fake_pack).read_text(encoding="utf-8") if args.fake_pack else ""
        return FakeLLM(director=lambda _m: pack, dialogue=lambda _m: args.fake_line or "")
    llm = llm_from_env()
    if llm is None:
        sys.exit("沒有 LLM 設定：請設 ZEWORLD_ANTHROPIC_API_KEY（Claude API），"
                 "或 ZEWORLD_LLM_BASE_URL 與 ZEWORLD_DIRECTOR_MODEL（OpenAI 相容端點），或用 --fake-pack 離線跑。")
    return llm


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m director", description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["serve", "generate", "profile"])
    ap.add_argument("--game", required=True, help="遊戲清單 zeworld.json")
    ap.add_argument("--state", help="世界狀態 JSON（WorldState.to_dict 或存檔裡的 state）")
    ap.add_argument("--save-id", default="")
    ap.add_argument("--hint", help="generate：給導演的劇情提示")
    ap.add_argument("--followup", type=int, help="generate：處理狀態裡第 N 個後續劇情請求")
    ap.add_argument("--out", help="產出的內容包寫到這個資料夾")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--access-codes", default=os.environ.get("ZEWORLD_DIRECTOR_ACCESS_CODES"),
                    help="serve：存取碼檔（python -m director.access 建立）。架在 localhost 以外時必填")
    ap.add_argument("--usage", default=os.environ.get("ZEWORLD_DIRECTOR_USAGE"),
                    help="serve：用量檔；沒給時寫在存取碼檔旁的 <檔名>.usage.json（存取碼檔唯讀時要給）")
    ap.add_argument("--cors-origin", default=os.environ.get("ZEWORLD_DIRECTOR_CORS_ORIGIN", "*"),
                    help="serve：允許哪個網頁來源呼叫導演（網頁版遊戲用）。預設 *；給空字串就不送 CORS 標頭")
    ap.add_argument("--retries", type=int, default=2)
    ap.add_argument("--fake-pack", help="假導演：每次都回這個檔案的內容")
    ap.add_argument("--fake-line", help="假台詞模型：每次都回這句")
    args = ap.parse_args(argv)

    game = validate.load_game(args.game)
    state = json.loads(Path(args.state).read_text(encoding="utf-8")) if args.state else {}
    state = state.get("state", state)
    if args.command == "profile":
        print(json.dumps(build_profile(state, game["ruleset"]), ensure_ascii=False, indent=2))
        return
    llm = make_llm(args)
    print(f"director: {type(llm).__name__} director={llm.model_for('director')} dialogue={llm.model_for('dialogue')}",
          file=sys.stderr, flush=True)
    director = Director(game, llm, args.retries, args.out)
    if args.command == "serve":
        access = AccessCodes(args.access_codes, usage_path=args.usage) if args.access_codes else None
        if access is not None and not access.codes:
            sys.exit(f"存取碼檔 {args.access_codes} 沒有任何存取碼：先用 python -m director.access add 新增")
        try:
            server = make_server(director, args.host, args.port, access, DecisionJudge.from_env(), args.cors_origin)
        except ValueError:
            sys.exit("架在 localhost 以外需要存取碼：加上 --access-codes codes.json")
        auth = f"，{len(access.codes)} 組存取碼" if access else "，不驗存取碼"
        auth += "，有決策模型" if os.environ.get("ZEWORLD_JUDGE_API_KEY") else "，沒有決策模型（排序用 priority）"
        print(f"director: serving {game['name']} on http://{args.host}:{server.server_port}{auth}", flush=True)
        server.serve_forever()
    else:
        followup = state.get("followups", [])[args.followup] if args.followup is not None else None
        try:
            pack = director.generate(state, args.save_id, followup, args.hint)
        except DirectorError as e:
            print(f"失敗：{e}", file=sys.stderr)
            for err in e.errors:
                print("  - " + err, file=sys.stderr)
            sys.exit(1)
        print(json.dumps(pack, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
