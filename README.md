# ZeWorld

以 LLM 當「導演」的遊戲引擎。導演在後台產出結構化的**內容包**（事件、場景、對話），驗證後放進預生成池；遊戲中的**判斷器**在條件成立時挑出內容包、即時個人化後載入。戰鬥與移動仍由傳統引擎確定性執行。

設計文件：[ZeWorld 引擎架構設計](https://claude.ai/artifact/YFo5fGdzMztbgqgFeFBtxT)

引擎不含任何題材設定。遊戲提供自己的 `ruleset.json`（階級、karma 軸、時段名稱）、掉落表、地圖區塊與內容包。第一款遊戲是 [Zetas](https://github.com/clement10601/Zetas)（仙俠橫軸）。

## 目錄

| 路徑 | 內容 |
| --- | --- |
| `content-pack/schema.json` | 內容包 JSON Schema（目前 `schema_version` 0.2） |
| `content-pack/validate.py` | 驗證器：Schema + ruleset 名稱 + 內部參照；導演產出的內容包要過這關才能入池 |
| `content-pack/fixtures/` | 通用測試用遊戲：清單、ruleset、素材目錄、內容包 |
| `director/` | 導演服務（Python，只依賴 jsonschema）：產生內容包 → 驗證 → 重試 → 入池；即時台詞改寫；玩家側寫 |
| `godot/addons/zeworld/` | Godot 4 執行期：世界狀態（含存讀檔）、條件判斷、判斷器、內容包執行器、內容包池、導演用戶端 |
| `godot/tests/` | 引擎測試（無頭 Godot） |

## 遊戲清單 `zeworld.json`

遊戲根目錄放一份清單，告訴驗證器與導演去哪裡找遊戲資料（路徑相對於清單）：

```json
{
  "game": "zetas",
  "language": "zh-Hant",
  "ruleset": "content/ruleset.json",
  "chunks": "content/chunks.json",
  "loot": "content/loot.json",
  "actor_templates": "content/actors.json",
  "style": "content/style.md",
  "packs": "content/packs"
}
```

- `chunks`：`{區塊 id: {name, width, tags, points: {點 id: x}}}`。內容包的地圖、地點、`enter_map` 標籤只能用這裡有的。
- `loot`：`{掉落表 id: {common, rare, epic}}`。
- `actor_templates`：`{模板 id: {name, description}}`。導演只能用這些模板。
- `ruleset` 可加 `karma_labels: {軸: [負向名稱, 正向名稱]}`，玩家側寫會用來描述傾向。

`python content-pack/validate.py --game path/to/zeworld.json` 會檢查清單裡的所有內容包。

## 導演

```sh
export ZEWORLD_LLM_BASE_URL=http://your-litellm:4000/v1   # 任何 OpenAI 相容端點
export ZEWORLD_LLM_API_KEY=...                            # 本機模型可省略；金鑰只放環境變數
export ZEWORLD_DIRECTOR_MODEL=your-director-model
export ZEWORLD_DIALOGUE_MODEL=your-local-model                 # 省略時同導演模型
python -m director serve --game ../Zetas/zeworld.json --out ../Zetas/.generated
```

沒有模型時可用固定回覆離線跑：`python -m director serve --game ... --fake-pack some_pack.json --fake-line "..."`。

- `POST /v1/packs/generate`：送世界狀態與後續劇情請求，回傳通過驗證的內容包。引擎會改寫 id、標記 `personalized_for`，並把「等 after_days 天」的條件加進觸發器（執行器在 `request_followup` 時會立 `followup:<內容包 id>` 旗標）。
- `POST /v1/dialogue/rewrite`：依 NPC 設定、對玩家的記憶與關係改寫一句台詞；失敗、空白或過長都退回原句。
- Godot 端用 `director_client.gd`：先顯示預寫台詞，改寫結果到了再交給 `PackRunner.apply_rewrite`（玩家已翻頁就丟掉）。

## 測試

```sh
pip install jsonschema pytest
python -m pytest content-pack director        # 設 GODOT=/path/to/godot 會多跑 Godot ↔ 導演端到端測試
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
