"""典籍參考：生成事件時依地區、階級、請求 tag 挑幾筆條目放進提示，讓導演借用名稱、形貌與典故。

條目格式（遊戲的 lore 資料夾，見 README）：name、summary、tags、quote{text,source,chapter,verified} 或
basis{source,note}、hooks{regions, planned_regions, tier_min, request_tags, idea}。
"""
import hashlib

LORE_NOTE = ("Reference material from the game's source texts. You may borrow names, looks and stories, "
             "keeping each one's defining traits. Never invent a source, quote or chapter, and do not put "
             "citations in player-facing text unless the style guide asks for it.")


def _eligible(e, region, tier_index, tiers):
    hooks = e.get("hooks", {})
    places = hooks.get("regions", []) + hooks.get("planned_regions", [])
    if region and places and region not in places:
        return False
    tier_min = hooks.get("tier_min")
    return not (tier_min in tiers and tiers.index(tier_min) > tier_index)


def _score(e, region, tag):
    hooks = e.get("hooks", {})
    s = 0
    if region and region in hooks.get("regions", []):
        s += 4
    elif region and region in hooks.get("planned_regions", []):
        s += 2
    if tag and (tag in hooks.get("request_tags", []) or tag in e.get("tags", [])):
        s += 3
    if e.get("quote", {}).get("verified"):
        s += 1
    return s


def select(lore, state, ruleset, region=None, tag=None, k=8, seed=""):
    """回傳最相關的 k 筆 (id, entry)。同分的依 seed 打散，換一天、換存檔就會換一批。"""
    if not lore or not lore.get("entries"):
        return []
    tiers = ruleset.get("tiers", [])
    tier_index = int(state.get("tier_index", 0))
    region = region or state.get("region") or None

    def jitter(key):
        return int(hashlib.sha1(f"{seed}:{key}".encode()).hexdigest()[:8], 16) / 0x100000000

    found = [(key, e) for key, e in lore["entries"].items() if _eligible(e, region, tier_index, tiers)]
    found.sort(key=lambda kv: -(_score(kv[1], region, tag) + jitter(kv[0])))
    return found[:k]


def cite(e, sources):
    q, b = e.get("quote"), e.get("basis")
    if q:
        src = sources.get(q.get("source"), {}).get("title", q.get("source", ""))
        where = f"{src} · {q['chapter']}" if q.get("chapter") else src
        return {"quote": q.get("text", ""), "source": where, "verified": bool(q.get("verified"))}
    if b:
        return {"based_on": sources.get(b.get("source"), {}).get("title", b.get("source", "")), "note": b.get("note", "")}
    return {}


def reference(items, sources):
    """放進導演請求的參考段落（JSON）。"""
    out = []
    for key, e in items:
        item = {"id": key, "name": e.get("name", ""), "summary": e.get("summary", "")}
        item.update(cite(e, sources))
        idea = e.get("hooks", {}).get("idea")
        if idea:
            item["usage_idea"] = idea
        out.append(item)
    return {"note": LORE_NOTE, "entries": out}
