## 引擎測試。執行：godot --headless --path godot -s res://tests/run_tests.gd
## 用 content-pack/fixtures 的通用測試內容包，不依賴任何遊戲。
extends SceneTree

const WorldState = preload("res://addons/zeworld/world_state.gd")
const PackRunner = preload("res://addons/zeworld/pack_runner.gd")
const TriggerJudge = preload("res://addons/zeworld/trigger_judge.gd")
const PackPool = preload("res://addons/zeworld/pack_pool.gd")
const AssetLibrary = preload("res://addons/zeworld/asset_library.gd")
const Conditions = preload("res://addons/zeworld/conditions.gd")

var fixtures := ProjectSettings.globalize_path("res://").path_join("../content-pack/fixtures")
var ruleset: Dictionary
var pool: Array
var failures := 0
var checks := 0


func _initialize() -> void:
	ruleset = PackPool.load_ruleset(fixtures.path_join("ruleset.json"))
	pool = PackPool.load_dir(fixtures.path_join("packs"))
	expect(pool.size() == 2, "loads both fixture packs")
	for t in [
		"test_trigger_waits_for_days_and_night",
		"test_rescue_protect_path",
		"test_rescue_walk_away_path",
		"test_hidden_choice_needs_karma",
		"test_once_pack_not_repeated",
		"test_trial_on_tier_up",
		"test_live_rewrite_and_fallback",
		"test_async_rewrite",
		"test_followup_flag_and_generated_pack",
		"test_save_and_load",
		"test_history_records_choices",
		"test_region_filter_and_stock",
		"test_generated_pack_expires",
		"test_abort_and_game_data",
		"test_asset_library",
		"test_fits_reorder_candidates",
		"test_killed_counts_from_pack_start",
	]:
		call(t)
	print("%d checks, %d failed" % [checks, failures])
	quit(1 if failures > 0 else 0)


func expect(ok: bool, what: String) -> void:
	checks += 1
	if not ok:
		failures += 1
		printerr("FAIL: " + what)


func pack(id: String) -> Dictionary:
	for p in pool:
		if p["id"] == id:
			return p
	return {}


func rescue_ready_state():
	var s = WorldState.new(ruleset)
	s.set_flag("helped_stranger", true)
	s.day = 2
	s.time_of_day = "night"
	s.map_tags = ["town"]
	return s


## 執行一個內容包並收集它發出的訊號。
func run(p: Dictionary, s) -> Dictionary:
	var log := {"lines": [], "spawned": [], "rewards": [], "result": "", "runner": null}
	var r = PackRunner.new(p, s)
	r.line_shown.connect(func(id, line): log["lines"].append([id, line]))
	r.actor_spawned.connect(func(a): log["spawned"].append(a["id"]))
	r.reward_requested.connect(func(e): log["rewards"].append(e))
	r.finished.connect(func(res): log["result"] = res)
	log["runner"] = r
	return log


func test_trigger_waits_for_days_and_night() -> void:
	var s = rescue_ready_state()
	s.day = 1
	expect(TriggerJudge.pick(pool, s).is_empty(), "rescue not ready before 2 days")
	s.day = 2
	s.time_of_day = "day"
	expect(TriggerJudge.pick(pool, s).is_empty(), "rescue not ready in daytime")
	s.time_of_day = "night"
	expect(TriggerJudge.pick(pool, s).get("id", "") == "pack.test.rescue", "rescue picked at night after 2 days")


func test_rescue_protect_path() -> void:
	var s = rescue_ready_state()
	var log := run(pack("pack.test.rescue"), s)
	var r = log["runner"]
	r.start()
	expect(log["spawned"] == ["stranger"], "only on_start actors spawn at start")
	expect(r.current_node == "a1", "stage 1 opens dialogue a1")
	r.advance()
	expect(log["spawned"] == ["stranger", "bandit"], "bandit spawns on a2 effect")
	r.advance()
	var choices: Array = log["lines"].back()[1]["choices"]
	expect(choices.size() == 2, "betray choice hidden without order karma")
	r.choose(0)
	expect(s.karma["mercy"] == 5, "protect adds mercy karma")
	expect(r.objective() == "Defeat the bandit", "advances to stage 2")
	r.notify({"type": "actor_defeated", "actor": "bandit"})
	expect(log["result"] == "complete", "defeating bandit completes pack")
	expect(s.relations.get("npc.stranger") == 25, "relation +25")
	expect(s.flags.get("stranger_ally") == true, "ally flag set")
	expect(s.memories["npc.stranger"] == ["They fought for me."], "npc remembers")
	expect(log["rewards"].size() == 1 and log["rewards"][0]["loot_table"] == "loot.test", "loot handed to game")
	expect(s.followups.size() == 1 and s.followups[0]["from_pack"] == "pack.test.rescue", "followup queued for director")
	expect(s.kills.get("mob.test") == 1, "kill recorded by template")


