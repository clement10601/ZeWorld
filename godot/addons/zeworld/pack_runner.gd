## 執行一個內容包：出場角色、對話、階段推進、效果、結局。
## 引擎只發出訊號；畫面、戰鬥、掉落表換算都由遊戲接手。
extends RefCounted

const Conditions = preload("res://addons/zeworld/conditions.gd")

## actor 出場（內容包裡的 actor 定義原樣傳出）
signal actor_spawned(actor: Dictionary)
signal actor_despawned(actor_id: String)
## 顯示一句台詞。line = {speaker, speaker_name, text, live_rewrite, choices: [{index, text}]}
signal line_shown(node_id: String, line: Dictionary)
signal dialogue_ended
signal objective_changed(text: String)
## give_loot、grant_exp：數值由遊戲依自己的掉落表與階級換算
signal reward_requested(effect: Dictionary)
## play_vfx、teleport：純表現，由遊戲處理
signal presentation_requested(effect: Dictionary)
## result 為 complete、fail、abandon
signal finished(result: String)

var pack: Dictionary
var state
## 選用：即時改寫台詞，簽名 (text, node, pack, state) -> String。失敗或未設定時用預寫台詞。
var line_rewriter: Callable

var running := false
var result := ""
var stage_index := -1
var current_node := ""
var spawned: Dictionary = {}
var defeated: Dictionary = {}
var reached_nodes: Dictionary = {}
var reached_points: Dictionary = {}

var _actors: Dictionary = {}
var _visible_choices: Array = []
var _checking := false
var _dirty := false


func _init(p_pack: Dictionary, p_state) -> void:
	pack = p_pack
	state = p_state
	for a in pack.get("actors", []):
		_actors[a["id"]] = a


func start() -> void:
	running = true
	for a in pack.get("actors", []):
		if a.get("spawn", "on_start") == "on_start":
			_spawn(a["id"])
	_enter_stage(0)
	_check_progress()


## 遊戲回報發生的事：{"type": "actor_defeated", "actor": id} 或 {"type": "reach_point", "point": id}
func notify(event: Dictionary) -> void:
	if not running:
		return
	match event.get("type", ""):
		"actor_defeated":
			var id: String = event["actor"]
			if spawned.has(id) and not defeated.has(id):
				defeated[id] = true
				state.record_kill(_actors[id]["template"])
		"reach_point":
			reached_points[event["point"]] = true
	_check_progress()


## 沒有選項時前進到下一句；回傳 false 表示正在等玩家選擇。
func advance() -> bool:
	if current_node == "" or not _visible_choices.is_empty():
		return false
	var nxt: String = _node(current_node).get("next", "")
	if nxt != "":
		_show_node(nxt)
	else:
		current_node = ""
		dialogue_ended.emit()
	return true


## index 是 line_shown 給出的 choices 裡的 index。
func choose(index: int) -> void:
	for c in _visible_choices:
		if c["index"] == index:
			var choice: Dictionary = _node(current_node)["choices"][index]
			_visible_choices = []
			_apply_all(choice.get("effects", []))
			if running:
				_show_node(choice["next"])
			return
	push_warning("ZeWorld: choice %d is not available" % index)


func objective() -> String:
	var stages: Array = pack["stages"]
	return stages[stage_index]["objective"] if stage_index >= 0 and stage_index < stages.size() else ""


func _ctx() -> Dictionary:
	return {"defeated": defeated, "reached_nodes": reached_nodes, "reached_points": reached_points}


func _node(id: String) -> Dictionary:
	return pack.get("dialogue", {}).get("nodes", {}).get(id, {})


func _show_node(id: String) -> void:
	var node := _node(id)
	if node.is_empty():
		push_error("ZeWorld: dialogue node %s not found in %s" % [id, pack["id"]])
		return
	current_node = id
	reached_nodes[id] = true
	_visible_choices = []
	for i in node.get("choices", []).size():
		var c: Dictionary = node["choices"][i]
		if Conditions.all_true(c.get("show_if", []), state, _ctx()):
			_visible_choices.append({"index": i, "text": c["text"]})
	var text: String = node["text"]
	if node.get("live_rewrite", false) and line_rewriter.is_valid():
		var rewritten = line_rewriter.call(text, node, pack, state)
		if rewritten is String and rewritten != "":
			text = rewritten
	var speaker: String = node["speaker"]
	line_shown.emit(id, {
		"speaker": speaker,
		"speaker_name": _actors[speaker]["name"] if _actors.has(speaker) else speaker,
		"text": text,
		"live_rewrite": node.get("live_rewrite", false),
		"choices": _visible_choices.duplicate(true),
	})
	_apply_all(node.get("effects", []))
	_check_progress()


func _enter_stage(i: int) -> void:
	stage_index = i
	var st: Dictionary = pack["stages"][i]
	objective_changed.emit(st["objective"])
	_apply_all(st.get("on_enter", []))


## 階段推進。效果可能再觸發對話與推進，用 _dirty 迴圈取代遞迴。
func _check_progress() -> void:
	if _checking:
		_dirty = true
		return
	_checking = true
	_dirty = true
	while _dirty and running:
		_dirty = false
		var stages: Array = pack["stages"]
		var st: Dictionary = stages[stage_index]
		var fail_when: Array = st.get("fail_when", [])
		if not fail_when.is_empty() and Conditions.all_true(fail_when, state, _ctx()):
			_finish("fail")
			break
		if Conditions.all_true(st["complete_when"], state, _ctx()):
			_apply_all(st.get("on_complete", []))
			if not running:
				break
			if stage_index + 1 >= stages.size():
				_finish("complete")
				break
			_enter_stage(stage_index + 1)
			_dirty = true
	_checking = false


func _finish(p_result: String) -> void:
	if not running:
		return
	running = false
	result = p_result
	_apply_all(pack["outcomes"].get("on_" + p_result, []), true)
	state.seen_packs[pack["id"]] = state.day
	finished.emit(p_result)


func _spawn(id: String) -> void:
	if _actors.has(id) and not spawned.has(id):
		spawned[id] = true
		actor_spawned.emit(_actors[id])


## outcome 為 true 時是在結算結局，此時 end_pack 不再生效。
func _apply_all(effects: Array, outcome := false) -> void:
	for e in effects:
		if not running and not outcome:
			return
		_apply(e)


func _apply(e: Dictionary) -> void:
	match e["type"]:
		"set_flag":
			state.set_flag(e["key"], e["value"])
		"add_karma":
			state.add_karma(e["axis"], int(e["delta"]))
		"change_relation":
			state.change_relation(e["npc"], int(e["delta"]))
		"take_item":
			state.take_item(e["item"], int(e["count"]))
		"remember":
			state.remember(e["npc"], e["text"])
		"request_followup":
			state.followups.append({
				"hint": e["hint"],
				"after_days": int(e.get("after_days", 0)),
				"from_pack": pack["id"],
				"day": state.day,
			})
		"give_loot", "grant_exp":
			reward_requested.emit(e)
		"play_vfx", "teleport":
			presentation_requested.emit(e)
		"spawn_actor":
			_spawn(e["actor"])
		"despawn_actor":
			if spawned.erase(e["actor"]):
				actor_despawned.emit(e["actor"])
		"start_dialogue":
			_show_node(e["node"])
		"end_pack":
			_finish(e["result"])
		_:
			push_warning("ZeWorld: unknown effect type %s" % e["type"])
	_dirty = true
