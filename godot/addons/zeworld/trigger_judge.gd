## 判斷器第一層：規則篩選。從內容包池挑出「現在可以上場」的內容包，依 priority 排序。
## 第二層（小模型或向量相似度排序）之後接在 candidates() 的結果上。
extends RefCounted

const Conditions = preload("res://addons/zeworld/conditions.gd")


## event 是觸發當下的事件，例如 {"type": "enter_map"} 或 {"type": "tier_up", "to": "adept"}。
static func candidates(pool: Array, state, event: Dictionary = {}) -> Array:
	var out: Array = []
	for pack in pool:
		if _eligible(pack, state, event):
			out.append(pack)
	out.sort_custom(func(a, b): return a["trigger"].get("priority", 50) > b["trigger"].get("priority", 50))
	return out


## 回傳最適合的內容包，沒有則回傳空 Dictionary。
static func pick(pool: Array, state, event: Dictionary = {}) -> Dictionary:
	var c := candidates(pool, state, event)
	return c[0] if not c.is_empty() else {}


static func _eligible(pack: Dictionary, state, event: Dictionary) -> bool:
	var trig: Dictionary = pack["trigger"]
	var id: String = pack["id"]
	if state.seen_packs.has(id):
		if trig.get("once", true):
			return false
		var cooldown_days := ceili(float(trig.get("cooldown_hours", 0)) / 24.0)
		if state.day - int(state.seen_packs[id]) < cooldown_days:
			return false
	# 標了地區的內容包只在該地區上場；玩家沒有地區（遊戲不分地區）時不篩
	var region: String = pack["meta"].get("region", "")
	if region != "" and state.region != "" and region != state.region:
		return false
	var tiers: Dictionary = pack["meta"]["tier_range"]
	var lo: int = state.tier_index_of(tiers["min"])
	var hi: int = state.tier_index_of(tiers["max"])
	if state.tier_index < lo or state.tier_index > hi:
		# 升階事件當下以「升上去的階級」判斷，否則剛突破的玩家會被擋在範圍外
		if not (event.get("type", "") == "tier_up" and state.tier_index_of(event.get("to", "")) <= hi):
			return false
	var ctx := {"event": event}
	if not Conditions.all_true(trig["all"], state, ctx):
		return false
	var any_conds: Array = trig.get("any", [])
	return any_conds.is_empty() or Conditions.any_true(any_conds, state, ctx)