func test_rescue_walk_away_path() -> void:
	var s = rescue_ready_state()
	var log := run(pack("pack.test.rescue"), s)
	var r = log["runner"]
	r.start()
	r.advance()
	r.advance()
	r.choose(1)
	expect(log["result"] == "abandon", "walking away ends pack as abandon")
	expect(s.relations.get("npc.stranger") == -30, "relation -30")
	expect(s.followups.size() == 1 and s.followups[0]["after_days"] == 10, "abandon followup queued")
	expect(not s.flags.has("stranger_ally"), "complete outcome not applied")
	r.notify({"type": "actor_defeated", "actor": "bandit"})
	expect(log["result"] == "abandon", "finished pack ignores later events")


func test_hidden_choice_needs_karma() -> void:
	var s = rescue_ready_state()
	s.add_karma("order", 40)
	var log := run(pack("pack.test.rescue"), s)
	var r = log["runner"]
	r.start()
	r.advance()
	r.advance()
	expect(log["lines"].back()[1]["choices"].size() == 3, "betray choice shown with order karma")
	r.choose(2)
	expect(log["result"] == "fail", "betray ends pack as fail")
	expect(s.flags.get("stranger_captured") == true, "fail outcome applied")
	expect(s.karma["order"] == 50, "betray adds order karma")


func test_once_pack_not_repeated() -> void:
	var s = rescue_ready_state()
	var r = PackRunner.new(pack("pack.test.rescue"), s)
	r.start()
	r.advance()
	r.advance()
	r.choose(1)
	expect(TriggerJudge.pick(pool, s).is_empty(), "once pack not picked again")


func test_trial_on_tier_up() -> void:
	var s = WorldState.new(ruleset)
	expect(TriggerJudge.pick(pool, s).is_empty(), "trial needs tier_up event")
	s.tier_index = 1
	var p := TriggerJudge.pick(pool, s, {"type": "tier_up", "to": "adept"})
	expect(p.get("id", "") == "pack.test.trial", "trial picked on tier_up to adept")
	var log := run(p, s)
	var r = log["runner"]
	var shows: Array = []
	r.presentation_requested.connect(func(e): shows.append(e["type"]))
	r.start()
	expect(log["spawned"].is_empty() and r.current_node == "", "nothing until player reaches square")
	r.notify({"type": "reach_point", "point": "pt.square"})
	expect(r.current_node == "m1" and log["spawned"] == ["mirror"], "reaching square opens dialogue and spawns boss")
	r.notify({"type": "actor_defeated", "actor": "mirror"})
	expect(log["result"] == "complete" and s.flags.get("trial_cleared") == true, "defeating mirror clears trial")
	expect(shows == ["teleport"], "teleport handed to game")
	expect(log["rewards"].size() == 1 and log["rewards"][0]["type"] == "grant_exp", "exp handed to game")


func test_live_rewrite_and_fallback() -> void:
	var s = rescue_ready_state()
	var log := run(pack("pack.test.rescue"), s)
	var r = log["runner"]
	r.line_rewriter = func(text, node, _p, _s): return "[rewritten] " + text
	r.start()
	expect(log["lines"][0][1]["text"] == "[rewritten] You helped me before.", "live_rewrite lines go through rewriter")
	r.line_rewriter = func(_t, _n, _p, _s): return ""
	r.advance()
	expect(log["lines"][1][1]["text"] == "Found you!", "non live_rewrite line untouched")
	var s2 = rescue_ready_state()
	var log2 := run(pack("pack.test.rescue"), s2)
	log2["runner"].line_rewriter = func(_t, _n, _p, _s): return ""
	log2["runner"].start()
	expect(log2["lines"][0][1]["text"] == "You helped me before.", "empty rewrite falls back to authored line")


