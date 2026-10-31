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
  "regions": "content/regions.json",
  "style": "content/style.md",
  "banned_words": "content/banned_words.txt",
  "lore": "content/lore",
  "packs": "content/packs"
}
```

- `chunks`：`{區塊 id: {name, width, tags, points: {點 id: x}}}`。內容包的地圖、地點、`enter_map` 標籤只能用這裡有的。
- `loot`：`{掉落表 id: {common, rare, epic}}`。
- `actor_templates`：`{模板 id: {name, description}}`。導演只能用這些模板。
- `regions`（選用）：`{地區 id: {name, description, chunks: [區塊 id]}}`。內容包的 `meta.region` 必須在這裡，地圖只能用該地區的區塊。
- `banned_words`（選用）：文字檔，一行一個禁用詞（`#` 開頭是註解）。玩家看得到的文字含有禁用詞就驗證失敗。
- `ruleset` 可加 `karma_labels: {軸: [負向名稱, 正向名稱]}`，玩家側寫會用來描述傾向。
- `ruleset` 可加獎勵上限：`loot_caps`、`exp_caps`（`{階級: 最高檔次}`，依內容包 `tier_range.min` 套用，沒列的階級沿用較低階級）、`max_loot_per_outcome`（每個結局最多幾個 `give_loot`）。

`python content-pack/validate.py --game path/to/zeworld.json` 會檢查清單裡的所有內容包；有 `lore` 時也檢查條目的出處、地區與階級。

## 導演

```sh
pip install jsonschema anthropic

# Claude API（建議）：金鑰只放環境變數，不要寫進檔案
export ZEWORLD_ANTHROPIC_API_KEY=...
# 選用：預設導演 claude-opus-5-5、即時台詞 claude-sonnet-5-5
# export ZEWORLD_DIRECTOR_MODEL=claude-opus-5-5
# export ZEWORLD_DIALOGUE_MODEL=claude-sonnet-5-5

# 或任何 OpenAI 相容端點（LiteLLM、vLLM、Ollama），沒設 Claude 金鑰時才會用
# export ZEWORLD_LLM_BASE_URL=http://your-litellm:4000/v1
# export ZEWORLD_LLM_API_KEY=...
# export ZEWORLD_DIRECTOR_MODEL=your-director-model

python -m director serve --game ../Zetas/zeworld.json --out ../Zetas/.generated
```

刻意不讀 `ANTHROPIC_API_KEY`／`ANTHROPIC_BASE_URL`，避免誤用同一台機器上其他工具的金鑰。導演用 Opus 時開了伺服器端拒答備援（`fallbacks: "default"`）。

沒有模型時可用固定回覆離線跑：`python -m director serve --game ... --fake-pack some_pack.json --fake-line "..."`。

- `POST /v1/packs/generate`：送世界狀態與後續劇情請求，回傳通過驗證的內容包。引擎會改寫 id、標記 `personalized_for`，並把「等 after_days 天」的條件加進觸發器（執行器在 `request_followup` 時會立 `followup:<內容包 id>` 旗標）。
- 帶 `region` 的請求是補貨：導演把內容包設在該地區（引擎強制寫入 `meta.region`）。
- 請求可再帶 `tag`、`gate`、`expires_after_days`，都由引擎寫入，不靠模型：`tag` 進 `meta.request_tag`（讓遊戲認出是哪種請求，例如 `heart_demon`、`encounter`）；`gate` 是加在觸發器最前面的條件（例如 `[{"type": "tier_up", "to": "金丹"}]`）；`expires_after_days` 是進池後有效天數。遊戲收到導演的內容包時呼叫 `state.note_arrival(id)`，過期的內容包不再觸發也不算存貨（`PackPool.expired` 列出可清掉的）。
- 內容包結束時執行器會寫一筆因果紀錄到 `state.history`（標題、結局、玩家選了什麼），導演側寫帶最近 8 筆。
- `POST /v1/dialogue/rewrite`：依 NPC 設定、對玩家的記憶與關係、上一句對白改寫一句台詞；失敗、空白、過長（超過原句 1.5 倍）或繁中遊戲混進簡體字都退回原句。模型多加的旁白（「某某冷笑道：」）與引號會先去掉。
- 遊戲語言是繁體中文時（`language` 以 `zh-Hant` 開頭或含「繁體」），內容包裡的簡體字會當成驗證錯誤丟回導演重寫。字表在 `director/data/zh_simplified_only.txt`（由 OpenCC 產生，排除「里、伙、群」這類繁中也用的字）。
- Claude API 的系統提示與範例包有標提示快取，連續生成時大部分輸入 token 走快取價。

### 判斷器第二層：決策模型評分

規則篩選後還剩好幾個候選內容包時，導演可以請決策模型（System One API，預設是 OpenRouter 上的 TypeSafe Jev）依玩家經歷替每個候選包評分。分數範圍 0–4，代表這個事件此刻有多貼合這位玩家：是否接得上他的選擇、因果、關係和記憶。Jev 只回分數和機率，不生成文字，每次評分約 2k 輸入 token。

```sh
export ZEWORLD_JUDGE_API_KEY=...                       # OpenRouter 金鑰，只放環境變數
# 選用：TypeSafe 直連
# export ZEWORLD_JUDGE_BASE_URL=https://api.typesafe.ai/v1
# export ZEWORLD_JUDGE_MODEL=jev-latest
```

