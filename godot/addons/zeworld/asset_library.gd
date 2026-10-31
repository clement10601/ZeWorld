## 素材庫：依素材 id 找圖，找不到時退回 fallback，再找不到回傳 null，由遊戲畫色塊之類的替代。
## 清單格式（遊戲提供，例如 content/assets.json）：
##   {"root": "res://art", "assets": {"<素材 id>": {"type": "sprite", "files": ["sprites/a.png", "sprites/a_2.png"], "height": 120}}}
## files 有多張時是變體：同一個 key（例如 NPC 的 memory_key）永遠拿到同一張，不同 NPC 共用模板也長得不一樣。
## 路徑相對於 root；也可以寫 res://、user:// 或絕對路徑。
extends RefCounted

var root := ""
var assets: Dictionary = {}
var _cache: Dictionary = {}


static func from_file(path: String) -> RefCounted:
	var lib = load("res://addons/zeworld/asset_library.gd").new()
	var data = JSON.parse_string(FileAccess.get_file_as_string(path)) if FileAccess.file_exists(path) else null
	if data is Dictionary:
		lib.root = data.get("root", path.get_base_dir())
		lib.assets = data.get("assets", {})
	return lib


## 素材資訊（清單裡的那一筆，沒有就是空 Dictionary）
func info(id: String) -> Dictionary:
	return assets.get(id, {})


## 回傳實際存在的檔案路徑：先找 id（依 key 挑變體），再找 fallback；都沒有回傳空字串。
func resolve(id: String, key: String = "", fallback: String = "") -> String:
	for candidate in [id, fallback]:
		if candidate == "":
			continue
		var files: Array = info(candidate).get("files", []).filter(func(f): return _exists(_full(f)))
		if not files.is_empty():
			var pick: int = 0 if key == "" else absi(key.hash()) % files.size()
			return _full(files[pick])
	return ""


func texture(id: String, key: String = "", fallback: String = "") -> Texture2D:
	var path := resolve(id, key, fallback)
	if path == "":
		return null
	if not _cache.has(path):
		_cache[path] = _load(path)
	return _cache[path]


## 清單裡有列、但一個檔案都還沒有的素材 id；ids 為空時檢查整份清單。
func missing(ids: Array = []) -> Array:
	var check: Array = ids if not ids.is_empty() else assets.keys()
	return check.filter(func(id): return resolve(id) == "")


func _full(f: String) -> String:
	return f if f.begins_with("res://") or f.begins_with("user://") or f.is_absolute_path() else root.path_join(f)


func _exists(path: String) -> bool:
	return ResourceLoader.exists(path) if path.begins_with("res://") else FileAccess.file_exists(path)


func _load(path: String) -> Texture2D:
	if path.begins_with("res://"):
		return load(path)
	var img := Image.load_from_file(path)
	return ImageTexture.create_from_image(img) if img != null else null
