"""導演與台詞模型的提示詞。規則用英文寫，產出語言跟隨遊戲清單的 language。"""
import json

from ._validate import validate

DIRECTOR_RULES = """You are the event director of a game built on the ZeWorld engine.
Write ONE new content pack as a single JSON object that matches the JSON Schema below. Output only the JSON, no prose, no code fences.

Rules:
- All player-facing text (title, summary, names, dialogue, objectives) is in {language}.
- Use only tier names, karma axes and times of day from the ruleset. meta.tier_range must include the player's current tier.
- Use only map chunks, points, map tags, actor templates and loot tables from the catalog. Points in map.points and map.spawn must belong to the chunks you choose.
- The trigger must be reachable from the player's current state in normal play (do not require flags nobody sets). Set trigger.once to true.
- Continue the story the player has actually lived: react to their karma, relations, memories and flags. A recurring NPC keeps the same memory_key.
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
    lines.append("Loot tables (give_loot.tier picks common/rare/epic):")
    for lid in catalog["loot"]:
        lines.append(f"- {lid}")
    return "\n".join(lines)


def director_messages(game, state, profile, request, example=None, taken_ids=()):
    system = DIRECTOR_RULES.format(
        language=game["language"],
        ruleset=json.dumps(game["ruleset"], ensure_ascii=False),
        catalog=catalog_text(game["catalog"]),
        style=game["style"] or "(none)",
        schema=json.dumps(validate.SCHEMA, ensure_ascii=False),
    )
    msgs = [{"role": "system", "content": system}]
    if example:
        msgs.append({"role": "user", "content": EXAMPLE_INTRO + "\n" + json.dumps(example, ensure_ascii=False)})
        msgs.append({"role": "assistant", "content": "Understood. Send the player's state and the request."})
    req = {"player_profile": profile, "world_state": state, "request": request}
    if taken_ids:
        req["pack_ids_already_used"] = sorted(taken_ids)
    msgs.append({"role": "user", "content": json.dumps(req, ensure_ascii=False)})
    return msgs


def repair_message(errors):
    return ("The pack was rejected by the validator. Fix every problem and send the whole corrected JSON again:\n- "
            + "\n- ".join(errors[:20]))


REWRITE_RULES = """You voice one NPC line in a game. Rewrite the authored line so it fits this player, in {language}.
Keep the same meaning, facts and intent. Do not add promises, items, names or plot the authored line does not have.
Reply with the line only: one or two sentences, no quotes, no speaker name."""


def rewrite_messages(game, text, actor, npc, profile):
    ctx = {
        "speaker": {k: actor.get(k) for k in ("name", "title", "persona") if actor.get(k)},
        "what_this_npc_remembers_about_player": npc["memories"],
        "relation_to_player": npc["relation"],
        "player": {"tier": profile["tier"], "karma": profile["karma"]},
        "authored_line": text,
    }
    return [{"role": "system", "content": REWRITE_RULES.format(language=game["language"])},
            {"role": "user", "content": json.dumps(ctx, ensure_ascii=False)}]
