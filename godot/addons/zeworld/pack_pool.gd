## 內容包池：從資料夾載入 JSON 內容包。
## 完整驗證在進池前由 content-pack/validate.py 負責；這裡只擋格式版本不符或缺欄位的檔案。
extends RefCounted

const SCHEMA_VERSION := "0.2"
const REQUIRED := ["schema_version", "id", "kind", "meta", "trigger", "stages", "outcomes"]


static func load_file(path: String) -> Dictionary:
	var data = JSON.parse_string(FileAccess.get_file_as_string(path))
	if not data is Dictionary:
		push_error("ZeWorld: %s is not a JSON object" % path)
		return {}
	for key in REQUIRED:
		if not data.has(key):
			push_error("ZeWorld: %s is missing %s" % [path, key])
			return {}
	if data["schema_version"] != SCHEMA_VERSION:
		push_error("ZeWorld: %s has schema_version %s, engine expects %s" % [path, data["schema_version"], SCHEMA_VERSION])
		return {}
	return data


static func load_dir(dir_path: String) -> Array:
	var pool: Array = []
	if not DirAccess.dir_exists_absolute(dir_path):
		return pool
	for file in DirAccess.get_files_at(dir_path):
		if file.ends_with(".json"):
			var pack := load_file(dir_path.path_join(file))
			if not pack.is_empty():
				pool.append(pack)
	return pool


static func load_ruleset(path: String) -> Dictionary:
	var data = JSON.parse_string(FileAccess.get_file_as_string(path))
	return data if data is Dictionary else {}


## 只留通用內容包與屬於這個存檔的個人化內容包。
static func for_save(pool: Array, save_id: String) -> Array:
	return pool.filter(func(p): return p["meta"].get("personalized_for", "") in ["", save_id])


## 池子裡還能給這位玩家用的內容包：沒玩過（或可重複）、階級範圍含目前階級、屬於 region。
## 補貨排程用它判斷某地區還剩多少存貨；不看觸發條件，那是判斷器的事。
static func stock(pool: Array, state, region: String) -> Array:
	return pool.filter(func(p):
		var meta: Dictionary = p["meta"]
		if meta.get("region", "") != region:
			return false
		if state.seen_packs.has(p["id"]) and p["trigger"].get("once", true):
			return false
		var lo: int = state.tier_index_of(meta["tier_range"]["min"])
		var hi: int = state.tier_index_of(meta["tier_range"]["max"])
		return state.tier_index >= lo and state.tier_index <= hi)


## 把導演產出的內容包存進資料夾（例如 user://generated），下次啟動用 load_dir 讀回。
static func save_pack(pack: Dictionary, dir_path: String) -> bool:
	DirAccess.make_dir_recursive_absolute(dir_path)
	var f := FileAccess.open(dir_path.path_join(pack["id"] + ".json"), FileAccess.WRITE)
	if f == null:
		return false
	f.store_string(JSON.stringify(pack, "  "))
	return true
