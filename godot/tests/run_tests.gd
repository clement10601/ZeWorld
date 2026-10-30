## 引擎測試。執行：godot --headless --path godot -s res://tests/run_tests.gd
## 用 content-pack/fixtures 的通用測試內容包，不依賴任何遊戲。
extends SceneTree

const WorldState = preload("res://addons/zeworld/world_state.gd")
const PackRunner = preload("res://addons/zeworld/pack_runner.gd")
const TriggerJudge = preload("res://addons/zeworld/trigger_judge.gd")
const PackPool = preload("res://addons/zeworld/pack_pool.gd")

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
