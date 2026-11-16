"""Creator Continuity persistence and public profile evidence.

Public status reads never authorize source changes or installations. Nexus's
observed profile header (2026-11-16) binds its H1, avatar and adjacent status pill.
A mod-page comment, challenge, missing page or request failure proves no ban.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from datetime import datetime
from functools import lru_cache
from html.parser import HTMLParser
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

HANDLE = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
ACCOUNT_ID = re.compile(r"[1-9][0-9]{0,19}\Z")
MAX_PAGE_BYTES = 1_048_576
VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"})


def public_url(value):
    if (not isinstance(value, str) or len(value) > 2048
            or any(ord(char) <= 32 or 127 <= ord(char) <= 159 or 0xD800 <= ord(char) <= 0xDFFF or char == "\\" for char in value)):
        return ""
    try:
        url = urllib.parse.urlsplit(value)
        host = url.hostname or ""
        if (url.scheme != "https" or url.username or url.password or url.port
                or "." not in host or re.fullmatch(r"[0-9.]+", host) or ":" in host
                or re.search(r"(^|\.)(localhost|local|internal|test|invalid)$", host)
                or any(re.search(r"token|secret|auth|password|key|signature|session", key, re.I)
                       for key, _ in urllib.parse.parse_qsl(url.query))):
            return ""
        return urllib.parse.urlunsplit((url.scheme, url.netloc, url.path, url.query, ""))
    except ValueError:
        return ""


def sanitize_choice(raw):
    """Keep the reversible choice as notes, separate from native install facts."""
    if (not isinstance(raw, dict) or type(raw.get("schemaVersion")) is not int
            or raw["schemaVersion"] != 1 or not isinstance(raw.get("state"), str)
            or raw["state"] not in {"tracking", "paused", "undone"}):
        return None
    fields = ("candidateId", "authorId", "expectedAuthor", "originalUrl", "previousOverride",
              "destinationUrl", "acceptedAt", "evidenceRevision", "state")
    if any(not isinstance(raw.get(key), str) or len(raw[key]) > (2048 if "Url" in key or key == "previousOverride" else 300)
           or any(ord(ch) < 32 or 127 <= ord(ch) <= 159 or 0xD800 <= ord(ch) <= 0xDFFF for ch in raw[key]) for key in fields):
        return None
    if (not public_url(raw["originalUrl"]) or not public_url(raw["destinationUrl"])
            or raw["previousOverride"] and not public_url(raw["previousOverride"])
            or not raw["expectedAuthor"].strip() or len(raw["expectedAuthor"]) > 180
            or not raw["candidateId"] or not raw["authorId"] or not raw["evidenceRevision"]):
        return None
    try:
        accepted = datetime.fromisoformat(raw["acceptedAt"].replace("Z", "+00:00"))
        if accepted.tzinfo is None or not 0 < accepted.timestamp() <= time.time() + 300:
            return None
    except (ValueError, OverflowError):
        return None
    return {"schemaVersion": 1, **{key: raw[key] for key in fields}}


def sanitize_observation(raw):
    if (not isinstance(raw, dict) or not isinstance(raw.get("state"), str)
            or raw["state"] not in {"banned", "not-marked-banned"} or raw.get("verified") is not True):
        return None
    handle = raw.get("handle")
    if not isinstance(handle, str) or not HANDLE.fullmatch(handle) or raw.get("profileUrl") != "https://www.nexusmods.com/profile/" + handle:
        return None
    stamp = raw.get("checkedAt")
    if not isinstance(stamp, (int, float)) or isinstance(stamp, bool) or not 0 < stamp <= time.time() + 300:
        return None
    account_id = raw.get("accountId")
    if account_id is not None and (not isinstance(account_id, str) or not ACCOUNT_ID.fullmatch(account_id)):
        return None
    result = {"state": raw["state"], "verified": True, "handle": handle,
            "profileUrl": raw["profileUrl"], "checkedAt": stamp,
            "evidenceKind": "provider-profile-header"}
    if account_id:
        result["accountId"] = account_id
    return result


def _registry_project(value):
    clean = public_url(value)
    if not clean:
        return None
    url = urllib.parse.urlsplit(clean)
    parts = [part for part in url.path.split("/") if part]
    numeric = lambda part: bool(re.fullmatch(r"[1-9][0-9]{0,15}", part)) and int(part) <= 9_007_199_254_740_991
    if (url.hostname in {"nexusmods.com", "www.nexusmods.com"} and len(parts) == 3
            and parts[1] == "mods" and re.fullmatch(r"[a-z0-9_-]+", parts[0]) and numeric(parts[2])):
        return "nexus", parts[0], parts[2]
    if (url.hostname == "modholdmods.com" and len(parts) in {4, 5} and parts[0] == "g" and parts[2] == "mods"
            and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", parts[1]) and numeric(parts[3])
            and "//" not in url.path
            and (len(parts) == 4 or re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", parts[4]) and parts[4] not in {".", ".."})):
        return "modhold", parts[1], parts[3]
    return "website", "", clean


@lru_cache(maxsize=4)
def _registry_valid(value, validation_minute):
    """Validate an immutable document once per minute, not once per UI save."""
    now = validation_minute * 60
    def text(raw, limit=300):
        return (isinstance(raw, str) and 0 < len(raw) <= limit
                and not any(ord(char) < 32 or 127 <= ord(char) <= 159 or 0xD800 <= ord(char) <= 0xDFFF for char in raw))

    def stamp(raw):
        if not isinstance(raw, str) or len(raw) > 80:
            return False
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return parsed.tzinfo is not None and 0 < parsed.timestamp() <= now + 300
        except (ValueError, OverflowError):
            return False

    def origin(raw):
        parsed = urllib.parse.urlsplit(public_url(raw))
        return parsed.scheme, parsed.hostname, parsed.port

    try:
        raw = json.loads(value)
        if (not isinstance(raw, dict) or type(raw.get("schemaVersion")) is not int or raw["schemaVersion"] != 1
                or raw.get("kind") != "creator-continuity-registry"
                or not text(raw.get("revision")) or not stamp(raw.get("updatedAt"))
                or not isinstance(raw.get("authors"), list) or not isinstance(raw.get("migrations"), list)
                or len(raw["authors"]) > 10000 or len(raw["migrations"]) > 50000):
            return False
        authors, handles, projects, migrations = {}, set(), set(), set()
        for author in raw["authors"]:
            if not isinstance(author, dict):
                return False
            source, status, destinations = author.get("source"), author.get("status"), author.get("destinations")
            if (not text(author.get("id")) or author["id"] in authors or not text(author.get("name"))
                    or not isinstance(source, dict) or not isinstance(status, dict) or not isinstance(destinations, list)
                    or len(destinations) > 100 or not text(source.get("provider"), 60) or not text(source.get("handle"), 180)
                    or not public_url(source.get("profileUrl")) or not text(status.get("state"))
                    or status["state"] not in {"active", "banned", "removed", "suspended", "moved", "unknown"}
                    or not stamp(status.get("observedAt")) or not public_url(status.get("evidenceUrl"))
                    or not text(status.get("evidenceKind"))):
                return False
            if source.get("accountId") is not None and (not isinstance(source["accountId"], str) or not ACCOUNT_ID.fullmatch(source["accountId"])):
                return False
            handle_key = source["provider"], source["handle"].lower()
            if handle_key in handles:
                return False
            handles.add(handle_key)
            authors[author["id"]] = author
            for destination in destinations:
                if not isinstance(destination, dict):
                    return False
                evidence = destination.get("identityEvidence")
                if (not text(destination.get("provider"), 60) or not text(destination.get("handle"), 180)
                        or not public_url(destination.get("profileUrl")) or not isinstance(evidence, dict)
                        or not public_url(evidence.get("url")) or not public_url(evidence.get("targetUrl"))
                        or not stamp(evidence.get("observedAt"))):
                    return False
        for migration in raw["migrations"]:
            if not isinstance(migration, dict):
                return False
            source, destination, evidence = migration.get("from"), migration.get("to"), migration.get("evidenceUrls")
            if not isinstance(source, dict) or not isinstance(destination, dict):
                return False
            before, after = _registry_project(source.get("url")), _registry_project(destination.get("url"))
            if (not text(migration.get("id")) or migration["id"] in migrations or not text(migration.get("authorId"))
                    or migration["authorId"] not in authors or not text(migration.get("title"))
                    or migration.get("reviewRequired") is not True or not before or not after or before == after
                    or before in projects or not stamp(migration.get("observedAt"))
                    or not isinstance(evidence, list) or not 1 <= len(evidence) <= 20
                    or any(not public_url(url) for url in evidence)):
                return False
            for side, identity in ((source, before), (destination, after)):
                if not text(side.get("provider"), 60):
                    return False
                if ((side["provider"] in {"nexus", "modhold"} or identity[0] != "website")
                        and (side["provider"], side.get("game"), str(side.get("projectId"))) != identity):
                    return False
            author = authors[migration["authorId"]]
            if (author["source"]["provider"] != source["provider"]
                    or not any(d["provider"] == destination["provider"] and origin(d["profileUrl"]) == origin(destination["url"])
                               for d in author["destinations"])):
                return False
            projects.add(before)
            migrations.add(migration["id"])
        return True
    except (ValueError, TypeError, RecursionError, OverflowError):
        return False


def sanitize_registry(value):
    """Bound imported local notices; imports never become provider attestations."""
    if not isinstance(value, str) or not value or len(value) > MAX_PAGE_BYTES:
        return ""
    try:
        if len(value.encode("utf-8")) > MAX_PAGE_BYTES:
            return ""
    except UnicodeError:
        return ""
    return value if _registry_valid(value, int(time.time() // 60)) else ""


class _ProfileHeader(HTMLParser):
    def __init__(self, handle):
        super().__init__(convert_charrefs=True)
        self.handle = handle.lower()
        self.stack = []
        self.nodes = []
        self.avatars = []
        self.avatar_parents = set()
        self.account_id = ""
        self.headings = []
        self.pills = []

    def handle_starttag(self, tag, attrs):
        if len(self.nodes) >= 50000 or len(self.stack) >= 128:
            raise ValueError("Profile markup exceeds parser limits")
        attributes = dict(attrs)
        parent = self.stack[-1] if self.stack else None
        node = {"tag": tag, "parent": parent, "classes": set(attributes.get("class", "").split()), "text": []}
        index = len(self.nodes)
        self.nodes.append(node)
        if tag == "img" and attributes.get("alt", "").lower() == self.handle:
            src = attributes.get("src", "")
            match = re.fullmatch(r"https://avatars\.nexusmods\.com/([1-9][0-9]{0,19})/[0-9]+", src)
            if match:
                self.avatars.append((parent, match.group(1)))
                self.avatar_parents.add(parent)
        if tag not in VOID:
            self.stack.append(index)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if any(self.nodes[index]["tag"] in {"script", "style", "noscript", "template"} for index in self.stack):
            return
        for index in self.stack:
            node = self.nodes[index]
            if node["tag"] == "h1" or "nxm-pill-label" in node["classes"] or index in self.avatar_parents:
                if sum(map(len, node["text"])) < 400:
                    node["text"].append(data[:400])

    def handle_endtag(self, tag):
        for position in range(len(self.stack) - 1, -1, -1):
            index = self.stack[position]
            node = self.nodes[index]
            if node["tag"] != tag:
                continue
            if tag == "h1":
                self.headings.append(index)
            if "nxm-pill-label" in node["classes"]:
                self.pills.append(index)
            del self.stack[position:]
            return

    def result(self):
        if len(self.headings) != 1:
            return "unknown"
        heading = self.nodes[self.headings[0]]
        if "".join(heading["text"]).strip().lower() != self.handle or heading["parent"] is None:
            return "unknown"
        header = self.nodes[heading["parent"]]["parent"]
        avatars = [(index, account_id) for index, account_id in self.avatars
                   if index is not None and self.nodes[index]["parent"] == header]
        if header is None or len(avatars) != 1:
            return "unknown"
        avatar, self.account_id = avatars[0]
        labels = []
        for index in self.pills:
            label = self.nodes[index]
            parent = label["parent"]
            if parent is None:
                continue
            pill = self.nodes[parent]
            if pill["parent"] == avatar and {"nxm-pill", "nxm-pill-danger"}.issubset(pill["classes"]):
                labels.append("".join(label["text"]).strip().lower())
        if labels == ["banned"]:
            return "banned"
        # An unfamiliar status-pill structure containing Banned is unknown,
        # never evidence that a formerly banned account is unmarked again.
        if re.search(r"\bbanned\b", "".join(self.nodes[avatar]["text"]), re.I):
            return "unknown"
        return "unknown" if labels else "not-marked-banned"


def parse_nexus_profile(html, handle):
    return parse_nexus_profile_evidence(html, handle)["state"]


def parse_nexus_profile_evidence(html, handle):
    """Bind status to the exact profile header and its stable numeric account."""
    if not isinstance(handle, str) or not HANDLE.fullmatch(handle):
        raise ValueError("Invalid Nexus profile handle")
    if not isinstance(html, str) or len(html.encode("utf-8")) > MAX_PAGE_BYTES:
        raise ValueError("Profile exceeds reader limits")
    parser = _ProfileHeader(handle)
    parser.feed(html)
    parser.close()
    state = parser.result()
    return {"state": state, "accountId": parser.account_id}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_nexus_profile(handle, *, opener=None, clock=time.time):
    if not isinstance(handle, str) or not HANDLE.fullmatch(handle):
        raise ValueError("Invalid Nexus profile handle")
    url = "https://www.nexusmods.com/profile/" + handle
    result = {"handle": handle, "profileUrl": url, "checkedAt": clock(),
              "state": "unknown", "verified": False, "evidenceKind": "provider-profile-header"}
    request = urllib.request.Request(url, headers={"User-Agent": "Modhunter/0.12 (Creator Continuity public metadata)",
                                                  "Accept": "text/html", "Accept-Encoding": "identity"})
    try:
        with (opener or urllib.request.build_opener(_NoRedirect())).open(request, timeout=10) as response:
            if response.geturl() != url or response.status != 200:
                return {**result, "reason": "The response did not identify the requested public profile."}
            if response.headers.get_content_type() not in {"text/html", "application/xhtml+xml"}:
                return {**result, "reason": "The provider did not return a public profile page."}
            if response.headers.get("Content-Encoding", "identity").lower() not in {"identity", ""}:
                return {**result, "reason": "The public profile used an unsupported encoding."}
            content = bytearray()
            deadline = time.monotonic() + 10
            reader = getattr(response, "read1", response.read)
            while len(content) <= MAX_PAGE_BYTES:
                if time.monotonic() > deadline:
                    raise TimeoutError()
                part = reader(min(65536, MAX_PAGE_BYTES + 1 - len(content)))
                if not part:
                    break
                content.extend(part)
            evidence = parse_nexus_profile_evidence(content.decode("utf-8"), handle)
            state = evidence["state"]
            return {**result, **evidence, "verified": state != "unknown",
                    "reason": "Profile header explicitly marks this author banned." if state == "banned"
                    else "The verified profile header has no banned badge; earlier observations remain in history." if state == "not-marked-banned"
                    else "The current profile structure did not establish an account status."}
    except (urllib.error.URLError, OSError, UnicodeError, ValueError):
        return {**result, "reason": "The public profile could not be verified. Earlier evidence is retained."}


class CreatorStatusMonitor:
    """Coalesce public reads across mods sharing an author; retain last good."""
    def __init__(self, fetch=fetch_nexus_profile, clock=time.time):
        self.fetch, self.clock = fetch, clock
        self._cache = OrderedDict()
        self._lock = threading.Lock()
        self._pending = {}

    @staticmethod
    def _bind_account(observation, expected_account_id):
        result = deepcopy(observation)
        if (expected_account_id
                and result.get("lastVerified", {}).get("accountId") != expected_account_id):
            result.pop("lastVerified", None)
        if (expected_account_id and result.get("verified")
                and result.get("accountId") != expected_account_id):
            result.update(state="unknown", verified=False, identityChanged=True,
                          expectedAccountId=expected_account_id,
                          reason="This handle belongs to a different Nexus account than the saved creator identity. Earlier evidence is retained.")
        return result

    def check(self, handle, expected_account_id=None):
        if not isinstance(handle, str) or not HANDLE.fullmatch(handle):
            raise ValueError("Invalid Nexus profile handle")
        if expected_account_id not in (None, "") and (not isinstance(expected_account_id, str)
                                                       or not ACCOUNT_ID.fullmatch(expected_account_id)):
            raise ValueError("Invalid Nexus account identity")
        key = handle.lower()
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached["retryAt"] > self.clock():
                return self._bind_account(cached, expected_account_id)
            pending = self._pending.get(key)
            owner = pending is None
            if owner:
                pending = self._pending[key] = threading.Event()
        if not owner:
            completed = pending.wait(12)
            with self._lock:
                cached = self._cache.get(key)
                if completed and cached:
                    return self._bind_account(cached, expected_account_id)
                result = {"handle": handle, "profileUrl": "https://www.nexusmods.com/profile/" + handle,
                          "checkedAt": self.clock(), "state": "unknown", "verified": False,
                          "reason": "This creator check is already running."}
                if cached and cached.get("lastVerified"):
                    result["lastVerified"] = deepcopy(cached["lastVerified"])
                return self._bind_account(result, expected_account_id)
        try:
            try:
                observation = self.fetch(handle)
                if not isinstance(observation, dict):
                    raise ValueError("Invalid profile observation")
                if observation.get("verified") and (
                        sanitize_observation(observation) is None
                        or not observation.get("accountId")
                        or str(observation.get("handle", "")).lower() != key):
                    raise ValueError("Profile observation did not bind this account")
                observation = deepcopy(observation)
            except Exception:
                # A failed transport/reader must release coalesced waiters and
                # retain the last verified account; it is never a ban result.
                observation = {"handle": handle, "profileUrl": "https://www.nexusmods.com/profile/" + handle,
                               "checkedAt": self.clock(), "state": "unknown", "verified": False,
                               "evidenceKind": "provider-profile-header",
                               "reason": "The public profile could not be verified. Earlier evidence is retained."}
            with self._lock:
                previous = self._cache.get(key)
                previous_verified = previous.get("lastVerified", {}) if previous else {}
                old_account = previous_verified.get("accountId")
                if (observation.get("verified") and old_account
                        and observation.get("accountId") != old_account):
                    observation.update(state="unknown", verified=False, identityChanged=True,
                                       expectedAccountId=old_account,
                                       reason="The Nexus profile handle now identifies a different account. Earlier creator evidence is retained.")
                if observation.get("verified"):
                    observation["lastVerified"] = {key: value for key, value in observation.items() if key != "lastVerified"}
                elif previous_verified:
                    observation["lastVerified"] = deepcopy(previous_verified)
                observation["retryAt"] = self.clock() + (900 if observation.get("verified") else 120)
                self._cache[key] = deepcopy(observation)
                self._cache.move_to_end(key)
                while len(self._cache) > 2048:
                    self._cache.popitem(last=False)
                return self._bind_account(observation, expected_account_id)
        finally:
            with self._lock:
                self._pending.pop(key, None)
                pending.set()
