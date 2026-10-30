## 世界狀態：內容包讀寫的唯一真相來源（單機版即存檔內容）。
## 題材名稱（tier、karma 軸、時段）來自遊戲提供的 ruleset。
extends RefCounted

var ruleset: Dictionary = {}

var day: int = 0
var time_of_day: String = ""
var tier_index: int = 0
var map_tags: Array = []

var flags: Dictionary = {}
var flag_days: Dictionary = {}   # flag key -> 設定當天的 day
var karma: Dictionary = {}       # axis -> int（-100..100）
var relations: Dictionary = {}   # npc memory_key -> int（-100..100）
var kills: Dictionary = {}       # monster tag -> count
var items: Dictionary = {}       # item id -> count
var memories: Dictionary = {}    # npc memory_key -> Array[String]
var followups: Array = []        # 交給導演的後續劇情請求
var seen_packs: Dictionary = {}  # pack id -> 結束時的 day


func _init(p_ruleset: Dictionary = {}) -> void:
	ruleset = p_ruleset
	for axis in ruleset.get("karma_axes", []):
		karma[axis] = 0
	var times: Array = ruleset.get("times_of_day", [])
	if not times.is_empty():
		time_of_day = times[0]


func tier_name() -> String:
	var tiers: Array = ruleset.get("tiers", [])
	return tiers[tier_index] if tier_index < tiers.size() else ""


func tier_index_of(tier: String) -> int:
	return ruleset.get("tiers", []).find(tier)


func set_flag(key: String, value) -> void:
	flags[key] = value
	flag_days[key] = day


func add_karma(axis: String, delta: int) -> void:
	karma[axis] = clampi(int(karma.get(axis, 0)) + delta, -100, 100)


func change_relation(npc: String, delta: int) -> void:
	relations[npc] = clampi(int(relations.get(npc, 0)) + delta, -100, 100)


func remember(npc: String, text: String) -> void:
	if not memories.has(npc):
		memories[npc] = []
	memories[npc].append(text)


func record_kill(tag: String) -> void:
	kills[tag] = int(kills.get(tag, 0)) + 1


func add_item(item: String, count: int = 1) -> void:
	items[item] = int(items.get(item, 0)) + count


func take_item(item: String, count: int) -> void:
	items[item] = max(0, int(items.get(item, 0)) - count)


func to_dict() -> Dictionary:
	return {
		"day": day, "time_of_day": time_of_day, "tier_index": tier_index, "map_tags": map_tags,
		"flags": flags, "flag_days": flag_days, "karma": karma, "relations": relations,
		"kills": kills, "items": items, "memories": memories, "followups": followups,
		"seen_packs": seen_packs,
	}


func load_dict(d: Dictionary) -> void:
	for key in d:
		if key in self:
			set(key, d[key])
