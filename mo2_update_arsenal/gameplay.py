"""Per-profile, local-only achievement ledger with MO2-backed update evidence.

Browser preferences/notes are distinct from authoritative score. No browser input
can claim XP or a victory. All mod identity keys are profile-scoped and stable.
"""
from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .scanner import version_verdict


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# Economic credits are minted only by deduplicated native MO2 version-update events.
# XP and cosmetic checklist clicks do not influence the wallet.
REALM_COINS_PER_UPDATE = 10
BOSS_KILL_POINTS = 100  # Cosmetic/run scoring is client-side.
BOSS_UPDATE_BONUS_POINTS = 200  # Native verified update awards twice boss points.
BOSS_UPDATE_BONUS_SIGILS = 10  # A marked update earns 20 total, instead of 10.
REALM_ITEMS = {
    "rift-key": {"cost": 4, "name": "Rift Key", "repeat": True, "kind": "run"},
    "supplies": {"cost": 3, "name": "Survival Supply Crate", "repeat": True, "kind": "supplies"},
    "steel-blade": {"cost": 8, "name": "Runesteel Blade", "repeat": False, "kind": "equipment"},
    "hunter-bow": {"cost": 7, "name": "Huntsman's Bow", "repeat": False, "kind": "equipment"},
    "ember-heart": {"cost": 10, "name": "Emberheart Spell", "repeat": False, "kind": "spell"},
    "winter-cloak": {"cost": 6, "name": "Winter Cloak", "repeat": False, "kind": "equipment"},
    "hearth-hall": {"cost": 18, "name": "Hearth Hall", "repeat": False, "kind": "camp"},
    "wolf-companion": {"cost": 14, "name": "Frostpaw Companion", "repeat": False, "kind": "companion"},
}

BADGES = (
    ("first-blood", "First Blood", "Complete your first verified MO2 update", 1, "⚔"),
    ("apprentice", "Apprentice Smith", "Finish 5 verified updates", 5, "🔨"),
    ("veteran", "Veteran of the Forge", "Finish 10 verified updates", 10, "🛡"),
    ("archmage", "Master Enchanter", "Finish 25 verified updates", 25, "✦"),
    ("dragonborn", "Dragonborn", "Finish 50 verified updates", 50, "🐉"),
    ("legend", "Legendary Smith", "Finish 100 verified updates", 100, "♛"),
)


