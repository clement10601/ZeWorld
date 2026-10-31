"""導演主流程：產生 → 驗證 → 把錯誤丟回去重寫（最多 max_retries 次）→ 寫進池。"""
import datetime
import hashlib
import json
import re
from pathlib import Path

from . import prompts
from ._validate import validate
from .llm import LLMError
from .profile import build_profile, npc_view


class DirectorError(RuntimeError):
    def __init__(self, message, errors=()):
        super().__init__(message)
        self.errors = list(errors)


def extract_json(text):
    """從模型回覆取出第一個 JSON 物件，容忍 ```json 包裝與前後廢話。"""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in reply")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(obj, dict):
        raise ValueError("reply is not a JSON object")
    return obj


def followup_gate(followup):
    """後續劇情要等 after_days 天才觸發：用執行器在 request_followup 時立的旗標來計時。"""
    if not followup or not followup.get("flag"):
        return []
    return [{"type": "flag", "key": followup["flag"], "equals": True},
            {"type": "days_since_flag", "key": followup["flag"], "days": int(followup.get("after_days", 0))}]


class Director:
    def __init__(self, game, llm, max_retries=2, out_dir=None):
        self.game = game
        self.llm = llm
        self.max_retries = max_retries
        self.out_dir = Path(out_dir) if out_dir else None
        self.example = self._load_example()

    def _load_example(self):
        files = sorted(Path(self.game["packs_dir"]).glob("*.json"))
        return json.loads(files[0].read_text(encoding="utf-8")) if files else None

    def taken_ids(self):
        ids = set()
        dirs = [Path(self.game["packs_dir"])] + ([self.out_dir] if self.out_dir else [])
        for d in dirs:
            for f in d.glob("*.json") if d.exists() else []:
                try:
                    ids.add(json.loads(f.read_text(encoding="utf-8"))["id"])
                except (ValueError, KeyError):
                    pass
        return ids

    def generate(self, state, save_id="", followup=None, hint=None):
        profile = build_profile(state, self.game["ruleset"])
        request = {"kind": "followup" if followup else "fresh"}
        if followup:
            request.update(hint=followup["hint"], after_days=followup.get("after_days", 0),
                           from_pack=followup.get("from_pack"),
                           note="The engine adds the waiting-days gate to the trigger itself.")
        if hint:
            request["hint"] = hint
        taken = self.taken_ids()
        messages = prompts.director_messages(self.game, state, profile, request, self.example, taken)
        errors = []
        for _ in range(self.max_retries + 1):
            try:
                reply = self.llm.chat(messages, role="director")
            except LLMError as e:
                raise DirectorError(f"LLM call failed: {e}") from e
            try:
                pack = self.finalize(extract_json(reply), save_id, followup, taken)
                errors = validate.validate_pack(pack, self.game["ruleset"], self.game["catalog"])
            except ValueError as e:
                errors = [f"reply is not valid JSON: {e}"]
            if not errors:
                self._save(pack)
                return pack
            messages = messages + [{"role": "assistant", "content": reply},
                                   {"role": "user", "content": prompts.repair_message(errors)}]
        raise DirectorError(f"pack still invalid after {self.max_retries} retries", errors)

    def finalize(self, pack, save_id, followup, taken):
        """引擎負責的欄位不交給模型：版本、作者、存檔歸屬、id 唯一性、後續劇情的等待天數。"""
        pack["schema_version"] = validate.SCHEMA["properties"]["schema_version"]["const"]
        meta = pack.setdefault("meta", {})
        if isinstance(meta, dict):
            meta["author"] = "director"
            meta["source_model"] = self.llm.model_for("director")
            meta["created_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if save_id:
                meta["personalized_for"] = save_id
        name = re.sub(r"[^a-z0-9_]", "_", str(pack.get("id", "")).removeprefix("pack.").lower())[:32].strip("_") or "event"
        digest = hashlib.sha1(json.dumps(pack, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        n = 6
        while f"pack.gen.{name}.{digest[:n]}" in taken:
            n += 1
        pack["id"] = f"pack.gen.{name}.{digest[:n]}"
        trig = pack.get("trigger")
        if isinstance(trig, dict) and isinstance(trig.get("all"), list):
            trig["once"] = True
            gate = [c for c in followup_gate(followup) if c not in trig["all"]]
            trig["all"][:0] = gate
        return pack

    def _save(self, pack):
        if self.out_dir:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            (self.out_dir / (pack["id"] + ".json")).write_text(
                json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")

    def rewrite_line(self, text, node_id, pack, state):
        """即時台詞。任何失敗都退回原台詞，遊戲永遠有字可顯示。"""
        node = pack.get("dialogue", {}).get("nodes", {}).get(node_id, {})
        actor = next((a for a in pack.get("actors", []) if a["id"] == node.get("speaker")), None)
        if actor is None:
            return {"text": text, "fallback": True, "reason": "speaker is not an actor"}
        profile = build_profile(state, self.game["ruleset"])
        npc = npc_view(profile, actor.get("memory_key", ""))
        try:
            reply = self.llm.chat(prompts.rewrite_messages(self.game, text, actor, npc, profile),
                                  role="dialogue", temperature=0.8, max_tokens=200)
        except LLMError as e:
            return {"text": text, "fallback": True, "reason": str(e)}
        line = reply.strip().splitlines()[0].strip() if reply.strip() else ""
        line = line.strip("\"'「」『』“”")
        if not line:
            return {"text": text, "fallback": True, "reason": "empty reply"}
        if len(line) > max(2 * len(text), len(text) + 40):
            return {"text": text, "fallback": True, "reason": "reply too long"}
        return {"text": line, "fallback": False}
