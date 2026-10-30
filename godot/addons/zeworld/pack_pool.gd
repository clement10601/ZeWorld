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
	for file in DirAccess.get_files_at(dir_path):
		if file.ends_with(".json"):
			var pack := load_file(dir_path.path_join(file))
			if not pack.is_empty():
				pool.append(pack)
	return pool


static func load_ruleset(path: String) -> Dictionary:
	var data = JSON.parse_string(FileAccess.get_file_as_string(path))
	return data if data is Dictionary else {}