func test_async_rewrite() -> void:
	var s = rescue_ready_state()
	var log := run(pack("pack.test.rescue"), s)
	var r = log["runner"]
	var updates := []
	r.line_updated.connect(func(id, line): updates.append([id, line]))
	r.start()
	expect(log["lines"][0][1]["text"] == "You helped me before.", "authored line shown first")
	expect(r.apply_rewrite("a1", "Friend, you saved me once."), "rewrite for current line accepted")
	expect(updates.size() == 1 and updates[0][1]["text"] == "Friend, you saved me once." and updates[0][1]["speaker_name"] == log["lines"][0][1]["speaker_name"], "line_updated carries full line")
	r.advance()
	expect(not r.apply_rewrite("a1", "too late"), "stale rewrite dropped after advancing")
	expect(updates.size() == 1, "no update for stale rewrite")


## 導演產出的後續內容包：引擎在 request_followup 時立旗標，導演把等待天數條件加在旗標上。
func test_followup_flag_and_generated_pack() -> void:
	var s = rescue_ready_state()
	var log := run(pack("pack.test.rescue"), s)
	var r = log["runner"]
	r.start()
	r.advance()
	r.advance()
	r.choose(0)
	r.notify({"type": "actor_defeated", "actor": "bandit"})
	var f: Dictionary = s.followups[0]
	expect(f["flag"] == "followup:pack.test.rescue" and s.flags.get(f["flag"]) == true, "followup sets its flag")
	expect(s.pending_followups().size() == 1, "followup pending")
	f["status"] = "requested"
	expect(s.pending_followups().is_empty(), "requested followup no longer pending")
	var gen = JSON.parse_string(FileAccess.get_file_as_string(fixtures.path_join("director/bandit_grudge.json")))
	gen["schema_version"] = "0.2"
	gen["id"] = "pack.gen.bandit_grudge.abc123"
	gen["meta"]["personalized_for"] = "save.test"
	gen["trigger"]["all"] = [{"type": "flag", "key": f["flag"], "equals": true}, {"type": "days_since_flag", "key": f["flag"], "days": 5}] + gen["trigger"]["all"]
	var dir := "user://zeworld_test_generated"
	expect(PackPool.save_pack(gen, dir), "generated pack saved")
	var loaded := PackPool.load_dir(dir)
	expect(loaded.size() == 1 and loaded[0]["id"] == gen["id"], "generated pack loads back")
	expect(PackPool.for_save(loaded, "save.other").is_empty(), "other saves do not see it")
	var mine := PackPool.for_save(loaded + pool, "save.test")
	expect(mine.size() == 3, "own and shared packs kept")
	s.map_tags = ["forest"]
	s.time_of_day = "night"
	expect(TriggerJudge.pick(mine, s, {"type": "enter_map"}).is_empty(), "generated pack waits out after_days")
	s.day += 5
	expect(TriggerJudge.pick(mine, s, {"type": "enter_map"}).get("id") == gen["id"], "generated pack triggers after the wait")
	DirAccess.remove_absolute(ProjectSettings.globalize_path(dir.path_join(gen["id"] + ".json")))
	DirAccess.remove_absolute(ProjectSettings.globalize_path(dir))


func test_save_and_load() -> void:
	var s = rescue_ready_state()
	s.save_id = "save.test"
	s.add_karma("order", 7)
	s.followups.append({"hint": "h", "after_days": 1, "from_pack": "p", "day": 2, "flag": "followup:p", "status": "requested"})
	var path := "user://zeworld_test_save.json"
	expect(s.save_file(path), "save written")
	var t = WorldState.new(ruleset)
	expect(t.load_file(path), "save read")
	expect(t.save_id == "save.test" and t.day == 2 and t.karma["order"] == 7 and t.flags["helped_stranger"] == true, "state round-trips")
	expect(t.followups[0]["status"] == "", "unanswered request goes back to pending")
	expect(not WorldState.new(ruleset).load_file("user://missing.json"), "missing save returns false")
	DirAccess.remove_absolute(ProjectSettings.globalize_path(path))


