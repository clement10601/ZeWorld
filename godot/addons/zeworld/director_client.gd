## 導演服務（ZeWorld director/server.py）的用戶端。加進場景樹後使用。
## 連不上、逾時、回覆不合格都只發失敗訊號，遊戲照常用預寫內容進行。
## 服務位址：環境變數 ZEWORLD_DIRECTOR_URL，預設 http://127.0.0.1:8765。
extends Node

## 導演產出新內容包（已通過引擎驗證器）。followup 是當初送出的請求，主動請求時為空。
signal pack_generated(pack: Dictionary, followup: Dictionary)
signal pack_failed(followup: Dictionary, reason: String)
## 即時改寫的台詞；交給 PackRunner.apply_rewrite。退回原句時不發。
signal line_rewritten(node_id: String, text: String)

var base_url := "http://127.0.0.1:8765"
var pack_timeout_sec := 90.0
var rewrite_timeout_sec := 4.0


func _ready() -> void:
	var env := OS.get_environment("ZEWORLD_DIRECTOR_URL")
	if env != "":
		base_url = env


func request_pack(state, followup: Dictionary = {}, hint: String = "") -> void:
	var body := {"state": state.to_dict(), "save_id": state.save_id}
	if not followup.is_empty():
		body["followup"] = followup
	if hint != "":
		body["hint"] = hint
	_post("/v1/packs/generate", body, pack_timeout_sec, func(code: int, data):
		if code == 200 and data is Dictionary and data.get("pack") is Dictionary:
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
	var err := http.request(base_url + path, ["Content-Type: application/json"], HTTPClient.METHOD_POST, JSON.stringify(body))
	if err != OK:
		http.queue_free()
		done.call(0, null)