class Chronicle:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self._source_revisions: dict[str, int] = {}
        self.data = {"schemaVersion": 1, "profiles": {}}
        try:
            found = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(found, dict) and found.get("schemaVersion") == 1 and isinstance(found.get("profiles"), dict):
                self.data = found
        except (OSError, ValueError, TypeError):
            pass

    def _profile(self, profile_id: str) -> dict[str, Any]:
        return self.data["profiles"].setdefault(profile_id, {"progress": {}, "events": [], "score": 0, "bestCombo": 0, "achievements": [], "victories": [], "lastCrownedSequence": 0})

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".pending")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, self.path)

    def progress(self, profile_id: str) -> dict:
        with self.lock:
            return deepcopy(self._profile(profile_id).get("progress") or {})

    def update_count(self, profile_id: str) -> int:
        with self.lock:
            return len(self._profile(profile_id)["events"])

    def source_links(self, profile_id: str, item_ids: list[str] | None = None) -> dict[str, str]:
        """Read just explicit source links, without cloning notes/game progress."""
        with self.lock:
            rows = (self._profile(profile_id).get("progress") or {}).get("rows") or {}
            keys = rows if item_ids is None else item_ids
            return {key: rows[key]["url"] for key in keys
                    if isinstance(rows.get(key), dict) and isinstance(rows[key].get("url"), str)
                    and rows[key]["url"]}

    def source_revision(self, profile_id: str) -> int:
        with self.lock:
            return self._source_revisions.get(profile_id, 0)

    def persist(self) -> None:
        """Persist accepted in-memory changes outside the service execution lock."""
        with self.lock:
            self._write()

    def set_progress(self, profile_id: str, rows: dict, prefs: dict, runtime: str, valid_ids: set[str],
                     *, persist=True) -> dict:
        """User-editable progress cannot mutate scores, badges, or native event evidence."""
        if not isinstance(rows, dict) or not isinstance(prefs, dict):
            return {"ok": False, "error": "Invalid progress document"}
        sanitized = {}
        for mid, row in rows.items():
            if mid not in valid_ids or not isinstance(row, dict):
                continue
            item = {}
            for k in ("status", "compat", "note", "url", "installed", "testedVersion", "updatedAt", "observedInstalledVersion", "lastOpened"):
                if isinstance(row.get(k), str):
                    item[k] = row[k][:8192 if k == "note" else 1024]
            for k in ("favorite", "mo2Source"):
                if isinstance(row.get(k), bool):
                    item[k] = row[k]
            if item.get("status") not in (None, "done", "review", "pending", "skipped"):
                item["status"] = "pending"
            sanitized[mid] = item
        safe_prefs = {}
        for k in ("filter", "query", "sort", "theme", "density", "effect", "sound", "focus", "collapsed", "autoCheckOnLaunch"):
            v = prefs.get(k)
            if k == "autoCheckOnLaunch" and not isinstance(v, bool):
                continue
            if isinstance(v, (bool, str)):
                safe_prefs[k] = v[:300] if isinstance(v, str) else v
            elif k == "collapsed" and isinstance(v, list):
                safe_prefs[k] = [str(x)[:100] for x in v if isinstance(x, str)]
        with self.lock:
            previous_links = self.source_links(profile_id)
            self._profile(profile_id)["progress"] = {"rows": sanitized, "prefs": safe_prefs, "verifiedRuntime": str(runtime)[:100]}
            if previous_links != self.source_links(profile_id):
                self._source_revisions[profile_id] = self._source_revisions.get(profile_id, 0) + 1
            if persist:
                self._write()
        return {"ok": True, "savedRows": len(sanitized), "profileId": profile_id}

    @staticmethod
    def _score(event_count: int, last_event: dict | None, now: str) -> tuple[int, int]:
        # A combo is a convenience reward, not an endless reward loop.
        combo = 1
        if last_event:
            try:
                then = datetime.fromisoformat(last_event["at"])
                diff = (datetime.fromisoformat(now) - then).total_seconds()
                if 0 <= diff <= 1800:
                    combo = min(20, int(last_event.get("combo", 1)) + 1)
            except (ValueError, TypeError, KeyError):
                pass
        return 100 + (combo - 1) * 20, combo

    def record_update(self, profile_id: str, mod: dict, before: str, after: str, evidence: str, artifact: str = "") -> dict:
        """Only called from the MO2 plugin or native snapshot observer."""
        if not isinstance(mod, dict) or not str(mod.get("id", "")).startswith("live-"):
            return {"ok": False, "reason": "Not a live mod"}
        if evidence == "version-observed":
            if version_verdict(before, after) != "candidate":
                return {"ok": False, "reason": "No demonstrable version upgrade"}
        elif evidence == "mo2-install-complete":
            # MO2 confirms that an installer returned, not that a *newer*
            # version was installed. A reinstall must never mint ranked XP.
            if version_verdict(before, after) != "candidate":
                return {"ok": False, "reason": "Successful install is not a verified version upgrade"}
        else:
            return {"ok": False, "reason": "Not verified MO2 evidence"}
        mid = str(mod["id"])
        stamp = utcnow()
        # Version changes are unique even if another event path observes them.
        unique = f"{mid}|{after}"
        with self.lock:
            p = self._profile(profile_id)
            if any(e["fingerprint"] == unique for e in p["events"]):
                return {"ok": False, "reason": "Already awarded"}
            # A successful install can trigger a second versionChanged observation.
            if evidence == "version-observed" and any(e["modId"] == mid and e["after"] == after and e["evidence"] == "mo2-install-complete" for e in p["events"]):
                return {"ok": False, "reason": "Already awarded for this MO2 install"}
            xp, combo = self._score(len(p["events"]), p["events"][-1] if p["events"] else None, stamp)
            event = {"sequence": len(p["events"]) + 1, "fingerprint": unique, "modId": mid, "name": str(mod.get("name", mid)), "before": str(before), "after": str(after), "evidence": evidence, "at": stamp, "xp": xp, "combo": combo}
            eligible = next((b for b in p.get("bossBounties", []) if b.get("modId") == mid
                             and b.get("before") == str(before) and b.get("status") == "defeated"), None)
            if eligible:
                eligible.update({"status": "forged", "forgedAt": stamp, "installedAfter": str(after),
                                 "bonusPoints": BOSS_UPDATE_BONUS_POINTS, "bonusSigils": BOSS_UPDATE_BONUS_SIGILS})
                event["bossBounty"] = {"id": eligible["id"], "name": eligible["name"],
                                       "bonusPoints": BOSS_UPDATE_BONUS_POINTS,
                                       "bonusSigils": BOSS_UPDATE_BONUS_SIGILS}
            p["events"].append(event)
            p["score"] = int(p["score"]) + xp + (BOSS_UPDATE_BONUS_POINTS if eligible else 0)
            p["bestCombo"] = max(int(p["bestCombo"]), combo)
            unlocks = []
            for code, name, description, threshold, glyph in BADGES:
                if len(p["events"]) >= threshold and code not in p["achievements"]:
                    p["achievements"].append(code)
                    unlocks.append({"id": code, "name": name, "description": description, "glyph": glyph})
            self._write()
            return {"ok": True, "event": deepcopy(event), "unlocks": unlocks}

    def observe(self, before: dict, after: dict) -> list[dict]:
        if before.get("profileId") != after.get("profileId"):
            return []
        old = {m.get("id"): m for m in before.get("items", [])}
        awards = []
        for m in after.get("items", []):
            prior = old.get(m.get("id"))
            if not prior:
                continue
            if str(prior.get("installedVersion", "")) == str(m.get("installedVersion", "")):
                continue
            result = self.record_update(after["profileId"], m, str(prior.get("installedVersion", "")), str(m.get("installedVersion", "")), "version-observed")
            if result.get("ok"):
                awards.append(result)
        return awards

    def maybe_victory(self, profile_id: str, snapshot: dict, scan: dict, scanned_after_update: bool) -> dict | None:
        """Only complete fresh, authoritative *full* checks can award a ranked clear."""
        if scan.get("state") != "completed" or scan.get("mode") != "deep" or not scanned_after_update:
            return None
        # The FINAL boss can only be defeated with *fresh* Nexus evidence,
        # never from metadata cached before an upgrade. Mods may share a
        # project ID, so compare against unique project count, not mod count.
        if int(scan.get("queried", 0)) != int(scan.get("uniqueNexusMods", -1)) or scan.get("cachedOrDeferred", 0):
            return None
        mods = [m for m in snapshot.get("items", []) if int(m.get("nexusId") or 0) > 0]
        if not mods:
            return None
        if any(m.get("update", {}).get("verdict") not in ("current", "newer-installed") for m in mods):
            return None
        if scan.get("errors"):
            return None
        with self.lock:
            p = self._profile(profile_id)
            n = len(p["events"])
            if not n or n <= p.get("lastCrownedSequence", 0):
                return None
            bonus = 2000 + 25 * len(mods)
            victory = {"id": f"{profile_id}:{n}", "at": utcnow(), "verifiedMods": len(mods), "updates": n, "bonus": bonus, "ack": False}
            p["score"] += bonus
            p["lastCrownedSequence"] = n
            p["victories"].append(victory)
            if "world-eater" not in p["achievements"]:
                p["achievements"].append("world-eater")
            self._write()
            return deepcopy(victory)

    def ack_victory(self, profile_id: str, victory_id: str) -> dict:
        with self.lock:
            for v in self._profile(profile_id)["victories"]:
                if v["id"] == victory_id:
                    v["ack"] = True
                    self._write()
                    return {"ok": True}
            return {"ok": False, "error": "Unknown victory or wrong profile"}

    def realm_wallet(self, profile_id: str) -> dict:
        """Live economy is derived from irrevocable native update evidence, not browser state."""
        with self.lock:
            p = self._profile(profile_id)
            receipts = p.setdefault("realmPurchases", [])
            bonus = sum(int(e.get("bossBounty", {}).get("bonusSigils", 0)) for e in p["events"])
            earned = len(p["events"]) * REALM_COINS_PER_UPDATE + bonus
            spent = sum(int(x.get("cost", 0)) for x in receipts)
            return {"ok": True, "profileId": profile_id, "earned": earned,
                    "spent": spent, "available": max(0, earned-spent),
                    "verifiedUpdates": len(p["events"]), "rate": REALM_COINS_PER_UPDATE, "bossBonusSigils": bonus,
                    "catalog": deepcopy(REALM_ITEMS),
                    "purchases": deepcopy(receipts)}

    def purchase_realm(self, profile_id: str, sku: str, txn: str) -> dict:
        """Atomic & idempotent. A repeated network request cannot spend twice."""
        import re
        if sku not in REALM_ITEMS or not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", str(txn)):
            return {"ok": False, "error": "Unknown item or transaction ID"}
        with self.lock:
            p = self._profile(profile_id)
            receipts = p.setdefault("realmPurchases", [])
            duplicate = next((x for x in receipts if x["txn"] == txn), None)
            if duplicate:
                if duplicate["sku"] != sku:
                    return {"ok": False, "error": "Transaction ID already used for another item"}
                return {"ok": True, "receipt": deepcopy(duplicate), "idempotent": True,
                        "wallet": self.realm_wallet(profile_id)}
            item = REALM_ITEMS[sku]
            if not item["repeat"] and any(x["sku"] == sku for x in receipts):
                return {"ok": False, "error": "Permanent upgrade already owned"}
            minted = len(p["events"]) * REALM_COINS_PER_UPDATE + sum(
                int(e.get("bossBounty", {}).get("bonusSigils", 0)) for e in p["events"])
            available = minted - sum(int(x["cost"]) for x in receipts)
            if available < item["cost"]:
                return {"ok": False, "error": "Not enough verified Forge Sigils", "wallet": self.realm_wallet(profile_id)}
            receipt = {"txn": txn, "sku": sku, "cost": item["cost"], "at": utcnow(),
                       "updateEvidenceCount": len(p["events"]), "profileId": profile_id}
            receipts.append(receipt)
            self._write()
            return {"ok": True, "receipt": deepcopy(receipt), "wallet": self.realm_wallet(profile_id)}

    def claim_mod_boss(self, profile_id: str, mod: dict, run_id: str) -> dict:
        """Record game victory over a candidate. This mints NO currency or ranked XP.

        Candidate identity/installed version is read by the service from its native
        MO2 snapshot, never from a browser-supplied claim. Native version evidence
        must later confirm the actual upgrade before any bounty is awarded.
        """
        import re
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", str(run_id or "")):
            return {"ok": False, "error": "Invalid run identifier"}
        if not str(mod.get("id", "")).startswith("live-") or not int(mod.get("nexusId") or 0):
            return {"ok": False, "error": "Only current, identified MO2 mods can be bounty bosses"}
        if mod.get("update", {}).get("verdict") != "candidate":
            return {"ok": False, "error": "The mod is not currently a verified update candidate"}
        from .scanner import version_verdict
        before = str(mod.get("installedVersion", ""))
        latest = str(mod.get("update", {}).get("latestVersion", ""))
        if version_verdict(before, latest) != "candidate":
            return {"ok": False, "error": "No comparable newer version is available"}
        with self.lock:
            p = self._profile(profile_id)
            all_bounties = p.setdefault("bossBounties", [])
            same = next((x for x in all_bounties if x["modId"] == mod["id"] and x["before"] == before), None)
            if same:
                return {"ok": True, "bounty": deepcopy(same), "idempotent": True, "awarded": False}
            mid = str(mod["id"])
            bounty = {"id": f"hunt-{len(all_bounties)+1}-{len(p['events'])}",
                      "modId": mid, "nexusId": int(mod["nexusId"]), "name": str(mod.get("name") or mid)[:200],
                      "before": before, "latestAtDefeat": latest, "runId": str(run_id), "status": "defeated",
                      "defeatedAt": utcnow(), "killPoints": BOSS_KILL_POINTS}
            all_bounties.append(bounty)
            self._write()
            return {"ok": True, "bounty": deepcopy(bounty), "idempotent": False, "awarded": False}

    def boss_bounties(self, profile_id: str) -> dict:
        with self.lock:
            p = self._profile(profile_id)
            b = p.get("bossBounties", [])
            return {"ok": True, "profileId": profile_id, "bounties": deepcopy(b),
                    "defeated": sum(x.get("status") == "defeated" for x in b),
                    "forged": sum(x.get("status") == "forged" for x in b),
                    "bossKillPoints": BOSS_KILL_POINTS, "verifiedBonusPoints": BOSS_UPDATE_BONUS_POINTS,
                    "verifiedBonusSigils": BOSS_UPDATE_BONUS_SIGILS}

    def siege_state(self, profile_id: str) -> dict:
        with self.lock:
            p = self._profile(profile_id)
            return {"ok": True, "profileId": profile_id, "revision": int(p.get("siegeRevision", 0)),
                    "state": deepcopy(p.get("siegeState") or {})}

    def save_siege_state(self, profile_id: str, state: dict, revision: int) -> dict:
        """Game-only save. Not a trusted source of MO2 currency or bounty score."""
        if not isinstance(state, dict) or state.get("v") != 1:
            return {"ok": False, "error": "Invalid siege state"}
        permitted = {"v", "mode", "run", "records", "options", "atlas"}
        if set(state) - permitted:
            return {"ok": False, "error": "Unexpected siege fields"}
        try:
            length = len(json.dumps(state, ensure_ascii=False, separators=(",", ":")))
        except (TypeError, ValueError):
            return {"ok": False, "error": "Siege state cannot be encoded"}
        if length > 750000:
            return {"ok": False, "error": "Siege state is too large"}
        with self.lock:
            p = self._profile(profile_id)
            current = int(p.get("siegeRevision", 0))
            if revision != current:
                return {"ok": False, "error": "A newer Siege save exists; reload", "conflict": True, "revision": current}
            p["siegeState"] = deepcopy(state)
            p["siegeRevision"] = current + 1
            self._write()
            return {"ok": True, "profileId": profile_id, "revision": current + 1}

    def realm_world(self, profile_id: str) -> dict:
        with self.lock:
            p = self._profile(profile_id)
            return {"ok": True, "profileId": profile_id,
                    "revision": int(p.get("realmWorldRevision", 0)),
                    "world": deepcopy(p.get("realmWorld") or {})}

    def save_realm_world(self, profile_id: str, world: dict, revision: int) -> dict:
        """World snapshot is cosmetic/gameplay-only: it cannot grant currency or XP."""
        if not isinstance(world, dict):
            return {"ok": False, "error": "Invalid world snapshot"}
        if len(json.dumps(world, separators=(',', ':'), ensure_ascii=False)) > 750000:
            return {"ok": False, "error": "World snapshot too large"}
        allowed = {"v", "player", "stats", "inventory", "known", "worldTime", "quests", "camp",
                   "records", "mode", "rift", "seed", "companions", "harvested", "removed", "cooldowns", "story", "perks"}
        if set(world) - allowed:
            return {"ok": False, "error": "Unexpected fields in world snapshot"}
        with self.lock:
            p = self._profile(profile_id)
            current = int(p.get("realmWorldRevision", 0))
            if int(revision) != current:
                return {"ok": False, "error": "A newer Realm save exists; reload before overwriting", "conflict": True, "revision": current}
            # Economy is explicitly omitted; client inventories contain only gathered resources.
            p["realmWorld"] = deepcopy(world)
            p["realmWorldRevision"] = current + 1
            self._write()
            return {"ok": True, "profileId": profile_id, "revision": current+1}

    def leaderboard(self) -> list[dict]:
        with self.lock:
            result = []
            for profile, p in self.data["profiles"].items():
                result.append({"profileId": profile, "name": str(p.get("profileName") or profile)[:100], "score": int(p.get("score", 0)), "updates": len(p.get("events", [])), "victories": len(p.get("victories", []))})
            return sorted(result, key=lambda x: (-x["score"], x["name"]))

    def set_profile_name(self, profile_id: str, name: str) -> None:
        with self.lock:
            p = self._profile(profile_id)
            if p.get("profileName") != name:
                p["profileName"] = str(name)[:100]
                self._write()

    def overview(self, profile_id: str, *, include_progress: bool = True) -> dict:
        with self.lock:
            p = self._profile(profile_id)
            score = p.get("score", 0)
            trophies = []
            for code, name, description, threshold, glyph in BADGES:
                trophies.append({"id": code, "name": name, "description": description, "glyph": glyph, "unlocked": code in p["achievements"], "target": threshold})
            trophies.append({"id": "world-eater", "name": "World-Eater Defeated", "description": "Verify an entire live profile after updates with a fresh full Nexus audit", "glyph": "👑", "unlocked": "world-eater" in p["achievements"], "target": None})
            result = {"ok": True, "profileId": profile_id, "score": score, "level": 1 + int((score / 400) ** 0.65), "bestCombo": p["bestCombo"], "updates": len(p["events"]), "achievements": trophies, "feed": deepcopy(p["events"][-8:]), "victories": len(p["victories"]), "leaderboard": self.leaderboard(), "unseenVictory": deepcopy(next((v for v in reversed(p["victories"]) if not v["ack"]), None)), "realmWallet": self.realm_wallet(profile_id), "bossBounties": self.boss_bounties(profile_id)}
            if include_progress:
                result["progress"] = deepcopy(p.get("progress") or {})
            return result
