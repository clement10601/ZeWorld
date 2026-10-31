"""導演與台詞模型的提示詞。規則用英文寫，產出語言跟隨遊戲清單的 language。"""
import json

from ._validate import validate
from .script import is_traditional

DIRECTOR_RULES = """You are the event director of a game built on the ZeWorld engine.
Write ONE new content pack as a single JSON object that matches the JSON Schema below. Output only the JSON, no prose, no code fences.

Rules:
- All player-facing text (title, summary, names, dialogue, objectives) is in {language}.
- Use only tier names, karma axes and times of day from the ruleset. meta.tier_range must include the player's current tier.
- Use only map chunks, points, map tags, actor templates and loot tables from the catalog. Points in map.points and map.spawn must belong to the chunks you choose.
- The trigger must be reachable from the player's current state in normal play (do not require flags nobody sets). Set trigger.once to true.
- Continue the story the player has actually lived: react to their karma, relations, memories, flags and player_profile.history (past events, how they ended and what the player chose). A recurring NPC keeps the same memory_key.
- Rewards follow the ruleset caps when present: loot_caps and exp_caps are keyed by meta.tier_range.min, max_loot_per_outcome limits give_loot effects per outcome.{banned}
- Keep it small: 1 to 3 stages, at most 12 dialogue nodes, at most 4 actors. Mark emotional lines of named NPCs with "live_rewrite": true.
- Every outcome you use should change the world (flags, relations, karma, memories) and may queue a request_followup for what could happen next.
- Pick a fresh id of the form pack.<short_snake_case_name>; it will be renamed.
- Stay inside the game's style guide.

Ruleset:
{ruleset}

Catalog:
{catalog}

Style guide:
{style}

JSON Schema:
{schema}
"""

EXAMPLE_INTRO = "Here is an existing pack from this game, for format and tone. Do not copy its story."


def catalog_text(catalog):
    lines = ["Map chunks (id: name, tags, points):"]
    for cid, c in catalog["chunks"].items():
        lines.append(f"- {cid}: {c.get('name', '')}; tags={c.get('tags', [])}; points={list(c.get('points', {}))}")
    lines.append("Actor templates:")
    for tid, t in catalog["actor_templates"].items():
        lines.append(f"- {tid}: {t.get('name', '')} — {t.get('description', '')}")
    if catalog.get("regions"):
        lines.append("Regions (meta.region; a pack set in a region uses only that region's chunks):")
        for rid, r in catalog["regions"].items():
            line = f"- {rid}: {r.get('name', '')} — {r.get('description', '')}; chunks={r.get('chunks', [])}"
            if r.get("wild"):
                # 遊戲地圖上的野怪：內容包可以用 killed 條件出「打倒 N 隻」的目標或觸發
                mobs = sorted({f"{w['template']}（{w.get('name', '')}，{w.get('chunk', '')}，{'/'.join(w.get('times', [])) or 'any time'}）" for w in r["wild"]})
                line += f"; wild monsters (usable in killed conditions)={mobs}"
            lines.append(line)
    lines.append("Loot tables (give_loot.tier picks common/rare/epic):")
    for lid in catalog["loot"]:
        lines.append(f"- {lid}")
    return "\n".join(lines)


def banned_rule(words):
    words = list(words)[:80]
    return "\n- Never use these words anywhere in player-facing text: " + "、".join(words) if words else ""


def director_messages(game, state, profile, request, example=None, taken_ids=(), lore_reference=None):
    system = DIRECTOR_RULES.format(
        language=game["language"],
        ruleset=json.dumps(game["ruleset"], ensure_ascii=False),
        catalog=catalog_text(game["catalog"]),
        style=game["style"] or "(none)",
        banned=banned_rule(game.get("banned_words", ())),
        schema=json.dumps(validate.SCHEMA, ensure_ascii=False),
    )
    msgs = [{"role": "system", "content": system}]
    if example:
        msgs.append({"role": "user", "content": EXAMPLE_INTRO + "\n" + json.dumps(example, ensure_ascii=False)})
        msgs.append({"role": "assistant", "content": "Understood. Send the player's state and the request."})
    # history 已整理進 player_profile，不再重複帶完整紀錄
    world = {k: v for k, v in state.items() if k != "history"}
    req = {"player_profile": profile, "world_state": world, "request": request}
    if lore_reference:
        req["lore_reference"] = lore_reference
    if taken_ids:
        req["pack_ids_already_used"] = sorted(taken_ids)
    msgs.append({"role": "user", "content": json.dumps(req, ensure_ascii=False)})
    return msgs


def repair_message(errors):
    return ("The pack was rejected by the validator. Fix every problem and send the whole corrected JSON again:\n- "
            + "\n- ".join(errors[:20]))


REWRITE_RULES = """You voice one NPC line in a game. Rewrite the authored line so it sounds like this speaker talking to this particular player, in {language}.
Hard rules:
- Keep exactly the same facts, intent and outcome. Never swap or blur who did what to whom (who attacked, who saved, who betrayed).
- Add nothing the authored line does not say: no new facts, promises, demands, items, names or places. The NPC's memories, the relation and the player's karma only colour tone and word choice; refer to a remembered event only if the authored line already refers to it.
- Output only the spoken words, one or two sentences: no speaker name, no narration such as "she said coldly", no quotation marks or brackets.
- Keep it about as long as the authored line, never longer than {max_chars} characters.{script_rule}"""

TRADITIONAL_RULE = "\n- Write Traditional Chinese characters only (繁體字, Taiwan usage). Never use Simplified characters."


def rewrite_limit(text):
    """改寫後台詞的長度上限，core 也用它判斷要不要退回原句。"""
    return max(int(len(text) * 1.5), len(text) + 12)


def rewrite_messages(game, text, actor, npc, profile, previous=None):
    ctx = {
        "speaker": {k: actor.get(k) for k in ("name", "title", "persona") if actor.get(k)},
        "what_this_npc_remembers_about_player": npc["memories"],
        "relation_to_player": npc["relation"],
        "player": {"tier": profile["tier"], "karma": profile["karma"]},
    }
    if previous:
        ctx["line_just_before"] = previous
    ctx["authored_line"] = text
    rules = REWRITE_RULES.format(language=game["language"], max_chars=rewrite_limit(text),
                                 script_rule=TRADITIONAL_RULE if is_traditional(game["language"]) else "")
    return [{"role": "system", "content": rules},
            {"role": "user", "content": json.dumps(ctx, ensure_ascii=False)}]


def script_message(chars):
    return ("Some player-facing text uses Simplified Chinese characters: " + "".join(chars)
            + ". The game is in Traditional Chinese. Replace them with their Traditional forms.")
