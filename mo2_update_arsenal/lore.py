"""Deterministic Modhunter enemy themes grounded in MO2 mod identity/metadata.

Only the title and metadata supplied by an installed mod are interpreted. This
module deliberately never claims to have read ESP/NIF records or specific NPCs,
weapons or armor when MO2 has not provided those names. Those title-derived
encounters are explicitly marked as playful inferences.
"""
from __future__ import annotations

import re
from typing import Any

_THEME = {
    "weapon": ("⚔", "#ecbd86", "WIELDER", "Forged from an unfinished arsenal."),
    "armor": ("⛨", "#e3b8a1", "IRONCLAD", "A dented guardian of forgotten equipment."),
    "npc": ("♟", "#d7a7ef", "ECHO", "A familiar face twisted by an unfinished update."),
    "creature": ("♞", "#a4dfbd", "BEAST", "A restless beast from the unpatched wilds."),
    "magic": ("ϟ", "#a9c9fd", "HEXER", "Its unstable magic bleeds into the arena."),
    "quest": ("✥", "#e9d08d", "OATHBREAKER", "An unfinished legend refuses to stay buried."),
    "world": ("❖", "#a5d1b4", "WAYWARD", "The landscape itself has joined the hunt."),
    "animation": ("✧", "#eeb6cd", "DANCER", "A cursed performer caught between animations."),
    "framework": ("ᛟ", "#b6d7e1", "BUGWRAITH", "A roaming manifestation of unfinished code."),
}

_KIND_RE = {
    "weapon": r"\b(?:weapon|sword|blade|maul|mace|halberd|spear|dagger|warhammer|greatsword|axe|bow|crossbow|katana|scythe|rapier|gun|rifle|staff|staves|armament|quiver)s?\b",
    "armor": r"\b(?:armor|armour|helmet|cuirass|shield|gauntlet|boot|robe|cloak|cape|outfit|hood|clothing|dress|greave|bracer|glasses|mask|ring|necklace)s?\b",
    "npc": r"\b(?:follower|companion|npc|citizen|villager|voiced|husband|wife)\b",
    "creature": r"\b(?:dragon|creature|animal|wolf|skeever|goblin|troll|giant|bear|werewolf|vampire|dinosaur|theropod|beast|minion|mount)s?\b",
    "magic": r"\b(?:magic|spell|sorcery|enchant|destruction|conjuration|rune|arcane|illusion|alchemy|elemental|storm|frost)\b",
    "quest": r"\b(?:quest|adventure|story|chapter|fable|tale|mission|campaign)\b",
    "world": r"\b(?:landscape|city|cities|town|village|settlement|grass|mountain|forest|waterfall|river|tree|flora|snow|ice cave|worldspace|location|dungeon|castle|floe)s?\b",
    "animation": r"\b(?:animation|idle|motion|dance|gesture|pose|behavior|behaviour)\b",
    "framework": r"\b(?:engine|framework|plugin|skse|fix|patch|library|distributor|swapper|hud|interface|control|mcm|sse display tweaks|performance)\b",
}

_ALIASES = {
    "weapon": ("weapons", "weaponNames", "weaponsNames", "weapon"),
    "armor": ("armors", "armours", "armorNames", "armourNames", "outfits"),
    "npc": ("npcs", "npcNames", "followers", "characters"),
    "creature": ("creatures", "creatureNames", "actors"),
    "magic": ("spells", "spellNames", "magic"),
}


def _first_name(mod: dict[str, Any], kind: str) -> str:
    for container_key in ("contentEntities", "contentNames", "loreEntities"):
        names = mod.get(container_key)
        if not isinstance(names, dict):
            continue
        for alias in _ALIASES.get(kind, ()):
            values = names.get(alias)
            if isinstance(values, str):
                values = [values]
            if isinstance(values, list):
                for value in values:
                    if isinstance(value, str) and 2 <= len(value.strip()) <= 160:
                        return value.strip()
    return ""


