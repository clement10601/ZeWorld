"""判斷器第二層：規則篩選後的候選內容包，由決策模型（System One API，例如 TypeSafe Jev）依玩家經歷評分。

每個候選包一題 Score（0–4），同一次請求平行作答。分數是機率加權的期望值，遊戲拿來和 priority 合併排序。
模型只回分數，不生成文字；連不上或逾時就回錯誤，遊戲照 priority 排序。

環境變數：
  ZEWORLD_JUDGE_API_KEY   OpenRouter 或 TypeSafe 的金鑰（只放環境變數）
  ZEWORLD_JUDGE_BASE_URL  預設 https://openrouter.ai/api/v1（TypeSafe 直連用 https://api.typesafe.ai/v1）
  ZEWORLD_JUDGE_MODEL     預設 ~typesafe/jev-latest（TypeSafe 直連用 jev-latest）
"""
import json
import os
import urllib.error
import urllib.request

from .profile import build_profile

FIT_LEVELS = [
    "Does not fit: it ignores or contradicts what this player has lived, or repeats something they just did.",
    "Weak fit: generic, could happen to anyone, no link to this player's past.",
    "Some fit: loosely connected to the player's tier, place or recent events.",
    "Good fit: clearly follows from the player's choices, karma, relations or memories.",
    "Perfect fit: feels made for this player right now; it pays off something they did or someone they know.",
]
FIT_INSTRUCTIONS = ("How well would `candidates.{cid}` fit this player's story if it happened now? "
                    "Judge it against `player` (tier, karma, NPC relations and memories, past events and choices) "
                    "and `moment`.")
MAX_CANDIDATES = 24


class JudgeError(RuntimeError):
    pass


def candidate_view(pack):
    meta = pack.get("meta", {})
    return {
        "title": meta.get("title", ""),
        "summary": meta.get("summary", ""),
        "tags": meta.get("tags", []),
        "kind": pack.get("kind", ""),
        "characters": [a.get("name", a.get("id", "")) for a in pack.get("actors", []) if isinstance(a, dict)],
    }


def fit_request(state, ruleset, packs, moment=None):
    """組出 System One 請求本體（不含 model）。回傳 (body, 題目 id -> 內容包 id)。"""
    packs = packs[:MAX_CANDIDATES]
    ids = {f"c{i}": p.get("id", "") for i, p in enumerate(packs)}
    body = {
        "state": {
            "player": build_profile(state, ruleset),
            "moment": moment or {},
            "candidates": {cid: candidate_view(p) for cid, p in zip(ids, packs)},
        },
        "questions": {cid: {"type": "score", "instructions": FIT_INSTRUCTIONS.format(cid=cid), "criteria": FIT_LEVELS}
                      for cid in ids},
    }
    return body, ids


class DecisionJudge:
    DEFAULT_BASE = "https://openrouter.ai/api/v1"
    DEFAULT_MODEL = "~typesafe/jev-latest"

    def __init__(self, api_key, base_url=DEFAULT_BASE, model=DEFAULT_MODEL, timeout=5):
        self.api_key, self.base_url, self.model, self.timeout = api_key, base_url.rstrip("/"), model, timeout

    @classmethod
    def from_env(cls):
        key = os.environ.get("ZEWORLD_JUDGE_API_KEY")
        if not key:
            return None
        return cls(key, os.environ.get("ZEWORLD_JUDGE_BASE_URL", cls.DEFAULT_BASE),
                   os.environ.get("ZEWORLD_JUDGE_MODEL", cls.DEFAULT_MODEL))

    def ask(self, body):
        req = urllib.request.Request(self.base_url + "/systemone", json.dumps(dict(body, model=self.model)).encode(),
                                     {"Content-Type": "application/json", "Authorization": "Bearer " + self.api_key})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise JudgeError(f"HTTP {e.code}: {e.read()[:300].decode('utf-8', 'replace')}") from e
        except (OSError, ValueError) as e:
            raise JudgeError(str(e)) from e


class FakeJudge:
    """測試用：answers 是 {內容包 id: 分數} 或依題目回覆的函式。"""
    model = "fake-judge"

    def __init__(self, scores):
        self.scores = scores
        self.calls = []

    def ask(self, body):
        self.calls.append(body)
        cand = body["state"]["candidates"]
        return {"model": self.model, "usage": {"input_tokens": 0, "output_tokens": 0},
                "answers": {cid: {"type": "score", "score": float(self.scores.get(c["title"], 2)), "confidence": 1.0}
                            for cid, c in cand.items()}}


def rank(judge, state, ruleset, packs, moment=None):
    """回傳 {內容包 id: {"fit": 0–4, "confidence": 0–1}}。"""
    if not packs:
        return {}
    body, ids = fit_request(state, ruleset, packs, moment)
    reply = judge.ask(body)
    out = {}
    for cid, answer in reply.get("answers", {}).items():
        if cid in ids and answer.get("type") == "score":
            out[ids[cid]] = {"fit": round(float(answer["score"]), 3), "confidence": round(float(answer.get("confidence", 0)), 3)}
    return out
