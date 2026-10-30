# ZeWorld

以 LLM 當「導演」的遊戲引擎。導演在後台產出結構化的**內容包**（事件、場景、對話），驗證後放進預生成池；遊戲中的**判斷器**在條件成立時挑出內容包、即時個人化後載入。戰鬥與移動仍由傳統引擎確定性執行。

設計文件：[ZeWorld 引擎架構設計](https://claude.ai/artifact/YFo5fGdzMztbgqgFeFBtxT)

引擎不含任何題材設定。遊戲提供自己的 `ruleset.json`（階級、karma 軸、時段名稱）、掉落表、地圖區塊與內容包。第一款遊戲是 [Zetas](https://github.com/clement10601/Zetas)（仙俠橫軸）。

## 目錄

| 路徑 | 內容 |
| --- | --- |
| `content-pack/schema.json` | 內容包 JSON Schema（目前 `schema_version` 0.2） |
| `content-pack/validate.py` | 驗證器：Schema + ruleset 名稱 + 內部參照；導演產出的內容包要過這關才能入池 |
| `content-pack/fixtures/` | 通用測試用 ruleset 與內容包 |
| `godot/addons/zeworld/` | Godot 4 執行期：世界狀態、條件判斷、判斷器、內容包執行器、內容包池 |
| `godot/tests/` | 引擎測試（無頭 Godot） |

## 測試

```sh
pip install jsonschema pytest
python -m pytest content-pack
godot --headless --path godot -s res://tests/run_tests.gd
```

## 在遊戲中使用

把 `godot/addons/zeworld` 放進遊戲專案的 `addons/zeworld`（Zetas 用 git submodule + `tools/sync_engine.py`）。引擎只發出訊號（角色出場、台詞、獎勵請求、表現請求、結局），畫面、戰鬥與數值換算由遊戲處理。

```gdscript
const WorldState = preload("res://addons/zeworld/world_state.gd")
const PackPool = preload("res://addons/zeworld/pack_pool.gd")
const TriggerJudge = preload("res://addons/zeworld/trigger_judge.gd")
const PackRunner = preload("res://addons/zeworld/pack_runner.gd")

var state = WorldState.new(PackPool.load_ruleset("res://content/ruleset.json"))
var pool = PackPool.load_dir("res://content/packs")
var pack = TriggerJudge.pick(pool, state, {"type": "enter_map"})
if not pack.is_empty():
    var runner = PackRunner.new(pack, state)
    runner.line_shown.connect(_on_line)
    runner.start()
```
