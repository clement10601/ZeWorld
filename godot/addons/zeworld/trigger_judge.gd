## 判斷器。第一層是規則篩選：從內容包池挑出「現在可以上場」的內容包。
## 第二層是決策模型給的貼合度 fits（DirectorClient.request_ranking 事先算好）：{內容包 id: 0–4}。
## 貼合度只當加分：高於中間值 2 才加，低於不扣，所以排序主體仍是 priority，模型只在接近的候選間分高下。
## 實測 Jev 會偏好「具體、有戲」的候選，即使前提沒發生（ZeWorld judge_bench），硬性前提交給第一層規則。
extends RefCounted

const Conditions = preload("res://addons/zeworld/conditions.gd")

## 貼合度高於中間值每 1 級，加這麼多 priority；最多加 2 級（+10）
const FIT_WEIGHT := 5.0
const NEUTRAL_FIT := 2.0


## event 是觸發當下的事件，例如 {"type": "enter_map"} 或 {"type": "tier_up", "to": "adept"}。
static func candidates(pool: Array, state, event: Dictionary = {}, fits: Dictionary = {}) -> Array:
	var out: Array = []
	for pack in pool:
		if _eligible(pack, state, event):
			out.append(pack)
	out.sort_custom(func(a, b): return rank_of(a, fits) > rank_of(b, fits))
	return out


## 回傳最適合的內容包，沒有則回傳空 Dictionary。
static func pick(pool: Array, state, event: Dictionary = {}, fits: Dictionary = {}) -> Dictionary:
	var c := candidates(pool, state, event, fits)
	return c[0] if not c.is_empty() else {}


static func rank_of(pack: Dictionary, fits: Dictionary) -> float:
	var fit := float(fits.get(pack["id"], NEUTRAL_FIT))
	return float(pack["trigger"].get("priority", 50)) + FIT_WEIGHT * clampf(fit - NEUTRAL_FIT, 0.0, 2.0)


static func _eligible(pack: Dictionary, state, event: Dictionary) -> bool:
	var trig: Dictionary = pack["trigger"]
	var id: String = pack["id"]
	if state.seen_packs.has(id):
		if trig.get("once", true):
			return false
		var cooldown_days := ceili(float(trig.get("cooldown_hours", 0)) / 24.0)
		if state.day - int(state.seen_packs[id]) < cooldown_days:
			return false
	if state.is_expired(pack):
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