func test_history_records_choices() -> void:
	var s = rescue_ready_state()
	var log := run(pack("pack.test.rescue"), s)
	var r = log["runner"]
	r.start()
	r.advance()
	r.advance()
	var choice_text: String = log["lines"].back()[1]["choices"][1]["text"]
	r.choose(1)
	expect(s.history.size() == 1, "finished pack writes one history entry")
	var h: Dictionary = s.history[0]
	expect(h["pack"] == "pack.test.rescue" and h["result"] == "abandon" and h["day"] == 2, "history keeps pack, result, day")
	expect(h["choices"] == [choice_text], "history keeps the chosen line")
	expect(h["title"] == pack("pack.test.rescue")["meta"]["title"], "history keeps the title")
	for i in WorldState.HISTORY_LIMIT + 5:
		s.record_history({"day": i})
	expect(s.history.size() == WorldState.HISTORY_LIMIT and s.history.back()["day"] == WorldState.HISTORY_LIMIT + 4, "history keeps only the latest entries")
	var path := "user://zeworld_test_history.json"
	s.save_file(path)
	var t = WorldState.new(ruleset)
	t.load_file(path)
	expect(t.history.size() == WorldState.HISTORY_LIMIT, "history survives save and load")
	DirAccess.remove_absolute(ProjectSettings.globalize_path(path))


func test_region_filter_and_stock() -> void:
	var s = rescue_ready_state()
	var rescue: Dictionary = pack("pack.test.rescue").duplicate(true)
	rescue["meta"]["region"] = "region.test_town"
	var p := [rescue]
	expect(TriggerJudge.pick(p, s)["id"] == "pack.test.rescue", "player without region sees region packs")
	s.region = "region.test_trial"
	expect(TriggerJudge.pick(p, s).is_empty(), "region pack stays home")
	expect(PackPool.stock(p, s, "region.test_trial").is_empty(), "no stock in other region")
	s.region = "region.test_town"
	expect(TriggerJudge.pick(p, s)["id"] == "pack.test.rescue", "region pack fires at home")
	expect(PackPool.stock(p, s, "region.test_town").size() == 1, "unseen pack counts as stock")
	s.seen_packs["pack.test.rescue"] = 2
	expect(PackPool.stock(p, s, "region.test_town").is_empty(), "played once-pack is not stock")
	s.seen_packs.erase("pack.test.rescue")
	s.tier_index = 2
	expect(PackPool.stock(p, s, "region.test_town").is_empty(), "pack outside tier is not stock")


func test_generated_pack_expires() -> void:
	var s = rescue_ready_state()
	var rescue: Dictionary = pack("pack.test.rescue").duplicate(true)
	rescue["trigger"]["expires_after_days"] = 1
	var p := [rescue]
	expect(not s.is_expired(rescue), "pack without arrival never expires")
	s.note_arrival(rescue["id"])
	s.note_arrival(rescue["id"])
	expect(TriggerJudge.pick(p, s)["id"] == rescue["id"], "fresh pack fires on its arrival day")
	expect(PackPool.stock(p, s, "").size() == 1, "fresh pack counts as stock")
	s.day += 1
	expect(s.is_expired(rescue), "pack expires after its days")
	expect(TriggerJudge.pick(p, s).is_empty(), "expired pack does not fire")
	expect(PackPool.stock(p, s, "").is_empty() and PackPool.expired(p, s).size() == 1, "expired pack is not stock")
	expect(s.to_dict()["pack_arrivals"].has(rescue["id"]), "arrivals are saved")


func test_abort_and_game_data() -> void:
	var s = rescue_ready_state()
	var log := run(pack("pack.test.rescue"), s)
	var r = log["runner"]
	r.start()
	r.abort("fail")
	expect(log["result"] == "fail" and s.flags.get("stranger_captured") == true, "abort settles the fail outcome")
	r.abort("complete")
	expect(log["result"] == "fail", "abort after the end does nothing")
	s.game_data["exp"] = 42
	var t = WorldState.new(ruleset)
	t.load_dict(s.to_dict())
	expect(t.game_data.get("exp") == 42, "game data round-trips")