def _clean_title(title: str, kind: str) -> str:
    # Do not strip story/NPC names before hyphens if they are the named work.
    cleaned = re.sub(r"\s*\[[^]]{0,60}\]", "", title).strip()
    cleaned = re.sub(r"\b(?:SSE|SE|AE|NG|ESPFE|ESL|HDT(?:-SMP)?|CBPC|CBBE|UNP|3BA|BOS|FSMP|XP32|VR|Standalone)\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\b(?:2k|4k|8k|16k|version\s*\d+(?:\.\d+)*|v\d+(?:\.\d+)*)\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*[-–—:]\s*(?:a\s+)?(?:follower|companion|weapon(?:s)?|armor|armour|outfit|quest|patch|overhaul|texture|mesh|fix|edition|port|replacer|remaster|expansion|collection)\b.*$", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+(?:Complex Material|Requirements|Texture[s]?|Patch(?:es)?|Replacer|Overhaul|Addon|Add-on|Expansion|Updated|Edition)\b.*$", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—:")
    if kind == "npc":
        # "Celuna - Dark Elf Follower" -> Celuna; named NPC remains intact.
        cleaned = re.split(r"\s+[-–—]\s+", cleaned, maxsplit=1)[0].strip()
    return cleaned or title.strip() or "An Unidentified Mod"


def describe_enemy_theme(mod: dict[str, Any]) -> dict[str, Any]:
    """Compose a *fictional* enemy persona from mod evidence, with provenance.

    Never substitute an inferred entity for verified in-game content. Exact item
    names are only called explicit when provided by a content index/manifest.
    """
    title = str(mod.get("name") or "An Unidentified Mod").strip()
    label = " ".join(str(v) for v in (title, mod.get("category") or "", mod.get("group") or "") if v)
    explicit = next(((kind, name) for kind in ("npc", "weapon", "armor", "creature", "magic")
                     if (name := _first_name(mod, kind))), None)
    if explicit:
        kind, subject = explicit
        provenance = "content-metadata"
    else:
        # Detect specific nouns before vague background keywords. The title wins
        # over the arbitrary MO2 category/section unless no signal is present.
        kind = next((k for k in ("npc", "weapon", "armor", "creature", "magic", "quest", "world", "animation", "framework")
                     if re.search(_KIND_RE[k], title, re.I)), None)
        if kind is None:
            kind = next((k for k in ("npc", "weapon", "armor", "creature", "magic", "quest", "world", "animation", "framework")
                         if re.search(_KIND_RE[k], label, re.I)), "framework")
        subject = _clean_title(title, kind)
        provenance = "title-inference"

    glyph, tint, role, flavor = _THEME[kind]
    if kind == "weapon":
        foes = [f"{subject} Reaver", f"Rustbound {subject} Duelist", f"{subject} Wielder"]
        boss = f"The {subject} Blade Tyrant"
    elif kind == "armor":
        foes = [f"Dented {subject} Sentinel", f"{subject} Clanker", f"{subject} Bulwark"]
        boss = f"The Ironclad {subject} Colossus"
    elif kind == "npc":
        foes = [f"Echo of {subject}", f"{subject}'s Lost Doppelgänger", f"The Unpatched {subject}"]
        boss = f"{subject}, the Unfinished"
    elif kind == "creature":
        foes = [f"Rabid {subject}", f"The Glitched {subject}", f"{subject} Broodling"]
        boss = f"The {subject} Broodmother"
    elif kind == "magic":
        foes = [f"{subject} Hexer", f"Spellbound {subject} Wraith", f"{subject} Sparkfiend"]
        boss = f"The Arch-Hexer of {subject}"
    elif kind == "quest":
        foes = [f"{subject} Oathbreaker", f"Lost {subject} Pilgrim", f"The {subject} Wanderer"]
        boss = f"The Unfinished Legend of {subject}"
    elif kind == "world":
        foes = [f"Mossbound {subject} Stalker", f"{subject} Wayfarer", f"Feral {subject} Guardian"]
        boss = f"The {subject} Worldroot"
    elif kind == "animation":
        foes = [f"{subject} Shadow-Dancer", f"Jittering {subject} Specter", f"The {subject} Stumbler"]
        boss = f"The Motionless {subject} Master"
    else:
        foes = [f"Patchstarved {subject} Wraith", f"{subject} Gremlin", f"Uncompiled {subject} Sentinel"]
        boss = f"The Unresolved {subject} Overlord"
    return {"kind": kind, "subject": subject, "glyph": glyph, "color": tint,
            "role": role, "flavor": flavor, "foes": foes, "bossTitle": boss,
            "provenance": provenance, "sourceMod": title}
