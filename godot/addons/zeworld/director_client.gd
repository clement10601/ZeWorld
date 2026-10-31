## 導演服務（ZeWorld director/server.py）的用戶端。加進場景樹後使用。
## 連不上、逾時、回覆不合格都只發失敗訊號，遊戲照常用預寫內容進行。
## 服務位址：環境變數 ZEWORLD_DIRECTOR_URL，預設 http://127.0.0.1:8765。
## 導演要存取碼時（架在網路上給測試者用），設 access_code 或環境變數 ZEWORLD_DIRECTOR_CODE。
extends Node

const PackPool = preload("res://addons/zeworld/pack_pool.gd")

## 導演產出新內容包（已通過引擎驗證器）。followup 是當初送出的請求，主動請求時為空。
signal pack_generated(pack: Dictionary, followup: Dictionary)
signal pack_failed(followup: Dictionary, reason: String)
## 即時改寫的台詞；交給 PackRunner.apply_rewrite。退回原句時不發。
signal line_rewritten(node_id: String, text: String)

var base_url := "http://127.0.0.1:8765"
var access_code := ""
var pack_timeout_sec := 90.0
var rewrite_timeout_sec := 4.0
## 補貨失敗後，同一地區等這麼久才再請求
var restock_cooldown_sec := 120.0

var _restocking: Dictionary = {}      # region -> 送出中的補貨請求數
var _restock_after: Dictionary = {}   # region -> 可再請求的 ticks_msec


func _ready() -> void:
	var env := OS.get_environment("ZEWORLD_DIRECTOR_URL")
	if env != "":
		base_url = env
	var code := OS.get_environment("ZEWORLD_DIRECTOR_CODE")
	if code != "":
		access_code = code


## options（選用）：tag 寫進 meta.request_tag，gate 是加在觸發器最前面的條件陣列，
## expires_after_days 是進池後的有效天數。都由引擎強制寫入，不靠模型自己寫。
func request_pack(state, followup: Dictionary = {}, hint: String = "", region: String = "", options: Dictionary = {}) -> void:
	_generate(state, followup, hint, region, Callable(), options)


## 補貨排程：玩家目前地區、階級可用的內容包少於 target 時，請導演補到 target。
## 已送出未回的請求算進存貨，不會重複請求；失敗後該地區冷卻 restock_cooldown_sec。
## 回傳這次送出的請求數。產出的內容包一樣走 pack_generated（followup 為空）。
func restock(state, pool: Array, target: int) -> int:
	var region: String = state.region
	if Time.get_ticks_msec() < int(_restock_after.get(region, 0)):
		return 0
	var need := maxi(0, target - PackPool.stock(pool, state, region).size() - restocking(region))
	for i in need:
		_restocking[region] = restocking(region) + 1
		_generate(state, {}, "", region, func(ok: bool):
			_restocking[region] = restocking(region) - 1
			if not ok:
				_restock_after[region] = Time.get_ticks_msec() + int(restock_cooldown_sec * 1000))
	return need


## 送出中的補貨請求數
func restocking(region: String) -> int:
	return int(_restocking.get(region, 0))


func _generate(state, followup: Dictionary, hint: String, region: String, done: Callable, options: Dictionary = {}) -> void:
	var body := {"state": state.to_dict(), "save_id": state.save_id}
	for key in ["tag", "gate", "expires_after_days"]:
		if options.has(key):
			body[key] = options[key]
	if not followup.is_empty():
		body["followup"] = followup
	if hint != "":
		body["hint"] = hint
	if region != "":
		body["region"] = region
	_post("/v1/packs/generate", body, pack_timeout_sec, func(code: int, data):
		var ok: bool = code == 200 and data is Dictionary and data.get("pack") is Dictionary
		if done.is_valid():
			done.call(ok)
		if ok:
			pack_generated.emit(data["pack"], followup)
		else:
			var reason: String = data.get("error", "") if data is Dictionary else ""
			pack_failed.emit(followup, "HTTP %d %s" % [code, reason] if code else "director unreachable"))


func request_rewrite(node_id: String, text: String, pack: Dictionary, state) -> void:
	var body := {"text": text, "node_id": node_id, "pack": pack, "state": state.to_dict()}
	_post("/v1/dialogue/rewrite", body, rewrite_timeout_sec, func(code: int, data):
		if code == 200 and data is Dictionary and not data.get("fallback", true):
			line_rewritten.emit(node_id, str(data.get("text", ""))))


func _post(path: String, body: Dictionary, timeout: float, done: Callable) -> void:
	var http := HTTPRequest.new()
	http.timeout = timeout
	add_child(http)
	http.request_completed.connect(func(result: int, code: int, _headers, bytes: PackedByteArray):
		http.queue_free()
		if result != HTTPRequest.RESULT_SUCCESS:
			done.call(0, null)
		else:
			done.call(code, JSON.parse_string(bytes.get_string_from_utf8())))
	var headers := ["Content-Type: application/json"]
	if access_code != "":
		headers.append("Authorization: Bearer " + access_code)
	var err := http.request(base_url + path, headers, HTTPClient.METHOD_POST, JSON.stringify(body))
	if err != OK:
		http.queue_free()
		done.call(0, null)
