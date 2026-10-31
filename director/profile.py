"""玩家側寫 v0：從世界狀態整理出導演與台詞模型看得懂的摘要。只用通用欄位，不含題材知識。"""

LEAN = 10  # karma 絕對值達到這個數才算有傾向


def build_profile(state, ruleset):
    tiers = ruleset.get("tiers", [])
    i = int(state.get("tier_index", 0))
    labels = ruleset.get("karma_labels", {})
    karma = []
    for axis in ruleset.get("karma_axes", []):
        v = int(state.get("karma", {}).get(axis, 0))
        lean = None
        if abs(v) >= LEAN:
            pair = labels.get(axis, [f"-{axis}", f"+{axis}"])
            lean = pair[1] if v > 0 else pair[0]
        karma.append({"axis": axis, "value": v, "leaning": lean})
    rel = state.get("relations", {})
    mem = state.get("memories", {})
    npcs = [{"npc": k, "relation": int(v), "memories": mem.get(k, [])[-3:]}
            for k, v in sorted(rel.items(), key=lambda kv: -abs(kv[1]))[:5]]
    for k in mem:
        if k not in rel:
            npcs.append({"npc": k, "relation": 0, "memories": mem[k][-3:]})
    seen = sorted(state.get("seen_packs", {}).items(), key=lambda kv: kv[1])
    return {
        "tier": tiers[i] if i < len(tiers) else None,
        "day": state.get("day", 0),
        "karma": karma,
        "npcs": npcs,
        "total_kills": sum(int(v) for v in state.get("kills", {}).values()),
        "flags": state.get("flags", {}),
        "recent_events": [k for k, _ in seen[-5:]],
    }


def npc_view(profile, memory_key):
    """單一 NPC 對玩家的印象，給即時台詞用。"""
    for n in profile["npcs"]:
        if n["npc"] == memory_key:
            return n
    return {"npc": memory_key, "relation": 0, "memories": []}
