## 導演用戶端端到端測試：連真的 director/server.py（假模型）。
## 由 director/test_e2e_godot.py 啟動服務並設定 ZEWORLD_DIRECTOR_URL 後執行。
extends SceneTree

const WorldState = preload("res://addons/zeworld/world_state.gd")
const DirectorClient = preload("res://addons/zeworld/director_client.gd")
const PackPool = preload("res://addons/zeworld/pack_pool.gd")

var fixtures := ProjectSettings.globalize_path("res://").path_join("../content-pack/fixtures")
var failures := 0


func _initialize() -> void:
	_run.call_deferred()


func expect(ok: bool, what: String) -> void:
	if not ok:
		failures += 1
		printerr("FAIL: " + what)


func _run() -> void:
	var client = DirectorClient.new()
	root.add_child(client)
	await process_frame
	var state = WorldState.new(PackPool.load_ruleset(fixtures.path_join("ruleset.json")))
	state.save_id = "save.e2e"
	var followup := {"hint": "The bandit gang holds a grudge.", "after_days": 5, "from_pack": "pack.test.rescue", "day": 0, "flag": "followup:pack.test.rescue", "status": ""}

	client.request_pack(state, followup)
	var got: Array = await client.pack_generated
	var pack: Dictionary = got[0]
	expect(pack["id"].begins_with("pack.gen."), "generated id")
	expect(pack["meta"]["personalized_for"] == "save.e2e", "personalized for save")
	expect(got[1]["flag"] == followup["flag"], "followup echoed back")

	client.request_rewrite("g1", "You cost me three men in that street.", pack, state)
	var line: Array = await client.line_rewritten
	expect(line == ["g1", "Well met again."], "rewritten line arrives")

	state.region = "region.test_town"
	expect(client.restock(state, [], 2) == 2, "restock asks for the shortfall")
	expect(client.restock(state, [], 2) == 0, "in-flight requests count as stock")
	var first: Array = await client.pack_generated
	expect(first[0]["meta"]["region"] == "region.test_town" and first[1].is_empty(), "restocked pack is set in the region")
	await client.pack_generated
	await process_frame
	expect(client.restocking("region.test_town") == 0, "in-flight count drops when packs arrive")

	client.base_url = "http://127.0.0.1:9"
	expect(client.restock(state, [], 1) == 1, "restock retries while stock is short")
	await client.pack_failed
	await process_frame
	expect(client.restock(state, [], 1) == 0, "failed restock cools down")

	client.request_pack(state)
	var failed: Array = await client.pack_failed
	expect(failed[1] == "director unreachable", "unreachable director reported")

	print("director e2e: %d failed" % failures)
	quit(1 if failures > 0 else 0)
