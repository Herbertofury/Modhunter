"""Profile-owned local creator follows from exact provider and native evidence.

Following never writes to a provider account, changes a mod source, or installs
files. Native callbacks enqueue immutable facts; network work uses the existing
source scanners off Qt. Display-name credits alone are never account identity.
"""
from __future__ import annotations

import configparser
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import threading
import time
import urllib.error
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from .creator_continuity import ACCOUNT_ID, HANDLE, public_url

FOLLOW_CHECK_INTERVAL = 1800
FOLLOW_RETRY_INTERVAL = 300
FOLLOW_SCHEDULER_TICK = 60


def _text(value, maximum=300):
    return (value.strip() if isinstance(value, str) and len(value) <= maximum
            and not any(ord(c) < 32 or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF for c in value) else "")


def project_identity(value):
    url = public_url(value)
    if not url:
        return None
    parsed = urlsplit(url)
    parts = parsed.path.strip("/").split("/")
    if "//" in parsed.path or any(p in {".", ".."} for p in parts):
        return None
    numeric = lambda p: bool(re.fullmatch(r"[1-9][0-9]{0,15}", p)) and int(p) <= 9007199254740991
    provider, game, project = "", "", ""
    if (parsed.hostname in {"www.nexusmods.com", "nexusmods.com"} and len(parts) == 3
            and re.fullmatch(r"[a-z0-9_-]{1,80}", parts[0]) and parts[1] == "mods" and numeric(parts[2])):
        provider, game, project = "nexus", parts[0], parts[2]
        url = f"https://www.nexusmods.com/{game}/mods/{project}"
    elif (parsed.hostname == "modholdmods.com" and len(parts) in {4, 5} and parts[0] == "g" and parts[2] == "mods"
          and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", parts[1]) and len(parts[1]) <= 80 and numeric(parts[3])
          and (len(parts) == 4 or re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", parts[4]))):
        provider, game, project = "modhold", parts[1], parts[3]
    elif parsed.hostname == "github.com" and len(parts) >= 2 and all(re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", p) for p in parts[:2]):
        if len(parts) > 2 and parts[2] != "releases":
            return None
        provider, project = "github", "/".join(parts[:2]).removesuffix(".git").lower()
        url = "https://github.com/" + project
    elif parsed.hostname == "gitlab.com":
        namespace = parts[:parts.index("-")] if "-" in parts else parts
        if len(namespace) < 2 or not all(re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", p) for p in namespace):
            return None
        provider, project = "gitlab", "/".join(namespace).removesuffix(".git")
        url = "https://gitlab.com/" + project
    elif parsed.hostname == "modrinth.com" and len(parts) == 2 and parts[0] in {"mod", "plugin", "modpack", "resourcepack", "shader", "datapack"}:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", parts[1]):
            return None
        provider, project = "modrinth", parts[1]
    elif parsed.hostname == "thunderstore.io":
        match = re.fullmatch(r"/(?:c/([a-z0-9-]+)/p|package)/([A-Za-z0-9_]+)/([A-Za-z0-9_]+)/?", parsed.path)
        if not match:
            return None
        provider, game, project = "thunderstore", match[1] or "", match[2] + "/" + match[3]
    else:
        return None
    return {"provider": provider, "game": game, "projectId": project, "url": url,
            "key": provider + ":" + game + ":" + project}


def _nexus_profile_matches(profile, account, handle):
    parsed = urlsplit(profile)
    path = parsed.path.rstrip("/")
    return (parsed.hostname in {"www.nexusmods.com", "nexusmods.com"} and not parsed.query
            and (re.fullmatch(r"(?i:/users/)" + re.escape(account), path) is not None
                 or path.casefold() == ("/profile/" + handle).casefold()))


def _row_project(row):
    source = row.get("source")
    if not isinstance(source, dict) or source.get("verified") is not True or source.get("kind") == "advisory":
        return None
    return project_identity(source.get("url"))


def nexus_creator(metadata, game, mod_id):
    """Only the uploader's explicit API member/profile tuple binds an account."""
    if (not isinstance(metadata, dict) or type(mod_id) is not int or mod_id <= 0
            or metadata.get("mod_id") != mod_id or metadata.get("domain_name") != game):
        return None
    user = metadata.get("user")
    if not isinstance(user, dict) or type(user.get("member_id")) is not int or user["member_id"] <= 0:
        return None
    account = str(user["member_id"])
    name = _text(user.get("name"), 180)
    profile = public_url(metadata.get("uploaded_users_profile_url"))
    if not name or not ACCOUNT_ID.fullmatch(account) or int(account) > 9007199254740991 or not profile:
        return None
    if not _nexus_profile_matches(profile, account, name):
        return None
    uploader = metadata.get("uploaded_by")
    if uploader is not None and (not isinstance(uploader, str) or uploader.casefold() != name.casefold()):
        return None
    result = {"id": "nexus:user:" + account, "provider": "nexus", "accountId": account,
              "handle": name, "name": name, "profileUrl": profile, "role": "uploader",
              "identityKind": "provider-account", "verified": True,
              "evidenceKind": "provider-project-account"}
    avatar = public_url(user.get("avatar_url"))
    if avatar and re.fullmatch(r"https://avatars\.nexusmods\.com/" + account + r"/[0-9]+", avatar):
        result["avatarUrl"] = avatar
    return result


def _creator_from_update(identity, update):
    if not isinstance(update, dict):
        return None
    checked, expires, now = update.get("checkedAt"), update.get("expiresAt"), time.time()
    if (not isinstance(checked, (int, float)) or isinstance(checked, bool) or not math.isfinite(checked) or not 0 < checked <= now + 300
            or update.get("freshness") not in {"fresh", "cached", "revalidated"}
            or isinstance(expires, (int, float)) and expires <= now):
        return None
    provider = identity["provider"]
    if provider == "nexus":
        person = update.get("creator")
        if not isinstance(person, dict) or person.get("provider") != "nexus" or person.get("verified") is not True:
            return None
        account = person.get("accountId")
        if (not isinstance(account, str) or not ACCOUNT_ID.fullmatch(account) or int(account) > 9007199254740991
                or person.get("id") != "nexus:user:" + account or person.get("evidenceKind") != "provider-project-account"):
            return None
        profile = public_url(person.get("profileUrl"))
        name = _text(person.get("name"), 180)
        if not profile or not name or not _nexus_profile_matches(profile, account, name):
            return None
        result = {key: deepcopy(value) for key, value in person.items() if key in
                  {"id", "provider", "accountId", "handle", "name", "profileUrl", "identityKind", "verified", "evidenceKind"}}
        result["role"] = "uploader"
        avatar = public_url(person.get("avatarUrl"))
        if avatar and re.fullmatch(r"https://avatars\.nexusmods\.com/" + account + r"/[0-9]+", avatar):
            result["avatarUrl"] = avatar
        return result
    source = project_identity(update.get("sourceUrl"))
    if (source is None or source["key"] != identity["key"] or update.get("provider") != provider
            or update.get("outcome") not in {"release", "review"}
            or update.get("freshness") not in {"fresh", "cached", "revalidated"}):
        return None
    person = update.get("author") if provider == "modhold" else update.get("publisher")
    if not isinstance(person, dict):
        return None
    profile = public_url(person.get("url"))
    name = _text(person.get("name"), 180)
    if not profile or not name:
        return None
    parsed = urlsplit(profile)
    if parsed.query:
        return None
    patterns = {"modhold": ("modholdmods.com", r"/u/([A-Za-z0-9_.-]{1,120})/?", "mod author"),
                "github": ("github.com", r"/([A-Za-z0-9_.-]{1,120})/?", "release publisher"),
                "gitlab": ("gitlab.com", r"/([A-Za-z0-9_.-]{1,120})/?", "release publisher"),
                "thunderstore": ("thunderstore.io", r"/package/([A-Za-z0-9_]{1,120})/?", "publishing namespace")}
    rule = patterns.get(provider)
    match = re.fullmatch(rule[1], parsed.path) if rule and parsed.hostname == rule[0] else None
    if not match:
        return None
    handle = match[1]
    if provider == "modhold" and str(update.get("publisherId", "")).casefold() != handle.casefold():
        return None
    result = {"id": provider + ":profile:" + handle.casefold(), "provider": provider,
              "handle": handle, "name": name, "profileUrl": profile, "role": rule[2],
              "identityKind": "provider-profile", "verified": True, "evidenceKind": "provider-project-profile"}
    avatar = public_url(person.get("avatarUrl"))
    if avatar and urlsplit(avatar).hostname in {"avatars.githubusercontent.com", "gitlab.com", "modholdmods.com"}:
        result["avatarUrl"] = avatar
    return result


def download_project_receipt(path, downloads_root, game):
    """Read a completed MO2 download's bounded sidecar, never a filename guess.

    Only the native callback supplies paths. Secrets and archive paths are not
    returned or persisted. Missing/delayed metadata remains review-required.
    """
    archive, root = Path(path).resolve(), Path(downloads_root).resolve()
    if not archive.is_relative_to(root) or not archive.is_file() or archive.suffix.lower() not in {".zip", ".7z", ".rar"}:
        return None
    sidecar = Path(str(archive) + ".meta")
    if sidecar.is_symlink() or not sidecar.is_file() or sidecar.stat().st_size > 131072:
        return None
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        parser.read_string(sidecar.read_text(encoding="utf-8-sig"))
        meta = parser["General"]
        repository = meta.get("repository", "Nexus").casefold()
        aliases = {"skyrimse": "skyrimspecialedition", "skyrimspecialedition": "skyrimspecialedition", "fallout4": "fallout4", "skyrim": "skyrim", "starfield": "starfield"}
        raw_game = meta.get("gameName", "").lower()
        source_game = aliases.get(raw_game, raw_game)
        mod_id, file_id = meta.get("modID", ""), meta.get("fileID", "")
        if (repository not in {"nexus", "nexusmods", "nexus mods"} or source_game != game
                or not re.fullmatch(r"[1-9][0-9]{0,15}", mod_id) or not re.fullmatch(r"[1-9][0-9]{0,15}", file_id)
                or int(file_id) > 9007199254740991):
            return None
        url = f"https://www.nexusmods.com/{source_game}/mods/{mod_id}"
        if not project_identity(url):
            return None
        return {"sourceUrl": url, "game": source_game, "modId": int(mod_id), "fileId": int(file_id),
                "title": _text(meta.get("modName", ""), 300) or "Nexus mod " + mod_id,
                "evidenceKind": "native-download-complete-sidecar"}
    except (OSError, UnicodeError, configparser.Error, KeyError, ValueError):
        return None


def empty_document():
    return {"schemaVersion": 1, "revision": 0, "autoFollow": True, "creators": {}, "review": {}}


def validate_document(raw):
    if raw is None:
        return empty_document()
    if (not isinstance(raw, dict) or type(raw.get("schemaVersion")) is not int or raw["schemaVersion"] != 1
            or type(raw.get("revision")) is not int or raw["revision"] < 0 or type(raw.get("autoFollow")) is not bool
            or not isinstance(raw.get("creators"), dict) or not isinstance(raw.get("review"), dict)):
        raise ValueError("Creator follows could not be read. Existing data was retained for recovery.")
    owners = set()
    for key, creator in raw["creators"].items():
        if (not isinstance(creator, dict) or creator.get("id") != key or not isinstance(creator.get("state"), str)
                or creator["state"] not in {"following", "paused", "unfollowed"}
                or not public_url(creator.get("profileUrl")) or not isinstance(creator.get("projects"), dict)
                or type(creator.get("generation")) is not int or creator["generation"] < 1
                or not _text(creator.get("name"), 180) or not _text(creator.get("handle"), 180)):
            raise ValueError("A saved creator follow is invalid. Existing data was retained for recovery.")
        provider, handle = creator.get("provider"), creator["handle"]
        profile = urlsplit(creator["profileUrl"])
        if provider == "nexus":
            account = creator.get("accountId")
            bound = (isinstance(account, str) and ACCOUNT_ID.fullmatch(account) and int(account) <= 9007199254740991
                     and key == "nexus:user:" + account and _nexus_profile_matches(creator["profileUrl"], account, handle))
        else:
            patterns = {"modhold": ("modholdmods.com", "/u/"), "github": ("github.com", "/"),
                        "gitlab": ("gitlab.com", "/"), "thunderstore": ("thunderstore.io", "/package/")}
            rule = patterns.get(provider) if isinstance(provider, str) else None
            bound = (rule and re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", handle)
                     and key == provider + ":profile:" + handle.casefold()
                     and profile.hostname == rule[0] and profile.path.rstrip("/") == rule[1] + handle)
        if not bound or profile.query or creator.get("verified") is not True:
            raise ValueError("A saved creator identity is invalid. Existing data was retained for recovery.")
        due = creator.get("nextCheckAt", 0)
        if not isinstance(due, (int, float)) or isinstance(due, bool) or not math.isfinite(due) or due < 0:
            raise ValueError("A saved creator schedule is invalid. Existing data was retained for recovery.")
        for project_key, project in creator["projects"].items():
            identity = project_identity(project.get("url")) if isinstance(project, dict) else None
            if (not identity or identity["key"] != project_key or identity["provider"] != provider
                    or not isinstance(project.get("provenance"), dict) or project_key in owners):
                raise ValueError("A saved followed project is invalid. Existing data was retained for recovery.")
            owners.add(project_key)
    for key, review in raw["review"].items():
        if not isinstance(review, dict) or review.get("id") != key or not _text(review.get("reason"), 1200):
            raise ValueError("A saved creator review is invalid. Existing data was retained for recovery.")
    return deepcopy(raw)


class ModholdCatalog:
    """One bounded public catalog per instance window, shared by all handles.

    The public API has no documented author filter. A missing or inconsistent
    page never becomes a partial creator catalog or evidence of removal.
    """
    def __init__(self, fetch=None, *, clock=time.time, ttl=900):
        if fetch is None:
            from .source_registry import _get_json
            fetch = _get_json
        self.fetch, self.clock, self.ttl = fetch, clock, ttl
        self._lock = threading.Lock()
        self._rows, self._checked, self._retry_at, self._error = [], 0, 0, ""

    @staticmethod
    def project(raw):
        if not isinstance(raw, dict):
            raise ValueError("ModHold returned invalid catalog project metadata.")
        identity = project_identity(raw.get("page"))
        author = raw.get("author") or {}
        handle = author.get("handle") if isinstance(author, dict) else None
        if (type(raw.get("id")) is not int or not identity or identity["provider"] != "modhold"
                or str(raw["id"]) != identity["projectId"] or raw.get("game") != identity["game"]
                or not _text(raw.get("name"), 600) or not _text(raw.get("version"), 100)
                or not _text(raw.get("updated"), 80) or not isinstance(handle, str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", handle)):
            raise ValueError("ModHold catalog project identity is incomplete or inconsistent.")
        return {**identity, "name": raw["name"][:300], "version": raw["version"],
                "updated": raw["updated"], "handle": handle,
                "authorName": _text(author.get("name"), 180) or handle}

    def collect(self, cancel):
        with self._lock:
            now = self.clock()
            if cancel.is_set():
                raise ValueError("Creator catalog check was cancelled.")
            if self._checked and now - self._checked < self.ttl:
                return deepcopy(self._rows), self._checked
            if self._retry_at > now:
                raise ValueError(self._error or "The public creator catalog is waiting before a retry.")
            rows, seen, page, total, page_size = [], set(), 1, None, None
            deadline = time.monotonic() + 45
            try:
                while True:
                    if cancel.is_set():
                        raise ValueError("Creator catalog check was cancelled.")
                    if time.monotonic() >= deadline:
                        raise ValueError("The complete ModHold catalog exceeded this check's time budget; last-good projects were retained.")
                    data = self.fetch("https://modholdmods.com/api/v1/mods?page=" + str(page))
                    if (not isinstance(data, dict) or not isinstance(data.get("mods"), list)
                            or type(data.get("total")) is not int or not 0 <= data["total"] <= 50000
                            or type(data.get("page")) is not int or data["page"] != page
                            or type(data.get("per_page")) is not int or not 1 <= data["per_page"] <= 50
                            or total is not None and data["total"] != total
                            or page_size is not None and data["per_page"] != page_size):
                        raise ValueError("ModHold catalog pagination changed or exceeds the public check budget; last-good projects were retained.")
                    total, page_size = data["total"], data["per_page"]
                    expected = min(page_size, total - len(rows))
                    if len(data["mods"]) != expected:
                        raise ValueError("ModHold catalog was incomplete; last-good projects were retained.")
                    for raw in data["mods"]:
                        value = self.project(raw)
                        if value["projectId"] in seen:
                            raise ValueError("ModHold repeated a project during pagination; last-good projects were retained.")
                        seen.add(value["projectId"])
                        rows.append(value)
                    if len(rows) == total:
                        break
                    page += 1
                if cancel.is_set():
                    raise ValueError("Creator catalog check was cancelled.")
                self._rows, self._checked = rows, self.clock()
                self._retry_at, self._error = 0, ""
                return deepcopy(rows), self._checked
            except Exception as error:
                if not cancel.is_set():
                    self._error = "ModHold catalog could not be verified: " + str(error)[:300]
                    self._retry_at = self.clock() + 60
                    if isinstance(error, urllib.error.HTTPError) and error.headers:
                        retry = error.headers.get("Retry-After", "")
                        try:
                            retry_at = self.clock() + int(retry) if re.fullmatch(r"[0-9]+", retry) else parsedate_to_datetime(retry).timestamp()
                            if math.isfinite(retry_at):
                                self._retry_at = max(self._retry_at, retry_at)
                        except (ValueError, TypeError, OverflowError, AttributeError):
                            pass
                raise


class CreatorFollowService:
    def __init__(self, host, *, clock=time.time):
        self.host, self.clock = host, clock
        self._generation = 0
        self.context_generation = 0
        self._cancel = threading.Event()
        self._job = {"state": "idle", "kind": "", "checked": 0, "total": 0}
        self._pending = {}
        self._pending_installs = {}
        self._active = False
        self._inventory_running = False
        self._inventory_again = False
        self._receipt_workers = set()
        self._native_download_states = {}
        self._catalog = ModholdCatalog()
        self._monitor_pending = False
        self._scheduler_enabled = False
        self._timer = None
        self._timer_token = None

    def start(self):
        """One service-owned timer; it has no lifetime beyond the native session."""
        with self.host._guard:
            if self._scheduler_enabled or self.host._stopped:
                return
            self._scheduler_enabled = True
            self._arm_timer()

    def stop(self):
        with self.host._guard:
            self._scheduler_enabled = False
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._timer_token = None
            self.invalidate("The native MO2 session closed.")

    def _arm_timer(self):
        if not self._scheduler_enabled or self.host._stopped or self._timer is not None:
            return
        token = object()
        timer = threading.Timer(FOLLOW_SCHEDULER_TICK, self._scheduled_tick, args=(token,))
        timer.daemon = True
        self._timer = timer
        self._timer_token = token
        timer.start()

    def _scheduled_tick(self, token):
        with self.host._guard:
            if token is not self._timer_token:
                return
            self._timer = None
            self._timer_token = None
            if not self._scheduler_enabled or self.host._stopped:
                return
            try:
                self.run_due()
            finally:
                self._arm_timer()

    def run_due(self):
        """Admit one bounded due batch; normal scans and explicit work own priority."""
        with self.host._guard:
            if self.host._stopped or self._active or self.host._scan.get("state") == "running":
                return False
            try:
                document = self._document(self.host.profile_id())
                now = self.clock()
                ids = [cid for cid, creator in document["creators"].items()
                       if creator["state"] == "following" and creator.get("nextCheckAt", 0) <= now]
                if not ids:
                    return False
                self._start(self._check_rows(document, ids), "scheduled", manual=False, creator_ids=ids)
                return True
            except (ValueError, OSError) as error:
                self._job.update(state="error", error=str(error), finishedAt=self.clock())
                return False

    def _document(self, profile):
        return validate_document(self.host.chronicle.creator_follow_document(profile))

    def _save(self, profile, document):
        previous = document["revision"]
        document["revision"] += 1
        self.host.chronicle.save_creator_follow_document(profile, document, previous)

    def invalidate(self, reason="The profile or creator request changed.", *, preserve_native_receipts=False):
        with self.host._guard:
            self._generation += 1
            self._cancel.set()
            self._pending.clear()
            self._monitor_pending = False
            if not preserve_native_receipts:
                self.context_generation += 1
                self._pending_installs.clear()
                self._native_download_states.clear()
            if self._job.get("state") in {"running", "waiting"}:
                self._job.update(state="cancelled", error=reason, finishedAt=self.clock())
            self._active = False

    def source_scan_started(self):
        """Normal mod checks keep priority; creator reads cannot supersede them."""
        with self.host._guard:
            if self._active and self._job.get("state") == "running":
                self._cancel.set()
                self._job.update(state="cancelled", error="The normal source check took priority; saved follows are retained.", finishedAt=self.clock())

    def source_scan_finished(self):
        with self.host._guard:
            try:
                if self._pending and not self._active and not self.host._stopped:
                    queued = list(self._pending.values()); self._pending.clear()
                    self._start(queued, "automatic", manual=False)
                elif self._monitor_pending and not self._active and not self.host._stopped:
                    self._monitor_pending = False
                    document = self._document(self.host.profile_id())
                    self._start(self._check_rows(document, uninstalled_only=True), "monitor", manual=False)
            except (ValueError, OSError) as error:
                self._job.update(state="error", error=str(error), finishedAt=self.clock())

    def snapshot(self):
        with self.host._guard:
            profile = self.host.profile_id()
            try:
                document = self._document(profile)
            except ValueError as error:
                return {"ok": False, "profileId": profile, "error": str(error)}
            creators = []
            for value in document["creators"].values():
                creator = deepcopy(value)
                creator["projects"] = list(creator["projects"].values())
                creator["unreadCount"] = sum(p.get("unread") is True for p in creator["projects"]) + int(bool(creator.get("noticeUnread")))
                creators.append(creator)
            return {"ok": True, "profileId": profile, "executionEpoch": self.host._execution_epoch,
                    "revision": document["revision"], "autoFollow": document["autoFollow"],
                    "creators": creators, "review": list(document["review"].values()), "job": deepcopy(self._job),
                    "summary": {"following": sum(c["state"] == "following" for c in creators),
                                "paused": sum(c["state"] == "paused" for c in creators),
                                "unfollowed": sum(c["state"] == "unfollowed" for c in creators),
                                "unread": sum(c["unreadCount"] for c in creators if c["state"] == "following"),
                                "review": len(document["review"])},
                    "monitoring": {"enabled": self._scheduler_enabled, "checkIntervalSeconds": FOLLOW_CHECK_INTERVAL,
                                   "schedulerTickSeconds": FOLLOW_SCHEDULER_TICK, "closedAppService": False,
                                   "providerCacheRespected": True},
                    "integration": {"canonicalFeed": False, "socialWrites": False, "migrationReviewRequired": True,
                                    "scope": "Local provider-verified creator monitoring; canonical transition ingestion and retraction reconciliation remain separate."}}

    def command(self, payload):
        if not isinstance(payload, dict):
            return {"ok": False, "error": "Invalid creator request."}
        with self.host._guard:
            profile = self.host.profile_id()
            if (self.host._stopped or payload.get("profileId") != profile or type(payload.get("executionEpoch")) is not int
                    or payload["executionEpoch"] != self.host._execution_epoch):
                return {"ok": False, "error": "The profile changed. Refresh Creator Center before changing follows."}
            try:
                document = self._document(profile)
                if "revision" in payload and (type(payload["revision"]) is not int or payload["revision"] != document["revision"]):
                    raise ValueError("Creator follows changed. Refresh before applying this action.")
                action = payload.get("action")
                if not isinstance(action, str) or "itemIds" in payload and action != "discover":
                    raise ValueError("Invalid creator action or project selection.")
                if action in {"discover", "check"}:
                    rows = self._rows_for_command(payload, document)
                    return self._start(rows, action, manual=True, creator_ids=payload.get("creatorIds"))
                if action == "settings":
                    if type(payload.get("autoFollow")) is not bool:
                        raise ValueError("Automatic follow preference must be true or false.")
                    document["autoFollow"] = payload["autoFollow"]
                    self.invalidate("Automatic creator-follow settings changed.")
                elif action in {"pause", "resume", "unfollow", "restore", "mark-seen"}:
                    ids = payload.get("creatorIds")
                    if (not isinstance(ids, list) or not ids or len(ids) > 25000
                            or any(not isinstance(cid, str) or cid not in document["creators"] for cid in ids)):
                        raise ValueError("Choose existing creators for this action.")
                    for cid in set(ids):
                        creator = document["creators"][cid]
                        if action == "mark-seen":
                            creator["noticeUnread"] = False
                            for project in creator["projects"].values():
                                project["unread"] = False
                                project["seenSignature"] = project.get("releaseSignature", "")
                        else:
                            if action in {"pause", "resume"} and creator["state"] == "unfollowed":
                                raise ValueError("Restore an unfollowed creator explicitly before monitoring again.")
                            creator["state"] = {"pause": "paused", "resume": "following", "unfollow": "unfollowed", "restore": "following"}[action]
                            creator["generation"] += 1
                            creator["changedAt"] = self.clock()
                            if action in {"resume", "restore"}:
                                creator["nextCheckAt"] = self.clock()
                            if action == "unfollow":
                                creator["unfollowedAt"] = self.clock()
                elif action == "cancel":
                    self.invalidate("Creator check cancelled; last-good evidence was retained.")
                    for creator in document["creators"].values():
                        if creator["state"] == "following":
                            creator["nextCheckAt"] = self.clock() + FOLLOW_CHECK_INTERVAL
                    self._save(profile, document)
                    return self.snapshot()
                else:
                    raise ValueError("Unknown creator action.")
                self._save(profile, document)
            except (ValueError, OSError) as error:
                return {"ok": False, "profileId": profile, "error": str(error)}
            return self.snapshot()

    def _rows_for_command(self, payload, document):
        if payload["action"] == "discover":
            catalog = self.host.creator_inventory()
            ids = payload.get("itemIds")
            by_id = {str(row.get("id")): row for row in catalog.get("items", [])}
            if ids is not None and (not isinstance(ids, list) or len(ids) > 25000 or not ids
                                    or any(not isinstance(item, str) or item not in by_id for item in ids)):
                raise ValueError("Discovery must name mods in the current profile.")
            return [{**deepcopy(row), "followOrigin": "installed", "followEvidence": "explicit-installed-library-discovery"}
                    for row in (by_id[key] for key in dict.fromkeys(ids))] if ids is not None else [
                        {**deepcopy(row), "followOrigin": "installed", "followEvidence": "explicit-installed-library-discovery"}
                        for row in by_id.values()]
        ids = payload.get("creatorIds")
        if ids is not None and (not isinstance(ids, list) or not ids or len(ids) > 25000
                                or any(not isinstance(cid, str) or cid not in document["creators"] for cid in ids)):
            raise ValueError("Choose existing creators to check.")
        return self._check_rows(document, ids)

    def _check_rows(self, document, ids=None, *, uninstalled_only=False):
        rows = {}
        selected = set(ids) if ids is not None else None
        for cid, creator in document["creators"].items():
            if creator["state"] != "following" or selected is not None and cid not in selected:
                continue
            for key, project in creator["projects"].items():
                if uninstalled_only and project.get("itemId") in self.host._items_by_id:
                    continue
                row = self._project_row(project)
                row["expectedCreatorId"] = cid
                rows[key] = row
        return list(rows.values())

    @staticmethod
    def _project_row(project):
        identity = project_identity(project["url"])
        # A migrated mod can retain links to two providers under one installed
        # row. Scanner IDs must identify the exact project, not that shared row.
        return {"id": "creator-project-" + hashlib.sha256(identity["key"].encode()).hexdigest()[:24],
                "followItemId": project.get("itemId") or "",
                "name": project.get("title", identity["projectId"]), "installedVersion": project.get("installedVersion", ""),
                "nexusId": int(identity["projectId"]) if identity["provider"] == "nexus" else 0,
                "source": {"url": project["url"], "type": identity["provider"], "kind": "project", "verified": True},
                "followOrigin": project.get("origin", "download"), "followEvidence": "followed-exact-project"}

    def _apply_rows(self, document, rows, records, *, discover, manual=False):
        now = self.clock()
        owners = {key: cid for cid, c in document["creators"].items() for key in c["projects"]}
        for row in rows:
            if "followDownloadId" in row and (
                    row.get("followDownloadContext") != self.context_generation
                    or self._native_download_states.get(row["followDownloadId"]) != "complete"):
                continue  # A removed/failed receipt cannot admit a late metadata result.
            item_id = str(row.get("id") or "")
            identity = _row_project(row)
            review_key = identity["key"] if identity else "item:" + item_id
            update = records.get(item_id) or {}
            creator = _creator_from_update(identity, update) if identity else None
            expected = row.get("expectedCreatorId") or owners.get(review_key)
            if expected in document["creators"] and document["creators"][expected]["state"] != "following":
                continue
            reason = "The provider has not bound a creator account to this exact project; review its source metadata."
            if not identity:
                reason = "Link a supported exact project before discovering its creator."
            elif creator and expected and creator["id"] != expected:
                reason = "This project now identifies a different creator. The previous follow and migration source are retained for review."
                old = document["creators"].get(expected)
                if old:
                    old["migrationHold"] = {"reason": reason, "observedAt": now, "sourceUrl": identity["url"]}
                creator = None
            elif not creator and update.get("reason"):
                detail = _text(update["reason"], 900)
                if detail and (update.get("verdict") in {"unknown", "unverified"} or update.get("outcome") in {"unavailable", "manual", "unsupported"}):
                    reason += " Last source check: " + detail
            if not creator:
                if discover or expected:
                    review = {"id": review_key, "itemId": row.get("followItemId", item_id if not item_id.startswith("creator-project-") else ""),
                        "title": _text(row.get("name")) or "Unresolved project",
                        "provider": identity["provider"] if identity else "unknown",
                        "sourceUrl": identity["url"] if identity else public_url((row.get("source") or {}).get("url")),
                        "reason": reason, "observedAt": now, "origin": row.get("followOrigin", "installed")}
                    previous_review = document["review"].get(review_key, {})
                    if all(previous_review.get(key) == value for key, value in review.items() if key != "observedAt"):
                        review["observedAt"] = previous_review["observedAt"]
                    document["review"][review_key] = review
                    if expected in document["creators"]:
                        old_project = document["creators"][expected]["projects"].get(review_key)
                        if old_project:
                            old_project["error"] = reason
                            old_project["lastAttemptAt"] = update.get("checkedAt") or old_project.get("lastAttemptAt") or now
                continue
            cid = creator["id"]
            previous = document["creators"].get(cid)
            if previous is None:
                if not discover or not (manual or document["autoFollow"]):
                    continue
                previous = document["creators"][cid] = {**creator, "state": "following", "generation": 1,
                    "origin": row.get("followOrigin", "installed"), "followedAt": now, "projects": {}, "lastCheckedAt": None,
                    "nextCheckAt": now + FOLLOW_CHECK_INTERVAL}
            if previous["state"] != "following":
                continue  # Durable pause/unfollow is never undone by a receipt or discovery.
            previous.update(creator)
            previous["coverage"] = ("Linked projects and pinned Nexus account status; new author projects are not enumerated."
                                    if creator["provider"] == "nexus" else "Linked projects and the complete public ModHold creator catalog."
                                    if creator["provider"] == "modhold" else "Linked projects only; new author projects are not enumerated.")
            project = previous["projects"].setdefault(identity["key"], {"id": identity["key"], "provider": identity["provider"],
                "game": identity["game"], "projectId": identity["projectId"], "url": identity["url"], "provenance": {}, "updates": []})
            project.update(itemId=row.get("followItemId", item_id if not item_id.startswith("creator-project-") else ""), title=_text(row.get("name")) or identity["projectId"],
                           installedVersion=_text(row.get("installedVersion"), 100), origin=row.get("followOrigin", "installed"))
            event_key = _text(row.get("followReceipt"), 240) or row.get("followEvidence", "provider-metadata") + ":" + item_id
            project["provenance"].setdefault(event_key, {"origin": project["origin"], "evidenceKind": row.get("followEvidence", "provider-project-metadata"), "observedAt": now})
            latest = _text(update.get("latestVersion"), 100)
            checked = update.get("checkedAt")
            fresh = (isinstance(checked, (int, float)) and not isinstance(checked, bool) and 0 < checked <= now + 300
                     and update.get("freshness") in {"fresh", "cached", "revalidated"}
                     and update.get("outcome", "release") not in {"unavailable", "manual", "unsupported"})
            if fresh and latest:
                signature = hashlib.sha256(json.dumps([identity["key"], latest, str(update.get("releaseId") or "")]).encode()).hexdigest()
                old_signature = project.get("releaseSignature")
                if old_signature and project.get("latestVersion") == latest and not project.get("releaseId") and update.get("releaseId"):
                    # Learning the exact main-file ID of a catalog version is
                    # stronger evidence for the same release, not a new release.
                    if not project.get("unread"):
                        project["seenSignature"] = signature
                    old_signature = signature
                if old_signature and old_signature != signature:
                    project["updates"].append({"signature": signature, "version": latest, "releaseId": str(update.get("releaseId") or ""), "observedAt": checked})
                    previous["lastUpdateAt"] = checked
                elif not old_signature and row.get("followOrigin") == "creator-catalog":
                    previous["lastUpdateAt"] = checked
                project.setdefault("seenSignature", "" if row.get("followOrigin") == "creator-catalog" else signature)
                project.update(latestVersion=latest, releaseId=str(update.get("releaseId") or ""), releaseSignature=signature,
                               updateState=str(update.get("verdict") or "review"), lastCheckedAt=checked,
                               unread=signature != project["seenSignature"], error="")
                previous["lastCheckedAt"] = checked
            document["review"].pop(review_key, None)
            owners[review_key] = cid

    def observe_updates(self, snapshot, records, *, origin="installed", evidence="verified-installed-library-metadata"):
        """Consume owned scanner results without another provider request."""
        with self.host._guard:
            if (self.host._stopped or snapshot.get("profileId") != self.host.profile_id()
                    or snapshot.get("executionEpoch", self.host._execution_epoch) != self.host._execution_epoch):
                return
            try:
                document = self._document(self.host.profile_id())
                before = deepcopy(document)
                rows = [{**row, "followOrigin": origin, "followEvidence": evidence} for row in snapshot.get("items", []) if str(row.get("id")) in records]
                self._apply_rows(document, rows, records, discover=document["autoFollow"])
                if document != before:
                    self._save(self.host.profile_id(), document)
                    if any(c["state"] == "following" for c in document["creators"].values()):
                        self._monitor_pending = True
            except (ValueError, OSError) as error:
                self._job.update(state="error", error=str(error), finishedAt=self.clock())

    def installed(self, name):
        with self.host._guard:
            if _text(name, 300):
                self._pending_installs[name] = (self.host.profile_id(), self.context_generation)

    def inventory(self):
        """Coalesce native refreshes; metadata IO and persistence never run on Qt."""
        with self.host._guard:
            if self.host._stopped:
                return
            # A native inventory refresh is not itself discovery evidence. Do
            # not create a worker (or initiate provider reads) when there is
            # no owned metadata, successful install, or queued monitoring work.
            if (not self._pending_installs and not self._monitor_pending
                    and not any(isinstance(row.get("update"), dict) and row["update"]
                                for row in self.host._snapshot.get("items", []))):
                return
            if self._inventory_running:
                self._inventory_again = True
                return
            self._inventory_running = True
        def run():
            try:
                while True:
                    with self.host._guard:
                        if self.host._stopped:
                            self._inventory_running = False
                            return
                        snapshot = self.host.creator_inventory()
                        records = {str(row.get("id")): row.get("update") for row in snapshot.get("items", []) if isinstance(row.get("update"), dict)}
                        self.observe_updates(snapshot, records)
                        installed = []
                        for row in snapshot.get("items", []):
                            request = self._pending_installs.pop(row.get("mo2InternalName", ""), None)
                            if request == (self.host.profile_id(), self.context_generation):
                                installed.append({**row, "followOrigin": "installed", "followEvidence": "native-mod-installed"})
                        if installed:
                            self._enqueue(installed)
                        if not self._inventory_again:
                            self._inventory_running = False
                            if self.host._scan.get("state") != "running":
                                self.source_scan_finished()
                            return
                        self._inventory_again = False
            except Exception as error:
                with self.host._guard:
                    self._inventory_running = False
                    self._job.update(state="error", error=str(error), finishedAt=self.clock())
        threading.Thread(target=run, name="ModhunterCreatorInventory", daemon=True).start()

    def downloaded(self, ticket, download_id):
        with self.host._guard:
            if (self.host._stopped or ticket.get("profileId") != self.host.profile_id()
                    or self._native_download_states.get(download_id) != "complete"
                    or ticket.get("creatorFollowContext", self.context_generation) != self.context_generation):
                return
            source = ticket.get("sourceUrl") or (f"https://www.nexusmods.com/{ticket.get('game')}/mods/{ticket.get('modId')}" if ticket.get("provider", "nexus") == "nexus" else "")
            identity = project_identity(source)
            item_id = str(ticket.get("itemId") or "")
            row = deepcopy(self.host._items_by_id.get(item_id) or {})
            if not identity:
                return
            row.update(id=item_id or "creator-project-" + hashlib.sha256(identity["key"].encode()).hexdigest()[:24],
                       name=row.get("name") or ticket.get("title") or ticket.get("name") or identity["projectId"],
                       source={"url": source, "type": identity["provider"], "kind": "project", "verified": True},
                       nexusId=int(identity["projectId"]) if identity["provider"] == "nexus" else 0,
                       installedVersion=row.get("installedVersion", ""), followOrigin="download",
                       followDownloadId=download_id, followDownloadContext=self.context_generation,
                       followEvidence=ticket.get("evidenceKind", "native-download-complete"),
                       followReceipt="download:" + str(download_id) + ":" + str(ticket.get("operationId") or ticket.get("fileId") or identity["key"]))
            self._enqueue([row])

    def download_state(self, download_id, state):
        """Receive actual native state only; the HTTP surface cannot call this."""
        with self.host._guard:
            self._native_download_states[download_id] = state
            if len(self._native_download_states) > 4096:
                self._native_download_states.pop(next(iter(self._native_download_states)))

    def native_download_completed(self, download_id, archive_path, downloads_root, native_profile_name):
        """Capture immutable Qt facts, then read the completed sidecar off Qt."""
        with self.host._guard:
            profile, context = self.host.profile_id(), self.context_generation
            if (self.host._stopped or native_profile_name != self.host._snapshot.get("profileName")
                    or download_id in self.host._downloads or (context, download_id) in self._receipt_workers):
                return
            try:
                if not self._document(profile)["autoFollow"]:
                    return
            except ValueError:
                return
            game = str(self.host._snapshot.get("game") or "")
            self._receipt_workers.add((context, download_id))
        def current():
            return (not self.host._stopped and profile == self.host.profile_id() and context == self.context_generation
                    and self._native_download_states.get(download_id) == "complete")
        def worker():
            try:
                receipt = None
                for delay in (0, 0.25, 1, 2):
                    if delay:
                        threading.Event().wait(delay)
                    with self.host._guard:
                        if not current() or download_id in self.host._downloads:
                            return
                    try:
                        receipt = download_project_receipt(archive_path, downloads_root, game)
                    except (OSError, ValueError):
                        receipt = None
                    if receipt:
                        break
                with self.host._guard:
                    if not current() or download_id in self.host._downloads:
                        return
                    if receipt:
                        self.downloaded({**receipt, "provider": "nexus", "profileId": profile, "creatorFollowContext": context}, download_id)
                    else:
                        document = self._document(profile)
                        key = "native-download:" + str(download_id)
                        document["review"][key] = {"id": key, "itemId": "", "title": "Completed MO2 download",
                            "sourceUrl": "", "provider": "unknown", "origin": "download", "observedAt": self.clock(),
                            "reason": "The completed download has no verified project receipt yet. Link its exact installed source and discover again; filenames are not creator evidence."}
                        self._save(profile, document)
            except (ValueError, OSError) as error:
                with self.host._guard:
                    if current():
                        self._job.update(state="error", error=str(error), finishedAt=self.clock())
            finally:
                with self.host._guard:
                    self._receipt_workers.discard((context, download_id))
        threading.Thread(target=worker, name="ModhunterCreatorReceipt", daemon=True).start()

    def _enqueue(self, rows):
        try:
            if not self._document(self.host.profile_id())["autoFollow"]:
                return
            for row in rows:
                self._pending[(str(row.get("id")), str(row.get("followReceipt", "")))] = deepcopy(row)
            if not self._active:
                queued = list(self._pending.values()); self._pending.clear()
                self._start(queued, "automatic", manual=False)
        except (ValueError, OSError) as error:
            self._job.update(state="error", error=str(error), finishedAt=self.clock())

    def _start(self, rows, kind, *, manual, creator_ids=None):
        if self._active:
            return {**self.snapshot(), "accepted": False, "error": "A creator operation is already running."}
        profile, epoch, generation = self.host.profile_id(), self.host._execution_epoch, self._generation
        document = self._document(profile)
        cancel = self._cancel = threading.Event()
        if kind in {"check", "monitor", "scheduled"}:
            self._monitor_pending = False
        self._active = True
        self._job = {"state": "running", "kind": kind, "checked": 0, "total": len(rows), "startedAt": self.clock()}
        selected_creators = {cid: c["generation"] for cid, c in document["creators"].items()
                             if c["state"] == "following" and (creator_ids is None or cid in creator_ids)}
        def current():
            return (not cancel.is_set() and not self.host._stopped and profile == self.host.profile_id()
                    and epoch == self.host._execution_epoch and generation == self._generation)
        def worker():
            try:
                while True:
                    with self.host._guard:
                        if not current():
                            return
                        running = self.host._scan.get("state") == "running"
                        if running:
                            self._job["state"] = "waiting"
                    if not running:
                        break
                    self.host._scan_done.wait(0.2)
                with self.host._guard:
                    if not current():
                        return
                    self._job["state"] = "running"
                    snapshot = {key: deepcopy(value) for key, value in self.host._snapshot.items() if key != "items"}
                    snapshot["items"] = deepcopy(rows)
                records, errors = self._scan_metadata(snapshot, cancel, force=kind == "check") if snapshot["items"] else ({}, [])
                statuses, catalog_rows, catalog_records, catalog_result = {}, [], {}, None
                if kind in {"check", "monitor", "scheduled"}:
                    followed_modhold = {c["handle"].casefold(): cid for cid, c in document["creators"].items()
                                        if cid in selected_creators and c.get("provider") == "modhold"}
                    if followed_modhold:
                        try:
                            catalog, checked = self._catalog.collect(cancel)
                            catalog_result = {"checkedAt": checked}
                            known = {key for c in document["creators"].values() for key in c["projects"]}
                            for project in catalog:
                                cid = followed_modhold.get(project["handle"].casefold())
                                if not cid or project["key"] in known:
                                    continue
                                row = self._project_row({"url": project["url"], "title": project["name"], "origin": "creator-catalog"})
                                row.update(expectedCreatorId=cid, followOrigin="creator-catalog", followEvidence="complete-provider-creator-catalog")
                                catalog_rows.append(row)
                                # Catalog versions advertise a project, not a chosen main file.
                                # Exact normal checks subsequently verify release/file details.
                                catalog_records[row["id"]] = {"provider": "modhold", "sourceUrl": project["url"], "projectId": project["projectId"],
                                    "publisherId": project["handle"], "author": {"name": project["authorName"], "url": "https://modholdmods.com/u/" + project["handle"]},
                                    "latestVersion": project["version"], "checkedAt": checked, "expiresAt": checked + self._catalog.ttl,
                                    "freshness": "fresh", "outcome": "review", "verdict": "review",
                                    "reason": "New project advertised in the complete creator catalog; open its exact page to review files and compatibility."}
                        except Exception as error:
                            if cancel.is_set():
                                return
                            message = "ModHold creator catalog: " + str(error)[:350]
                            errors.append(message)
                            catalog_result = {"error": message}
                    status_deadline = time.monotonic() + 45
                    status_order = sorted(selected_creators, key=lambda cid: (document["creators"][cid].get("lastStatusAttempt") or {}).get("checkedAt") or 0)
                    for cid in status_order:
                        with self.host._guard:
                            if not current():
                                return
                            creator = self.host.chronicle.creator_follow_document(profile, cid)
                        if creator and creator["state"] == "following" and creator["generation"] == selected_creators[cid] and creator.get("provider") == "nexus" and HANDLE.fullmatch(creator.get("handle", "")):
                            if time.monotonic() >= status_deadline:
                                errors.append("Remaining creator profile checks were deferred by the time budget; last-good evidence is retained.")
                                break
                            statuses[cid] = self.host._creator_status.check(creator["handle"], creator.get("accountId"))
                with self.host._guard:
                    if not current():
                        return
                    data = self._document(profile)
                    owners = {key: cid for cid, c in data["creators"].items() for key in c["projects"]}
                    eligible = []
                    for row in rows:
                        identity = project_identity((row.get("source") or {}).get("url"))
                        cid = row.get("expectedCreatorId") or owners.get(identity["key"] if identity else "")
                        if cid in selected_creators and (cid not in data["creators"] or data["creators"][cid]["generation"] != selected_creators[cid]):
                            continue
                        eligible.append(row)
                    self._apply_rows(data, eligible, records, discover=kind != "check", manual=manual)
                    # New projects belong only to an already-followed, still-owned
                    # creator. A catalog cannot create a social follow or relocate a mod.
                    allowed_catalog = [row for row in catalog_rows if row["expectedCreatorId"] in data["creators"]
                                       and data["creators"][row["expectedCreatorId"]]["state"] == "following"
                                       and data["creators"][row["expectedCreatorId"]]["generation"] == selected_creators.get(row["expectedCreatorId"])]
                    self._apply_rows(data, allowed_catalog, catalog_records, discover=False)
                    if catalog_result is not None:
                        for cid in followed_modhold.values():
                            creator = data["creators"].get(cid)
                            if not creator or creator["state"] != "following" or creator["generation"] != selected_creators[cid]:
                                continue
                            creator["catalogError"] = catalog_result.get("error", "")
                            creator["catalogAttemptAt"] = self.clock()
                            if "checkedAt" in catalog_result:
                                creator["catalogCheckedAt"] = catalog_result["checkedAt"]
                    for cid, observation in statuses.items():
                        creator = data["creators"].get(cid)
                        if not creator or creator["state"] != "following" or creator["generation"] != selected_creators[cid]:
                            continue
                        creator["lastStatusAttempt"] = deepcopy(observation)
                        if observation.get("verified") and observation.get("accountId") == creator.get("accountId"):
                            previous = creator.get("notice", {})
                            if ((previous and previous.get("state") != observation.get("state"))
                                    or not previous and observation.get("state") == "banned"):
                                creator["noticeUnread"] = True
                            creator["notice"] = deepcopy(observation)
                            creator["lastCheckedAt"] = max(creator.get("lastCheckedAt") or 0, observation.get("checkedAt") or 0)
                    if kind in {"check", "scheduled"}:
                        for cid, owned_generation in selected_creators.items():
                            creator = data["creators"].get(cid)
                            if creator and creator["state"] == "following" and creator["generation"] == owned_generation:
                                creator["nextCheckAt"] = self.clock() + (FOLLOW_RETRY_INTERVAL if errors else FOLLOW_CHECK_INTERVAL)
                    self._save(profile, data)
                    self._job.update(state="partial" if errors else "completed", checked=len(records) + len(catalog_records), errors=errors,
                                     error="; ".join(str(error) for error in errors)[:500], finishedAt=self.clock())
                    if kind in {"automatic", "discover"} and any(c["state"] == "following" for c in data["creators"].values()):
                        self._monitor_pending = True
            except Exception as error:
                with self.host._guard:
                    if current():
                        self._job.update(state="error", error=str(error)[:500], finishedAt=self.clock())
            finally:
                with self.host._guard:
                    if generation == self._generation and cancel is self._cancel:
                        if not current() and self._job.get("state") in {"running", "waiting"}:
                            self._job.update(state="cancelled", finishedAt=self.clock())
                        self._active = False
                        if self._pending and not self.host._stopped and profile == self.host.profile_id():
                            queued = list(self._pending.values()); self._pending.clear()
                            self._start(queued, "automatic", manual=False)
                        elif (self._monitor_pending and not self.host._stopped and profile == self.host.profile_id()
                              and epoch == self.host._execution_epoch and self.host._scan.get("state") != "running"):
                            self._monitor_pending = False
                            next_document = self._document(profile)
                            self._start(self._check_rows(next_document, uninstalled_only=True), "monitor", manual=False)
        threading.Thread(target=worker, name="ModhunterCreatorFollows", daemon=True).start()
        return {**self.snapshot(), "accepted": True}

    def _scan_metadata(self, snapshot, cancel, *, force):
        from .scanner import NexusClient, NexusError, UpdateScanner
        from .source_registry import SourceScanner
        records, errors = {}, []
        nexus = [row for row in snapshot["items"] if (_row_project(row) or {}).get("provider") == "nexus"]
        if nexus:
            game = snapshot.get("game", "")
            if self.host.api_key:
                client = NexusClient(self.host.api_key)
            elif self.host.native_nexus_available:
                def native_get(path):
                    match = re.fullmatch(r"/v1/games/([a-z0-9_-]+)/mods/([1-9][0-9]*)\.json", path)
                    if not match or match[1] != game:
                        raise NexusError("Creator metadata requested a different game.")
                    result = self.host.call_ui("nexus-metadata", {"game": game, "modId": int(match[2])}, timeout=70, cancel_event=cancel)
                    if not result.get("ok"):
                        raise NexusError(str(result.get("error") or "Nexus creator metadata was unavailable."))
                    return result["meta"]
                client = NexusClient("mo2-native", request_json=native_get)
            else:
                client = NexusClient("")
            try:
                result = UpdateScanner(self.host.cache, client).scan({**snapshot, "items": nexus}, "deep" if force else "startup", cancel_event=cancel)
                records.update(result.get("itemRecords", {})); errors.extend(result.get("errors", []))
            except Exception as error:
                errors.append(str(error)[:300])
        nexus_ids = {str(row.get("id")) for row in nexus}
        external = [row for row in snapshot["items"] if str(row.get("id")) not in nexus_ids]
        if external and not cancel.is_set():
            with self.host._guard:
                if self.host._source_scanner is None:
                    self.host._source_scanner = SourceScanner(cache_path=self.host._source_cache_path)
                scanner = self.host._source_scanner
            result = scanner.scan({**snapshot, "items": external}, cancel_event=cancel, force=force)
            records.update(result.get("itemRecords", {})); errors.extend(result.get("errors", []))
        return records, errors