func test_asset_library() -> void:
	var dir := "user://zeworld_test_art"
	DirAccess.make_dir_recursive_absolute(dir)
	for n in ["a.png", "b.png", "generic.png"]:
		Image.create(4, 8, false, Image.FORMAT_RGBA8).save_png(dir.path_join(n))
	var manifest := dir.path_join("assets.json")
	var f := FileAccess.open(manifest, FileAccess.WRITE)
	f.store_string(JSON.stringify({"assets": {
		"npc.fox": {"type": "sprite", "files": ["a.png", "b.png"], "height": 120},
		"npc.generic": {"type": "sprite", "files": ["generic.png"]},
		"npc.todo": {"type": "sprite", "files": ["todo.png"]},
	}}))
	f.close()
	var lib = AssetLibrary.from_file(manifest)
	expect(lib.root == dir, "root defaults to the manifest folder")
	var first: String = lib.resolve("npc.fox", "npc.baiheng")
	expect(first != "" and lib.resolve("npc.fox", "npc.baiheng") == first, "same key always gets the same variant")
	var picks := {}
	for k in ["k1", "k2", "k3", "k4", "k5", "k6"]:
		picks[lib.resolve("npc.fox", k)] = true
	expect(picks.size() == 2, "different keys spread over the variants")
	expect(lib.resolve("npc.todo", "", "npc.generic").ends_with("generic.png"), "missing art falls back")
	expect(lib.resolve("npc.todo") == "" and lib.texture("npc.todo") == null, "no art and no fallback gives nothing")
	expect(lib.texture("npc.fox", "x") is Texture2D and lib.texture("npc.fox", "x").get_height() == 8, "texture loads from user://")
	expect(lib.missing() == ["npc.todo"], "missing lists ids without any file")
	expect(AssetLibrary.from_file("user://nope.json").assets.is_empty(), "missing manifest gives an empty library")
	for n in ["a.png", "b.png", "generic.png", "assets.json"]:
		DirAccess.remove_absolute(ProjectSettings.globalize_path(dir.path_join(n)))


func test_fits_reorder_candidates() -> void:
	var s = rescue_ready_state()
	var a: Dictionary = pack("pack.test.rescue").duplicate(true)
	var b: Dictionary = a.duplicate(true)
	b["id"] = "pack.test.rescue_b"
	b["trigger"]["priority"] = a["trigger"].get("priority", 50) + 5
	expect(TriggerJudge.pick([a, b], s)["id"] == "pack.test.rescue_b", "without fits priority decides")
	var fits := {"pack.test.rescue": 3.5, "pack.test.rescue_b": 1.0}
	expect(TriggerJudge.pick([a, b], s, {}, fits)["id"] == "pack.test.rescue", "a much better fit beats a small priority edge")
	expect(TriggerJudge.pick([a, b], s, {}, {"pack.test.rescue": 2.3})["id"] == "pack.test.rescue_b", "a slight fit edge does not")
	expect(TriggerJudge.pick([a, b], s, {}, {"pack.test.rescue_b": 0.0})["id"] == "pack.test.rescue_b", "a low fit never subtracts")
	b["trigger"]["priority"] = a["trigger"].get("priority", 50) + 15
	expect(TriggerJudge.pick([a, b], s, {}, {"pack.test.rescue": 4.0})["id"] == "pack.test.rescue_b", "fit bonus is capped below a large priority gap")


## killed：觸發條件算累計；內容包進行中只算開始之後打倒的，包含遊戲回報的內容包以外的怪。
func test_killed_counts_from_pack_start() -> void:
	var s = WorldState.new(ruleset)
	s.record_kill("mob.wolf")
	s.record_kill("mob.wolf")
	var cond := [{"type": "killed", "monster_tag": "mob.wolf", "count": 2}]
	expect(Conditions.all_true(cond, s), "outside a pack, killed counts every kill so far")
	var p := {"id": "pack.test.hunt", "meta": {"title": "hunt"}, "stages": [
		{"id": "s1", "objective": "hunt", "complete_when": cond}],
		"outcomes": {"on_complete": [], "on_fail": []}}
	var log := run(p, s)
	var r = log["runner"]
	r.start()
	expect(r.running and log["result"] == "", "earlier kills do not finish a kill objective")
	r.notify({"type": "monster_killed", "monster_tag": "mob.wolf"})
	expect(r.running and s.kills["mob.wolf"] == 3, "a reported outside kill is recorded")
	r.notify({"type": "monster_killed", "monster_tag": "mob.wolf"})
	expect(log["result"] == "complete", "two kills after the start finish the objective")
