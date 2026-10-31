## 條件判斷：內容包 schema 列舉的每一種 condition 在這裡有唯一的解讀。
## ctx 帶入執行中的資訊：
##   event          觸發當下的事件，例如 {"type": "tier_up", "to": "adept"}
##   defeated       已擊敗的 actor id
##   reached_nodes  已出現過的對話節點
##   reached_points 已抵達的地點
##   kills_at_start 內容包開始時的擊殺數（有它時 killed 只算之後打倒的）
extends RefCounted


static func all_true(conds: Array, state, ctx: Dictionary = {}) -> bool:
	for c in conds:
		if not check(c, state, ctx):
			return false
	return true


static func any_true(conds: Array, state, ctx: Dictionary = {}) -> bool:
	for c in conds:
		if check(c, state, ctx):
			return true
	return false


static func _compare(value: int, op: String, target: int) -> bool:
	return value >= target if op == ">=" else value <= target


static func check(c: Dictionary, state, ctx: Dictionary = {}) -> bool:
	match c.get("type", ""):
		"enter_map":
			return c["map_tag"] in state.map_tags
		"tier_at_least":
			var idx: int = state.tier_index_of(c["tier"])
			return idx >= 0 and state.tier_index >= idx
		"tier_up":
			var ev: Dictionary = ctx.get("event", {})
			return ev.get("type", "") == "tier_up" and ev.get("to", "") == c["to"]
		"flag":
			return state.flags.has(c["key"]) and state.flags[c["key"]] == c["equals"]
		"karma":
			return _compare(int(state.karma.get(c["axis"], 0)), c["op"], int(c["value"]))
		"relation":
			return _compare(int(state.relations.get(c["npc"], 0)), c["op"], int(c["value"]))
		"time_of_day":
			return state.time_of_day in c["in"]
		"killed":
			# 內容包進行中只算開始之後打倒的；觸發條件（沒有 kills_at_start）算累計
			var base := int(ctx.get("kills_at_start", {}).get(c["monster_tag"], 0))
			return int(state.kills.get(c["monster_tag"], 0)) - base >= int(c["count"])
		"has_item":
			return int(state.items.get(c["item"], 0)) >= int(c.get("count", 1))
		"reach_point":
			return ctx.get("reached_points", {}).has(c["point"])
		"dialogue_reached":
			return ctx.get("reached_nodes", {}).has(c["node"])
		"actor_defeated":
			return ctx.get("defeated", {}).has(c["actor"])
		"days_since_flag":
			if not state.flag_days.has(c["key"]):
				return false
			return state.day - int(state.flag_days[c["key"]]) >= int(c["days"])
	push_warning("ZeWorld: unknown condition type %s" % c.get("type", "?"))
	return false
