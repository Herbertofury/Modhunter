"""Background orchestration for local Rust facts and public provider evidence.

The service's existing reentrant guard owns all state. Native capture crosses
its Qt queue; disk scans and HTTP requests never run on MO2's UI thread. Epochs
discard work from a previous profile, including an A -> B -> A switch.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import json
import os
from pathlib import Path
import threading
import time

from .mod_health import build_mod_health, nexus_identity, NexusPageClient, PAGE_TTL_SECONDS
from .plugin_intelligence import RustAuditRunner
from .source_registry import provider_of


class ModIntelligence:
    def __init__(self, service, path: Path, *, runner=None, page_client=None):
        self.service, self.path = service, path
        self.runner = runner or RustAuditRunner()
        self.page_client = page_client or NexusPageClient()
        self._epoch = 0
        self._pending = None
        self._worker_active = False
        self._active_cancel = None
        self._result = None
        self._state = {"state": "idle", "profileId": service.profile_id()}
        self._deep_profiles = set()
        self._health_active = set()
        self._health_jobs = OrderedDict()
        self._evidence = OrderedDict()
        self._previous_health = OrderedDict()
        self._summary_cache = {}
        self._identity_cache = {}
        self._configured = []
        self._write_lock = threading.Lock()
        try:
            if path.stat().st_size <= 2_000_000:
                saved = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(saved, dict) and saved.get("schema") == 1:
                    entries = saved.get("pageEvidence", {})
                    if isinstance(entries, dict):
                        self._evidence.update((key, value) for key, value in list(entries.items())[-400:]
                                              if isinstance(key, str) and isinstance(value, dict))
                    self._configured = [p for p in saved.get("eslifierDataPaths", [])
                                        if isinstance(p, str) and p][:4]
        except (OSError, ValueError, TypeError):
            pass

    def _persist(self):
        with self._write_lock:
            with self.service._guard:
                saved = {"schema": 1, "pageEvidence": dict(self._evidence),
                         "eslifierDataPaths": list(self._configured)}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(saved, separators=(",", ":")), encoding="utf-8")
            os.replace(temporary, self.path)

    def configured_paths(self):
        with self.service._guard:
            return list(self._configured)

    def configure_path(self, path: str, profile_id: str) -> dict:
        # Invoked only after a native folder dialog, never from an HTTP path field.
        folder = Path(path)
        if not folder.is_dir() or not any((folder / name).is_file() for name in
                                          ("settings.json", "original_files.json", "new_file_hashes.json")):
            return {"ok": False, "error": "Choose ESLifier_Data or ESLifier_FO4_Data containing ESLifier's JSON records."}
        with self.service._guard:
            if self.service.profile_id() != profile_id:
                return {"ok": False, "error": "MO2 profile changed while choosing the ESLifier folder"}
            previous = self._configured
            self._configured = [str(folder.resolve())]
        try:
            self._persist()
        except OSError as exc:
            with self.service._guard:
                self._configured = previous
            return {"ok": False, "error": "Could not save ESLifier folder: " + str(exc)}
        self.invalidate(reason="ESLifier records changed; compare fresh source and output hashes")
        return self.start_audit(profile_id, deep=True)

    def state(self) -> dict:
        with self.service._guard:
            result = deepcopy(self._state)
            result.update(ok=True, available=bool(self.runner.available),
                          eslifierConfigured=bool(self._configured) or bool(
                              self.service._snapshot.get("pluginAnalysis", {}).get("eslifierConfigured")))
            if self._result:
                result["analysis"] = {key: deepcopy(value) for key, value in self._result.items()
                                      if key not in ("mods", "plugins")}
            return result

    def invalidate(self, *, reason="MO2 plugin or mod state changed") -> None:
        with self.service._guard:
            self._epoch += 1
            if self._active_cancel is not None:
                self._active_cancel.set()
            self._pending = None
            self._result = None
            self._summary_cache.clear()
            self._identity_cache.clear()
            self._state = {"state": "stale", "profileId": self.service.profile_id(), "reason": reason}

    def auto_audit(self):
        with self.service._guard:
            if self.service._stopped or not self.runner.available:
                return
            profile = self.service.profile_id()
            deep = profile in self._deep_profiles or bool(self._configured) or bool(
                self.service._snapshot.get("pluginAnalysis", {}).get("eslifierConfigured"))
        self.start_audit(profile, deep=deep)

    def start_audit(self, profile_id: str, *, deep=True) -> dict:
        with self.service._guard:
            if self.service._stopped or profile_id != self.service.profile_id():
                return {"ok": False, "error": "MO2 profile changed; refresh before analyzing plugins"}
            if not self.runner.available:
                self._state = {"state": "unavailable", "profileId": profile_id,
                    "error": "Rust plugin analyzer is missing. Install the matching Modhunter MO2 package to analyze plugin records and ESLifier hashes."}
                return {**self.state(), "ok": False}
            if (self._worker_active and self._state.get("epoch") == self._epoch
                    and self._state.get("state") in ("queued", "running")):
                if deep and self._state.get("depth") != "records":
                    self._pending = (self._epoch, profile_id, True)
                return self.state()
            self._pending = (self._epoch, profile_id, bool(deep))
            self._state = {"state": "queued", "profileId": profile_id, "epoch": self._epoch,
                           "depth": "records" if deep else "header"}
            if not self._worker_active:
                self._worker_active = True
                threading.Thread(target=self._audit_loop, daemon=True, name="ModhunterRustAudit").start()
            return self.state()

    def _audit_loop(self):
        while True:
            with self.service._guard:
                if self.service._stopped or self._pending is None:
                    self._worker_active = False
                    self._active_cancel = None
                    return
                epoch, profile, deep = self._pending
                self._pending = None
                self._state = {"state": "running", "profileId": profile, "epoch": epoch,
                               "depth": "records" if deep else "header", "startedAt": time.time()}
                configured = list(self._configured)
                cancel_event = self._active_cancel = threading.Event()
            try:
                captured = self.service.call_ui("plugin-audit-input", {"profileId": profile,
                    "deep": deep, "eslifierDataPaths": configured}, timeout=25)
                if not captured.get("ok") or not isinstance(captured.get("request"), dict):
                    raise RuntimeError(str(captured.get("error") or "MO2 plugin inventory could not be captured"))
                with self.service._guard:
                    if epoch != self._epoch or profile != self.service.profile_id() or self.service._stopped:
                        continue
                if callable(getattr(self.runner, "run_cancellable", None)):
                    result = self.runner.run_cancellable(captured["request"], cancel_event)
                else:
                    result = self.runner.run(captured["request"])
                with self.service._guard:
                    if epoch != self._epoch or profile != self.service.profile_id() or self.service._stopped:
                        continue
                    self._result = result
                    if deep:
                        self._deep_profiles.add(profile)
                    self._state = {"state": "completed", "profileId": profile, "epoch": epoch,
                                   "depth": result.get("depth"), "checkedAt": result.get("checkedAt")}
                    self.service._generation += 1
            except Exception as exc:
                with self.service._guard:
                    if epoch == self._epoch and profile == self.service.profile_id() and not self.service._stopped:
                        self._state = {"state": "failed", "profileId": profile, "epoch": epoch,
                                       "error": str(exc)[:1200]}
                        self.service._generation += 1

    @staticmethod
    def _compact_health(health):
        # The projection has only scalars and one-level scalar dictionaries.
        # Copy each dictionary once instead of recursive-copying every scalar.
        owned = lambda value: dict(value) if isinstance(value, dict) else value
        result = {key: owned(health[key]) for key in ("schema", "provider", "checkedAt", "freshness", "job") if key in health}
        result["page"] = {key: owned(value) for key, value in health.get("page", {}).items()
                          if key in ("status", "updatedAt", "checkedAt", "changedSincePrevious", "freshness")}
        result["bugs"] = {key: owned(value) for key, value in health.get("bugs", {}).items()
                          if key in ("state", "displayedCount", "checkedAt", "changedSincePrevious", "freshness", "reasonCode")}
        result["detailsAvailable"] = True
        return result

    @staticmethod
    def _compact_analysis(analysis):
        result = {key: analysis[key] for key in ("status", "depth", "checkedAt", "inventoryComplete", "needsRecheck") if key in analysis}
        result["detailsAvailable"] = True
        result["plugins"] = []
        for plugin in analysis.get("plugins", []):
            row = {key: plugin[key] for key in ("name", "winner", "winnerKnown", "winnerModId", "winnerLight",
                   "lightOverride", "active", "loadOrder", "masterIssues") if key in plugin}
            if "masterIssues" in row:
                row["masterIssues"] = deepcopy(row["masterIssues"])
            row["header"] = {"lightEffective": (plugin.get("header") or {}).get("lightEffective")}
            row["eligibility"] = {"status": (plugin.get("eligibility") or {}).get("status", "unknown")}
            row["provenance"] = {"state": (plugin.get("provenance") or {}).get("state", "unknown")}
            row["recordScanComplete"] = (plugin.get("records") or {}).get("scanComplete")
            row["errors"] = deepcopy((plugin.get("errors") or [])[:1])
            result["plugins"].append(row)
        return result

    def decorate(self, snapshot: dict, *, detailed=False) -> dict:
        """Decorate an already owned copy. Selection calls stay O(selected rows)."""
        with self.service._guard:
            current = snapshot.get("profileId") == self.service.profile_id()
            analysis = self._result if current else None
            if analysis:
                snapshot["pluginAnalysis"] = {key: deepcopy(value) for key, value in analysis.items()
                                               if key not in ("mods", "plugins")}
            snapshot["pluginAuditState"] = self.state() if current else {"state": "stale"}
            game = str(snapshot.get("game") or "")
            now = time.time()
            for item in snapshot.get("items", []):
                if analysis and str(item.get("id")) in analysis["mods"]:
                    raw = analysis["mods"][str(item["id"])]
                    # Compact list rows never expose the raw plugin report.
                    # Keep its nested data read-only under the guard and copy
                    # only the compact projection below; detailed inspectors
                    # still receive a completely independent deep copy.
                    item["localAnalysis"] = deepcopy(raw) if detailed else dict(raw)
                    item["localAnalysis"].update(depth=analysis.get("depth"), checkedAt=analysis.get("checkedAt"),
                                                 inventoryComplete=analysis.get("inventoryComplete"))
                local = item.get("localAnalysis")
                if isinstance(local, dict) and current:
                    local["needsRecheck"] = self._state.get("state") in ("stale", "failed", "queued", "running")
                source = item.get("source") or {}
                identity_key = (game, item.get("nexusId"), source.get("url"), source.get("verified"), source.get("kind"))
                cached_identity = self._identity_cache.get(str(item.get("id")))
                if cached_identity and cached_identity[0] == identity_key:
                    identity, provider = cached_identity[1:]
                else:
                    identity = nexus_identity(item, game)
                    provider = "nexus" if identity else provider_of(source.get("url", ""))
                    self._identity_cache[str(item.get("id"))] = (identity_key, identity, provider)
                key = f"{identity[0]}/{identity[1]}" if identity else ""
                cache, cache_revision = self.service.cache.get_with_revision(*identity) if identity else (None, 0)
                evidence = self._evidence.get(key)
                update = item.get("update") or {}
                summary_key = (snapshot.get("profileId"), identity_key, self._epoch,
                    update.get("source"), update.get("verdict"), update.get("checkedAt"), update.get("latestVersion"),
                    item.get("installedVersion"), (cache or {}).get("checkedAt"), cache_revision,
                    (evidence or {}).get("checkedAt"), (analysis or {}).get("checkedAt"),
                    self._state.get("state"), int(now // 15))
                saved_summary = self._summary_cache.get(str(item.get("id"))) if not detailed else None
                if saved_summary and saved_summary[0] == summary_key:
                    item["modHealth"] = self._compact_health(saved_summary[1])
                    if key in self._health_jobs:
                        item["modHealth"]["job"] = deepcopy(self._health_jobs[key])
                    if local:
                        item["localAnalysis"] = self._compact_analysis(local)
                    continue
                if not detailed and not cache and not evidence:
                    # Most profiles begin with thousands of unobserved pages.
                    # Do not construct URLs, prose or action lists for every row;
                    # the inspector obtains those exact-row details on demand.
                    health = {"schema": 1, "provider": provider, "checkedAt": None, "freshness": {"state": "unknown"},
                              "page": {"status": "unknown"}, "bugs": {"state": "unknown" if provider in ("nexus", "unknown") else "unsupported", "displayedCount": None}}
                    item["modHealth"] = self._compact_health(health)
                    if key in self._health_jobs:
                        item["modHealth"]["job"] = deepcopy(self._health_jobs[key])
                    self._summary_cache[str(item.get("id"))] = (summary_key, self._compact_health(item["modHealth"]))
                    if local:
                        item["localAnalysis"] = self._compact_analysis(local)
                    continue
                health = build_mod_health(item, game=game, now=now,
                    metadata=(cache or {}).get("meta"), metadata_checked_at=(cache or {}).get("checkedAt"),
                    page_evidence=evidence, local_analysis=local,
                    previous=self._previous_health.get(key))
                if key:
                    previous = self._previous_health.get(key)
                    if previous and previous.get("checkedAt") == health.get("checkedAt"):
                        # A change belongs to this observation, not only the
                        # first render or first installed row sharing a project.
                        for part in ("page", "bugs"):
                            prior = previous.get(part, {})
                            if prior.get("checkedAt") == health.get(part, {}).get("checkedAt"):
                                for field in ("changedSincePrevious", "previousState"):
                                    if prior.get(field) is not None:
                                        health[part][field] = prior[field]
                    if not previous or previous.get("checkedAt") != health.get("checkedAt"):
                        self._previous_health[key] = deepcopy(health)
                        while len(self._previous_health) > 1000:
                            self._previous_health.popitem(last=False)
                    job = self._health_jobs.get(key)
                    if job:
                        health["job"] = deepcopy(job)
                item["modHealth"] = health if detailed else self._compact_health(health)
                if not detailed:
                    self._summary_cache[str(item.get("id"))] = (summary_key, self._compact_health(item["modHealth"]))
                    if local:
                        item["localAnalysis"] = self._compact_analysis(local)
            return snapshot

    def health_state(self, item_id: str, profile_id: str = "") -> dict:
        with self.service._guard:
            if profile_id and profile_id != self.service.profile_id():
                return {"ok": False, "error": "MO2 profile changed; refresh before checking health"}
            snapshot = self.service.selection_snapshot([item_id])
            if not snapshot.get("items"):
                return {"ok": False, "error": "Mod is not in the active profile"}
            return {"ok": True, "profileId": snapshot["profileId"], "itemId": item_id,
                    "health": snapshot["items"][0].get("modHealth"),
                    "localAnalysis": snapshot["items"][0].get("localAnalysis"),
                    "pluginAuditState": snapshot.get("pluginAuditState")}

    def start_health(self, item_id: str, profile_id: str, *, force=False) -> dict:
        with self.service._guard:
            if self.service._stopped or profile_id != self.service.profile_id():
                return {"ok": False, "error": "MO2 profile changed; refresh before checking health"}
            snapshot = self.service.selection_snapshot([item_id])
            if not snapshot.get("items"):
                return {"ok": False, "error": "Mod is not in the active MO2 profile"}
            identity = nexus_identity(snapshot["items"][0], str(snapshot.get("game") or ""))
            if not identity:
                return {"ok": False, "error": "This mod has no unambiguous Nexus project identity; open its original issue page"}
            key = f"{identity[0]}/{identity[1]}"
            saved = self._evidence.get(key) or {}
            stamp = saved.get("checkedAt")
            expires = (saved.get("cache") or {}).get("expiresAt")
            same_identity = saved.get("identity") == {"game": identity[0], "modId": identity[1]}
            limited = (saved.get("fetch") or {}).get("state") == "rate-limited"
            lifetime = 3600 if limited else PAGE_TTL_SECONDS
            if ((not force or limited) and same_identity and isinstance(stamp, (int, float)) and not isinstance(stamp, bool)
                    and isinstance(expires, (int, float)) and not isinstance(expires, bool)
                    and 0 <= time.time() - stamp < lifetime and time.time() < expires <= stamp + lifetime + 5):
                self._health_jobs[key] = {"state": "completed", "cached": True}
                self.service._generation += 1
                return self.health_state(item_id, profile_id)
            if key in self._health_active:
                return self.health_state(item_id, profile_id)
            if len(self._health_active) >= 2:
                return {"ok": False, "error": "Two Nexus health checks are running. Retry when one finishes."}
            self._health_active.add(key)
            self._health_jobs[key] = {"state": "running", "startedAt": time.time()}
            self.service._generation += 1
            threading.Thread(target=self._health_worker, args=(identity, key, bool(force)),
                             daemon=True, name="ModhunterPageHealth").start()
            return self.health_state(item_id, profile_id)

    def _health_worker(self, identity, key, force):
        try:
            evidence = self.page_client.fetch(*identity, force=force)
            with self.service._guard:
                if self.service._stopped:
                    return
                self._evidence[key] = evidence
                self._evidence.move_to_end(key)
                while len(self._evidence) > 400:
                    self._evidence.popitem(last=False)
                self._health_jobs[key] = {"state": "completed", "checkedAt": evidence.get("checkedAt")}
                self.service._generation += 1
            self._persist()
        except Exception as exc:
            with self.service._guard:
                if not self.service._stopped:
                    self._health_jobs[key] = {"state": "failed", "error": str(exc)[:600]}
                    self.service._generation += 1
        finally:
            with self.service._guard:
                self._health_active.discard(key)
                while len(self._health_jobs) > 400:
                    self._health_jobs.popitem(last=False)
                    self.service._generation += 1