- `POST /v1/judge/rank` 回傳 `{scores: {內容包 id: {fit, confidence}}}`。沒設金鑰時回 503，模型失敗時回 502。用存取碼時，評分次數另外計算（`daily_judges`，預設每人每天 2000 次）。
- Godot 端：`DirectorClient.request_ranking(state, packs, moment)` 會發出 `ranking_ready(fits)`，把 `fits` 傳給 `TriggerJudge.pick(pool, state, event, fits)`。排序依據是 `priority + 5 × clamp(fit − 2, 0, 2)`：貼合度只加分不扣分，最多 +10，沒有分數的內容包算 2，所以沒接決策模型時等同純 priority 排序。實測 Jev 會偏好具體有戲的候選，即使前提沒發生，所以硬性前提要寫在 trigger 裡。
- 評分建議在進地區、過一天時先算好，觸發當下直接用快取，玩家不用等。
- 用真模型實測（會花額度）：`python -m director.judge_bench --game ../Zetas/zeworld.json --scenarios ../Zetas/tests/director/judge_scenarios.json --runs 3`，印出每個情境的排序、是否符合預期、延遲、輸入 token 與花費。加 `--fake` 只檢查情境檔。

### 架在網路上給測試者用：存取碼與用量上限

導演預設只聽 `127.0.0.1`。要給別台電腦連時，必須用存取碼，否則不肯啟動。存取碼檔只存雜湊，存取碼在新增時印一次：

```sh
python -m director.access add --file ~/zeworld-codes.json --name 阿明              # 預設每天 40 個事件、800 句台詞
python -m director.access add --file ~/zeworld-codes.json --name 小寶 --daily-packs 20
python -m director.access total --file ~/zeworld-codes.json --daily-packs 200     # 所有人合計的每日事件上限（預設 200）
python -m director.access list --file ~/zeworld-codes.json                        # 今天的用量
python -m director.access disable --file ~/zeworld-codes.json --name 阿明          # 停用（enable 恢復）
python -m director serve --game ../Zetas/zeworld.json --host 0.0.0.0 --access-codes ~/zeworld-codes.json
```

- 存取碼檔也可以用環境變數 `ZEWORLD_DIRECTOR_ACCESS_CODES` 指定。不要放進版控。用量記在旁邊的 `<檔名>.usage.json`，以 UTC 日期分天。存取碼檔唯讀時（例如掛 Kubernetes Secret），用 `--usage` 或環境變數 `ZEWORLD_DIRECTOR_USAGE` 把用量寫到別處。
- 遊戲送 `Authorization: Bearer <存取碼>`（`director_client.gd` 的 `access_code`，或環境變數 `ZEWORLD_DIRECTOR_CODE`）。沒帶或無效回 401，超過個人或合計上限回 429。遊戲端兩者都當成導演失敗，照常用手寫內容。
- 每次生成請求算一次（含驗證失敗的重寫，因為模型已經花了錢），台詞改寫每句算一次。
- 存取碼走 HTTP 標頭，公開網路上請放在 HTTPS 後面（例如 Caddy 反向代理或 Cloudflare Tunnel），不要直接開 HTTP 埠。

### 用真模型實測 `director.bench`

會花 API 額度，不在 CI 跑。情境檔放在遊戲 repo（例如 Zetas 的 `tests/director/scenarios.json`），每個情境是一份世界狀態、要處理的後續劇情請求，以及要試改寫的台詞。

```sh
python -m director.bench --game ../Zetas/zeworld.json --scenarios ../Zetas/tests/director/scenarios.json --runs 2 --out /tmp/bench
```

輸出：內容包通過率、一次就過的比例、平均嘗試次數、每包秒數、token（含快取讀取）、台詞改寫的退回數與秒數、簡體字混入數；`--out` 會寫 `report.json` 和產出的內容包，方便人工讀文筆。
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

### 補貨排程

`WorldState.region` 是玩家所在地區。標了 `meta.region` 的內容包只在該地區觸發（`region` 為空時不篩）。
`PackPool.stock(pool, state, region)` 回傳該地區、目前階級還沒玩過的內容包。在適當時機（進地圖、換日）呼叫：

```gdscript
director.restock(state, PackPool.for_save(pool, state.save_id), 3)  # 不足 3 個就請導演補
```

送出中的請求會算進存貨，不會重複請求；失敗後該地區冷卻 `restock_cooldown_sec`（預設 120 秒）。補來的內容包走 `pack_generated`。

### 素材庫

`AssetLibrary.from_file("res://content/assets.json")` 讀遊戲的素材清單。`texture(id, key, fallback)` 依素材 id 找圖，找不到就改找 fallback（對應內容包 `requires_assets` 的 fallback），兩者都沒有時回傳 `null`，由遊戲畫替代圖形。`files` 有多張時是變體：同一個 key（例如 NPC 的 `memory_key`）永遠拿到同一張，所以共用模板的不同 NPC 也能長得不一樣。`missing()` 列出清單裡還沒有檔案的素材，可以當美術待辦。

```json
{"root": "res://art", "assets": {"npc.fox_girl": {"type": "sprite", "files": ["sprites/fox_girl_a.png", "sprites/fox_girl_b.png"], "height": 120}}}
```
