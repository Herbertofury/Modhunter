"""Evidence-based, provider-aware mod health without changing installed files.

Nexus's public Mod API does not expose mod bug counts or the bugs-tab setting.
In particular, collection bug-report fields are not mod bug-report evidence:
https://graphql.nexusmods.com/#definition-Mod
https://github.com/Nexus-Mods/node-nexus-api/blob/master/docs/interfaces/_types_.imodinfo.md

The optional public-page reader uses the actual Nexus navigation markup observed
at /skyrimspecialedition/mods/145168?tab=bugs on 2026-11-12. Its ``Bugs 1`` badge
coexists with two report rows, so a badge is deliberately *not* called an open or
total report count. Absence of the tab is never evidence of author removal.
Only small scalar observations are cached; HTML, cookies and credentials are not.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
from html.parser import HTMLParser
import http.client
import math
import re
import threading
import time
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request

from .source_registry import canonical_source_url, provider_of


NEXUS_ORIGIN = "https://www.nexusmods.com"
MAX_PAGE_BYTES = 1_048_576
PAGE_TTL_SECONDS = 15 * 60
METADATA_TTL_SECONDS = 24 * 60 * 60
MAX_COUNT = 2_147_483_647
_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{1,63}\Z")
_PATH = re.compile(r"/([a-z0-9][a-z0-9_-]{1,63})/mods/([1-9][0-9]{0,9})/?\Z")
_VOID_TAGS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"})
_IGNORED_TAGS = frozenset({"script", "style", "noscript", "template"})


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= MAX_COUNT else None
    if isinstance(value, str) and re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)", value.strip()):
        try:
            number = int(value.strip().replace(",", ""))
            return number if number <= MAX_COUNT else None
        except ValueError:
            pass
    return None


def _timestamp(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        if isinstance(value, str) and not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return None
            result = parsed.timestamp()
        else:
            result = float(value)
        return result if math.isfinite(result) and 0 < result < 32_503_680_000 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _iso(value: Any) -> str | None:
    stamp = _timestamp(value)
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat().replace("+00:00", "Z") if stamp else None


def freshness(checked_at: Any, now: float, max_age: int = METADATA_TTL_SECONDS) -> dict:
    stamp = _timestamp(checked_at)
    age = now - stamp if stamp is not None else None
    state = "unknown" if age is None else "clock-skew" if age < -300 else "stale" if age > max_age else "fresh"
    return {"state": state, "ageSeconds": max(0, int(age)) if age is not None else None,
            "maxAgeSeconds": max_age}


def _identity(game: str, mod_id: Any) -> tuple[str, int]:
    number = _integer(mod_id)
    if not isinstance(game, str) or not _SLUG.fullmatch(game) or game == "unknown" or not number:
        raise ValueError("A known Nexus game and positive mod ID are required")
    return game, number


def nexus_page_url(game: str, mod_id: Any, tab: str = "bugs") -> str:
    game, number = _identity(game, mod_id)
    if tab not in {"bugs", "description", "files", "logs"}:
        raise ValueError("Unsupported Nexus page tab")
    return f"{NEXUS_ORIGIN}/{game}/mods/{number}?tab={tab}"


def _same_mod_url(url: str, game: str, mod_id: int, *, strict_origin: bool = True) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
        expected_hosts = {"www.nexusmods.com"} if strict_origin else {"www.nexusmods.com", "nexusmods.com"}
        if parsed.scheme != "https" or parsed.hostname not in expected_hosts or parsed.username or parsed.password or parsed.port:
            return False
        match = _PATH.fullmatch(parsed.path)
        return bool(match and match.group(1) == game and int(match.group(2)) == mod_id)
    except (TypeError, ValueError):
        return False


def nexus_identity(item: dict, game: str = "") -> tuple[str, int] | None:
    """A verified external source takes precedence over an old Nexus metadata ID."""
    source = item.get("source") if isinstance(item.get("source"), dict) else {}
    authoritative_link = bool(source.get("verified")) and source.get("kind") != "advisory"
    url = canonical_source_url(source.get("url", "")) if authoritative_link else ""
    if url:
        parsed = urllib.parse.urlsplit(url)
        # canonical_source_url already validated/sanitized this URL. Running it
        # through provider_of would repeat that entire pass for every live row.
        if parsed.hostname not in {"nexusmods.com", "www.nexusmods.com"}:
            return None
        match = _PATH.fullmatch(parsed.path)
        if not match:
            return None
        linked_game, linked_id = match.group(1), int(match.group(2))
        supplied_id = _integer(item.get("nexusId"))
        if (game and game != "unknown" and linked_game != game) or (supplied_id and supplied_id != linked_id):
            return None
        return _identity(linked_game, linked_id)
    try:
        return _identity(game, item.get("nexusId"))
    except ValueError:
        return None


def source_links(item: dict, game: str = "") -> dict[str, str]:
    return _source_links(item, nexus_identity(item, game))


def _source_links(item: dict, identity: tuple[str, int] | None) -> dict[str, str]:
    if identity:
        base = f"{NEXUS_ORIGIN}/{identity[0]}/mods/{identity[1]}"
        # Nexus's Logs tab is site activity. Changelogs and requirements belong
        # to the description; do not mislabel the activity log as a changelog.
        return {"project": base, "files": base + "?tab=files", "bugs": base + "?tab=bugs",
                "changelog": base + "?tab=description", "requirements": base + "?tab=description",
                "activity": base + "?tab=logs"}
    source = item.get("source") if isinstance(item.get("source"), dict) else {}
    url = canonical_source_url(source.get("url", ""))
    if not url:
        return {}
    links = {"project": url}
    # GitHub's issue count includes pull requests in some APIs. This module
    # offers a link and never reuses it as a Nexus-style bug count.
    parsed = urllib.parse.urlsplit(url)
    if source.get("verified") and source.get("kind") != "advisory" and parsed.hostname in {"github.com", "www.github.com"}:
        parts = parsed.path.strip("/").split("/")
        if len(parts) >= 2 and all(re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", p) for p in parts[:2]):
            base = "https://github.com/" + "/".join(parts[:2]).removesuffix(".git")
            links.update(files=base + "/releases", changelog=base + "/releases", bugs=base + "/issues", requirements=base)
    return links


def _bugs(*, state: str = "unknown", reason: str = "Nexus's mod metadata API does not expose bug counts or the bugs-tab setting.",
          reason_code: str = "not-observed", url: str = "", checked_at: Any = None, source: str = "") -> dict:
    return {"state": state, "displayedCount": None, "countLabel": "Nexus displayed bug count",
            "total": None, "open": None, "resolved": None, "url": url, "source": source,
            "checkedAt": _timestamp(checked_at), "reasonCode": reason_code, "reason": reason,
            "changedSincePrevious": None, "attributedTo": None}


class _NexusMarkup(HTMLParser):
    """Small bounded extraction of actual Nexus tab markup, never script eval."""
    def __init__(self, game: str, mod_id: int, url: str):
        super().__init__(convert_charrefs=True)
        self.game, self.mod_id, self.url = game, mod_id, url
        self.stack: list[dict] = []
        self.nodes = 0
        self.tabs: list[dict] = []
        self.title = ""
        self.has_mod_nav = False
        self.identity_mismatch = False
        self.canonical_seen = False
        self.notices: list[str] = []
        self.challenge = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.nodes += 1
        if self.nodes > 50_000 or len(self.stack) >= 128:
            raise ValueError("Nexus page markup exceeded parser limits")
        values = dict(attrs)
        node_id = values.get("id") or ""
        classes = set((values.get("class") or "").split())
        ignored = tag in _IGNORED_TAGS or bool(self.stack and self.stack[-1]["ignored"])
        nav = next((p for p in reversed(self.stack) if p["id"].startswith("mod-page-tab-")), None)
        bug_region = any(p["id"] == "tab-modbugs" for p in self.stack)
        if tag == "link" and "canonical" in (values.get("rel") or "").split():
            canonical = urllib.parse.urljoin(self.url, values.get("href") or "")
            self.canonical_seen = True
            if not _same_mod_url(canonical, self.game, self.mod_id, strict_origin=False):
                self.identity_mismatch = True
        if node_id == "bugs_tab_h2" and values.get("data-mod-id") is not None:
            if _integer(values.get("data-mod-id")) != self.mod_id:
                self.identity_mismatch = True
        if node_id.startswith("cf-chl-") or "cf-turnstile" in classes:
            self.challenge = True
        href = urllib.parse.urljoin(self.url, values.get("href") or "") if tag == "a" else ""
        same = bool(href and _same_mod_url(href, self.game, self.mod_id))
        if nav and same:
            self.has_mod_nav = True
        is_bug_anchor = bool(nav and nav["id"] == "mod-page-tab-bugs" and same
                             and urllib.parse.parse_qs(urllib.parse.urlsplit(href).query).get("tab") == ["bugs"])
        notice = bug_region and not ignored and (values.get("role") in {"alert", "status"} or bool(classes & {"alert", "notice", "message"}))
        frame = {"tag": tag, "id": node_id, "ignored": ignored, "text": [], "size": 0,
                 "capture": tag == "title" or is_bug_anchor or (tag == "span" and "alert" in classes and bool(nav)) or notice,
                 "bug_anchor": is_bug_anchor, "count": tag == "span" and "alert" in classes and bool(nav),
                 "counts": [], "notice": notice, "hidden": "hidden" in values or values.get("aria-hidden") == "true",
                 "disabled": "disabled" in values or values.get("aria-disabled") == "true"}
        if is_bug_anchor and nav:
            frame["hidden"] = frame["hidden"] or nav["hidden"]
            frame["disabled"] = frame["disabled"] or nav["disabled"]
        if tag not in _VOID_TAGS:
            self.stack.append(frame)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self.stack and self.stack[-1]["ignored"]:
            return
        for frame in self.stack:
            if frame["capture"] and frame["size"] < 4096:
                value = data[:4096 - frame["size"]]
                frame["text"].append(value)
                frame["size"] += len(value)

    def handle_endtag(self, tag: str) -> None:
        matching = next((i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i]["tag"] == tag), None)
        if matching is None:
            return
        while len(self.stack) > matching:
            frame = self.stack.pop()
            text = " ".join(" ".join(frame["text"]).split())
            if frame["tag"] == "title":
                self.title = text[:300]
            if frame["count"]:
                anchor = next((p for p in reversed(self.stack) if p["bug_anchor"]), None)
                if anchor is not None:
                    anchor["counts"].append(_integer(text))
            if frame["bug_anchor"]:
                # Exact semantic label plus known #mod-page-tab-bugs structure.
                if re.fullmatch(r"Bugs(?:\s+[0-9][0-9,]*)?", text, re.I):
                    self.tabs.append({"hidden": frame["hidden"], "disabled": frame["disabled"],
                                      "count": frame["counts"][0] if len(frame["counts"]) == 1 else None})
            if frame["notice"] and len(text) <= 240:
                self.notices.append(text)


def _observation(game: str, mod_id: int, checked_at: float, state: str, http_status: int | None,
                 reason: str, *, page_status: str = "unknown") -> dict:
    url = nexus_page_url(game, mod_id)
    return {"schema": 1, "provider": "nexus", "identity": {"game": game, "modId": mod_id},
            "url": url, "checkedAt": checked_at, "fetch": {"state": state, "httpStatus": http_status},
            "page": {"status": page_status, "source": "nexus-public-page", "checkedAt": checked_at},
            "bugs": _bugs(state="unknown" if state == "ok" else "unavailable", reason=reason,
                          reason_code="tab-not-observed" if state == "ok" else state, url=url,
                          checked_at=checked_at, source="nexus-public-page")}


def parse_nexus_page(html: bytes | str, *, game: str, mod_id: int, url: str | None = None,
                     checked_at: float | None = None) -> dict:
    """Extract only explicitly observed, identity-bound facts from public HTML."""
    game, mod_id = _identity(game, mod_id)
    stamp = _timestamp(checked_at) or time.time()
    url = url or nexus_page_url(game, mod_id)
    if not _same_mod_url(url, game, mod_id):
        return _observation(game, mod_id, stamp, "identity-mismatch", None, "The returned page does not identify this Nexus mod.")
    if not isinstance(html, (bytes, str)) or len(html) > MAX_PAGE_BYTES:
        return _observation(game, mod_id, stamp, "response-too-large", 200, "The public page exceeded the bounded reader limit.")
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="replace")
    elif len(html.encode("utf-8")) > MAX_PAGE_BYTES:
        return _observation(game, mod_id, stamp, "response-too-large", 200, "The public page exceeded the bounded reader limit.")
    parser = _NexusMarkup(game, mod_id, url)
    try:
        parser.feed(html)
        parser.close()
    except (ValueError, RecursionError):
        return _observation(game, mod_id, stamp, "unsupported-page", 200, "The public page could not be parsed within the bounded reader limits.")
    if parser.identity_mismatch:
        return _observation(game, mod_id, stamp, "identity-mismatch", 200, "The returned page identifies a different mod.")
    title = parser.title.lower()
    if not parser.has_mod_nav:
        if parser.challenge or any(word in title for word in ("just a moment", "attention required", "verify you are human", "security verification")):
            return _observation(game, mod_id, stamp, "challenge", 200, "Nexus requested browser verification; the bugs-tab setting is unknown.")
        if re.search(r"\b(log in|sign in|login|age verification)\b", title):
            return _observation(game, mod_id, stamp, "authentication-required", 200, "This public response requires browser access; the bugs-tab setting is unknown.")
    observed = _observation(game, mod_id, stamp, "ok", 200,
                            "The bugs tab was not observed in the returned page; disabled, hidden and unavailable cannot be distinguished.",
                            page_status="available" if parser.has_mod_nav else "unknown")
    # A precise scoped notice can establish a state; page descriptions and
    # users' report bodies never count as a tab-setting message.
    explicit_states = set()
    for notice in parser.notices:
        match = re.fullmatch(r"(?:bug reports?|bug reporting|the bugs (?:tab|section)) (?:is|are|has been|have been) (disabled|hidden|locked)(?: for this mod)?[.!]?", notice, re.I)
        if match:
            explicit_states.add(match.group(1).lower())
    if len(explicit_states) == 1:
        state = explicit_states.pop()
        observed["bugs"].update(state=state, reasonCode="explicit-page-notice",
                                reason=f"The returned Nexus bugs section explicitly says reporting is {state}; no actor is inferred.")
    elif len(explicit_states) > 1:
        observed["bugs"].update(reasonCode="conflicting-page-evidence", reason="The returned page contains conflicting bugs-section notices.")
    elif parser.tabs:
        states = {"hidden" if node["hidden"] else "disabled" if node["disabled"] else "available" for node in parser.tabs}
        counts = {node["count"] for node in parser.tabs}
        if len(states) == 1 and len(counts) == 1:
            state = states.pop()
            observed["bugs"].update(state=state, displayedCount=counts.pop() if state == "available" else None,
                                    reasonCode="observed-bugs-navigation",
                                    reason="Observed Nexus bugs navigation. The displayed badge is not a verified total/open/resolved count." if state == "available" else f"The returned bugs navigation is explicitly {state}; no actor is inferred.")
        else:
            observed["bugs"].update(reasonCode="conflicting-page-evidence", reason="The returned page contains inconsistent bugs navigation.")
    return observed


class _RedirectBlocked(Exception):
    def __init__(self, state: str):
        self.state = state
        super().__init__(state)


class _SameModRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, game: str, mod_id: int):
        self.game, self.mod_id = game, mod_id
        self.redirects = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.redirects += 1
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.hostname == "users.nexusmods.com":
            raise _RedirectBlocked("authentication-required")
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        if self.redirects > 2 or not _same_mod_url(newurl, self.game, self.mod_id) or any(key != "tab" for key in query) or query.get("tab", ["bugs"]) != ["bugs"]:
            raise _RedirectBlocked("redirect-blocked")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _public_opener(game: str, mod_id: int):
    # Honour anonymous system/network proxies (also needed in managed networks)
    # without importing proxy passwords or browser/session authentication.
    proxies = {}
    for scheme, value in urllib.request.getproxies().items():
        if scheme not in {"http", "https"}:
            continue
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password:
            proxies[scheme] = value
    return urllib.request.build_opener(urllib.request.ProxyHandler(proxies), _SameModRedirect(game, mod_id))


class NexusPageClient:
    """Public-only bounded reader for a background worker, with scalar TTL cache.

    ``opener_factory(game, mod_id)`` is injectable for deterministic tests. The
    default has no cookies, browser session, API key, proxy authentication or
    cross-origin redirect support. No retry is attempted on a blocked response.
    """
    def __init__(self, *, ttl_seconds: int = PAGE_TTL_SECONDS, error_ttl_seconds: int = 60,
                 timeout: float = 10, max_entries: int = 512,
                 opener_factory: Callable[[str, int], Any] | None = None,
                 clock: Callable[[], float] = time.time):
        self.ttl = max(30, min(int(ttl_seconds), METADATA_TTL_SECONDS))
        self.error_ttl = max(5, min(int(error_ttl_seconds), 600))
        self.timeout = max(1.0, min(float(timeout), 20.0))
        self.max_entries = max(1, min(int(max_entries), 4096))
        self.clock = clock
        self.opener_factory = opener_factory or _public_opener
        self._lock = threading.Lock()
        self._cache: OrderedDict[tuple[str, int], dict] = OrderedDict()
        self._known_bugs: dict[tuple[str, int], dict] = {}
        self._pending: dict[tuple[str, int], threading.Event] = {}
        self._rate_limited_until = 0.0

    def fetch(self, game: str, mod_id: int, *, force: bool = False) -> dict:
        key = _identity(game, mod_id)
        now = self.clock()
        with self._lock:
            cached = self._cache.get(key)
            if cached and (not force or cached["fetch"]["state"] == "rate-limited") and cached["cache"]["expiresAt"] > now:
                self._cache.move_to_end(key)
                result = deepcopy(cached)
                result["cache"]["hit"] = True
                return result
            if self._rate_limited_until > now:
                result = _observation(*key, now, "rate-limited", 429, "Nexus requested a pause before another public-page check.")
                result["cache"] = {"hit": True, "expiresAt": self._rate_limited_until}
                return result
            pending = self._pending.get(key)
            owner = pending is None
            if owner:
                pending = self._pending[key] = threading.Event()
        if not owner:
            if pending.wait(self.timeout + 2):
                with self._lock:
                    cached = self._cache.get(key)
                    if cached:
                        result = deepcopy(cached)
                        result["cache"]["hit"] = True
                        return result
            return _observation(*key, now, "in-flight-timeout", None, "A public-page check is still running; no duplicate request was sent.")
        result = None
        try:
            result = self._fetch_once(*key)
            with self._lock:
                previous = self._known_bugs.get(key)
                new_state = result["bugs"]["state"]
                if new_state in {"available", "disabled", "hidden", "locked"}:
                    if previous and result["checkedAt"] >= previous["checkedAt"]:
                        result["bugs"]["changedSincePrevious"] = previous["state"] != new_state
                        result["bugs"]["previousState"] = previous["state"]
                    self._known_bugs[key] = {"state": new_state, "checkedAt": result["checkedAt"]}
                lifetime = self.ttl if result["fetch"]["state"] == "ok" else self.error_ttl
                if result["fetch"]["state"] == "rate-limited":
                    lifetime = max(lifetime, result.pop("retryAfterSeconds", 60))
                    self._rate_limited_until = self.clock() + lifetime
                result["cache"] = {"hit": False, "expiresAt": self.clock() + lifetime}
                self._cache[key] = deepcopy(result)
                self._cache.move_to_end(key)
                while len(self._cache) > self.max_entries:
                    expired_key, _ = self._cache.popitem(last=False)
                    self._known_bugs.pop(expired_key, None)
            return result
        finally:
            with self._lock:
                self._pending.pop(key, None)
                pending.set()

    def _fetch_once(self, game: str, mod_id: int) -> dict:
        stamp = self.clock()
        url = nexus_page_url(game, mod_id)
        request = urllib.request.Request(url, headers={"User-Agent": "Modhunter/0.9 (public mod health)", "Accept": "text/html", "Accept-Encoding": "identity"})
        deadline = time.monotonic() + self.timeout
        try:
            with self.opener_factory(game, mod_id).open(request, timeout=self.timeout) as response:
                status = getattr(response, "status", 200)
                if not _same_mod_url(response.geturl(), game, mod_id):
                    return _observation(game, mod_id, stamp, "identity-mismatch", status, "The public response did not identify the requested mod.")
                content_type = str(response.headers.get("Content-Type", "")).split(";", 1)[0].lower()
                if status != 200 or content_type not in {"text/html", "application/xhtml+xml"}:
                    return _observation(game, mod_id, stamp, "unavailable", status, "Nexus did not return a readable public HTML page.")
                length = _integer(response.headers.get("Content-Length"))
                if length is not None and length > MAX_PAGE_BYTES:
                    return _observation(game, mod_id, stamp, "response-too-large", status, "The public page exceeded the bounded reader limit.")
                if response.headers.get("Content-Encoding", "identity").lower() not in {"identity", ""}:
                    return _observation(game, mod_id, stamp, "unsupported-encoding", status, "The public response used an unsupported content encoding.")
                chunks: list[bytes] = []
                size = 0
                read = getattr(response, "read1", response.read)
                while size <= MAX_PAGE_BYTES:
                    if time.monotonic() > deadline:
                        raise TimeoutError("Public page deadline reached")
                    chunk = read(min(65_536, MAX_PAGE_BYTES + 1 - size))
                    if not chunk:
                        break
                    size += len(chunk)
                    chunks.append(chunk)
                if time.monotonic() > deadline:
                    raise TimeoutError("Public page deadline reached")
                return parse_nexus_page(b"".join(chunks), game=game, mod_id=mod_id, url=response.geturl(), checked_at=stamp)
        except _RedirectBlocked as ex:
            return _observation(game, mod_id, stamp, ex.state, None, "Nexus redirected away from this public mod page; no redirected page was requested.")
        except urllib.error.HTTPError as ex:
            try:
                code = ex.code
                state = {401: "authentication-required", 403: "forbidden", 404: "not-found", 429: "rate-limited"}.get(code, "unavailable")
                result = _observation(game, mod_id, stamp, state, code,
                                      f"Nexus returned HTTP {code}; this does not establish the bugs-tab setting.",
                                      page_status="not-found" if code == 404 else "unknown")
                if code == 429:
                    result["retryAfterSeconds"] = max(60, min(_integer((ex.headers or {}).get("Retry-After")) or 60, 3600))
                return result
            finally:
                ex.close()
        except (TimeoutError, urllib.error.URLError, OSError, http.client.HTTPException) as ex:
            timeout = isinstance(ex, TimeoutError) or isinstance(getattr(ex, "reason", None), TimeoutError)
            return _observation(game, mod_id, stamp, "timeout" if timeout else "unavailable", None,
                                "The public-page request timed out." if timeout else "The public-page request failed; the bugs-tab setting is unknown.")


def _metadata_page(metadata: dict | None, checked_at: Any, now: float, game: str, mod_id: int) -> dict:
    page = {"status": "unknown", "providerStatus": None, "updatedAt": None, "updatedTimestamp": None,
            "source": "", "sourceUrl": "", "checkedAt": None, "changedSincePrevious": None}
    if not isinstance(metadata, dict) or not metadata:
        return page
    if ((metadata.get("mod_id") is not None and _integer(metadata["mod_id"]) != mod_id)
            or (metadata.get("domain_name") is not None and metadata["domain_name"] != game)):
        page["reasonCode"] = "identity-mismatch"
        return page
    raw = str(metadata.get("status") or "").lower()
    status = {"published": "available", "hidden": "hidden", "removed": "removed", "wastebinned": "removed",
              "under_moderation": "under-moderation", "not_published": "unpublished", "publish_with_game": "unpublished", "not_found": "not-found"}.get(raw, "unknown")
    if metadata.get("available") is False and status in {"unknown", "available"}:
        status = "unavailable"
    updated = _timestamp(metadata.get("updated_timestamp")) or _timestamp(metadata.get("updated_time")) or _timestamp(metadata.get("updatedAt"))
    if updated is not None and updated > now + 300:
        updated = None
    page.update(status=status, providerStatus=raw if raw in {"published", "hidden", "removed", "wastebinned", "under_moderation", "not_published", "publish_with_game", "not_found"} else None,
                updatedAt=_iso(updated), updatedTimestamp=updated, source="nexus-api",
                sourceUrl=f"https://api.nexusmods.com/v1/games/{game}/mods/{mod_id}.json", checkedAt=_timestamp(checked_at))
    return page


def _local_actions(item: dict, analysis: dict | None) -> list[dict]:
    if not isinstance(analysis, dict):
        return []
    output = []
    plugins = analysis.get("plugins") if isinstance(analysis.get("plugins"), list) else []
    for plugin in plugins[:2048]:
        if not isinstance(plugin, dict):
            continue
        name = str(plugin.get("name") or "Plugin")[:240]
        # Rust ties these selected, bounded ESLifier records to owned source and
        # output copies, then compares fresh full-file hashes and actual winner.
        # A losing original still needs this warning when its conversion output
        # is stale; only master/eligibility actions below require the winner.
        provenance = plugin.get("provenance") if isinstance(plugin.get("provenance"), dict) else {}
        state = provenance.get("state")
        provenance_notes = {
            "source-changed": "The original plugin bytes changed while the winning output still matches the selected ESLifier record. Re-audit this conversion after the source update.",
            "output-changed": "The output differs from the selected ESLifier record, or ESLifier marks it externally altered. Review that change before treating it as the recorded conversion.",
            "source-and-output-changed": "Both the recorded source and output are inconsistent with the current analysis. Review the changes and rerun the conversion audit before trusting the output.",
            "winner-changed": "The recorded ESLifier output no longer wins in this profile. Review MO2 file priority and the actual winning plugin before deciding whether to rebuild it.",
        }
        if (state in provenance_notes and provenance.get("tool") == "ESLifier"
                and all(isinstance(provenance.get(field), str) and re.fullmatch(r"[0-9a-fA-F]{64}", provenance[field])
                        for field in ("originalSha256", "outputSha256"))):
            output.append({"id": "esl-stale-" + name, "severity": "warning", "label": "Recheck changed ESL conversion",
                           "detail": f"{name}: {provenance_notes[state]}", "action": "review-plugins", "url": "",
                           "evidence": "ESLifier records compared with freshly hashed MO2 plugin copies and the current winner",
                           "plugin": name, "provenanceState": state})
        if plugin.get("winner") is False:
            continue
        master_issues = plugin.get("masterIssues") if isinstance(plugin.get("masterIssues"), list) else []
        for issue in master_issues[:128]:
            if not isinstance(issue, dict) or issue.get("status") not in {"missing", "inactive", "loads-after"}:
                continue
            master = str(issue.get("name") or "")[:240]
            if not master:
                continue
            status = issue["status"]
            detail = {"missing": f"{name} declares {master}, which was not found in the complete scanned plugin inventory. Install the correct requirement or leave {name} disabled.",
                      "inactive": f"{name} requires {master}; that master is installed but inactive. Enable the correct master before enabling this plugin.",
                      "loads-after": f"{name} loads before its declared master {master}. Review the plugin load order in MO2."}[status]
            output.append({"id": "master-" + status + "-" + name + "-" + master, "severity": "warning", "label": "Resolve " + status.replace("-", " ") + " master",
                           "detail": detail, "action": "review-plugins", "url": "", "evidence": "TES4 MAST records and scanned plugin inventory", "plugin": name, "master": master})
        eligibility = plugin.get("eligibility") if isinstance(plugin.get("eligibility"), dict) else {}
        if eligibility.get("status") == "flag-only-candidate":
            output.append({"id": "esl-candidate-" + name, "severity": "info", "label": "Review ESL flag candidate",
                           "detail": f"{name}'s scanned records meet the local flag-only checks. Review it in xEdit and account for save and external references before changing flags.",
                           "action": "review-plugins", "url": "", "evidence": "Full TES4 record analysis; save safety is not assessed", "plugin": name})
    return output[:256]


def build_mod_health(item: dict, *, game: str = "", now: float | None = None,
                     metadata: dict | None = None, metadata_checked_at: float | None = None,
                     page_evidence: dict | None = None, local_analysis: dict | None = None,
                     previous: dict | None = None) -> dict:
    """Pure small per-mod evaluator; caller supplies background-fetched evidence."""
    now = _timestamp(now) or time.time()
    identity = nexus_identity(item, game)
    source = item.get("source") if isinstance(item.get("source"), dict) else {}
    links = _source_links(item, identity)
    provider = "nexus" if identity else provider_of(source.get("url", ""))
    result = {"schema": 1, "provider": provider, "identity": {"game": identity[0], "modId": identity[1]} if identity else {"url": links.get("project", "")},
              "checkedAt": None, "links": links, "page": {"status": "unknown", "updatedAt": None, "updatedTimestamp": None,
                "source": "", "sourceUrl": "", "checkedAt": None, "changedSincePrevious": None},
              "bugs": _bugs(url=links.get("bugs", "")), "actions": []}
    if identity:
        result["page"] = _metadata_page(metadata, metadata_checked_at, now, *identity)
        if isinstance(page_evidence, dict) and page_evidence.get("identity") == result["identity"] and page_evidence.get("provider") == "nexus":
            evidence_stamp = _timestamp(page_evidence.get("checkedAt"))
            if evidence_stamp and evidence_stamp <= now + 300 and _same_mod_url(str(page_evidence.get("url") or ""), *identity):
                incoming = page_evidence.get("bugs") if isinstance(page_evidence.get("bugs"), dict) else {}
                known = {"available", "disabled", "hidden", "locked", "unknown", "unavailable"}
                if incoming.get("state") in known and incoming.get("source") == "nexus-public-page":
                    # Only allow the scalar fields the reader emits. Never leak
                    # a caller's raw page, cookies, arbitrary URLs or metadata.
                    allowed = {"state", "displayedCount", "reasonCode", "reason", "changedSincePrevious", "previousState"}
                    result["bugs"].update({k: v for k, v in incoming.items() if k in allowed and isinstance(v, (str, int, float, bool, type(None)))})
                    result["bugs"]["displayedCount"] = _integer(result["bugs"].get("displayedCount"))
                    result["bugs"].update(source="nexus-public-page", checkedAt=evidence_stamp)
                    for key in ("reason", "reasonCode"):
                        result["bugs"][key] = str(result["bugs"].get(key) or "")[:500]
                observed_page = page_evidence.get("page") if isinstance(page_evidence.get("page"), dict) else {}
                # Public failure cannot erase a known provider listing state.
                if result["page"]["status"] == "unknown" and observed_page.get("status") in {"available", "not-found"}:
                    result["page"].update(status=observed_page["status"], source="nexus-public-page", sourceUrl=links["project"], checkedAt=evidence_stamp)
                if isinstance(page_evidence.get("fetch"), dict):
                    result["fetch"] = {"state": str(page_evidence["fetch"].get("state") or "unknown")[:80], "httpStatus": _integer(page_evidence["fetch"].get("httpStatus"))}
    else:
        result["bugs"].update(state="unsupported" if provider != "unknown" else "unknown", countLabel="Provider issue count",
                                reasonCode="provider-not-supported", reason="This provider has no verified bug-count adapter. Open its original issue or project page.")
        update = item.get("update") if isinstance(item.get("update"), dict) else {}
        if source.get("verified") and update.get("source") == f"{provider}-release-api":
            result["page"].update(status="release-observed", source=update["source"], sourceUrl=canonical_source_url(update.get("releaseUrl")) or links.get("project", ""), checkedAt=_timestamp(update.get("checkedAt")))
    page, bugs = result["page"], result["bugs"]
    if isinstance(previous, dict) and previous.get("identity") == result["identity"] and previous.get("provider") == provider:
        old = previous.get("page") if isinstance(previous.get("page"), dict) else {}
        old_updated, old_checked = _timestamp(old.get("updatedTimestamp")), _timestamp(old.get("checkedAt"))
        if (page.get("updatedTimestamp") is not None and _timestamp(old.get("updatedTimestamp")) is not None
                and page.get("checkedAt") and old_checked and page["checkedAt"] > old_checked):
            page["changedSincePrevious"] = page["updatedTimestamp"] > old_updated
        elif (page.get("checkedAt") and page["checkedAt"] == old_checked
              and page.get("updatedTimestamp") == old_updated and page.get("status") == old.get("status")
              and isinstance(old.get("changedSincePrevious"), bool)):
            # Rendering or inspecting the same observation must not consume its
            # change notification (including two installed copies of a project).
            page["changedSincePrevious"] = old["changedSincePrevious"]
        prior_bugs = previous.get("bugs") if isinstance(previous.get("bugs"), dict) else {}
        if bugs["state"] in {"available", "disabled", "hidden", "locked"} and prior_bugs.get("state") in {"available", "disabled", "hidden", "locked"}:
            prior_bug_stamp = _timestamp(prior_bugs.get("checkedAt"))
            if bugs.get("checkedAt") and prior_bug_stamp and bugs["checkedAt"] > prior_bug_stamp:
                bugs["changedSincePrevious"] = bugs["state"] != prior_bugs["state"]
                bugs["previousState"] = prior_bugs["state"]
            elif (bugs.get("checkedAt") and bugs["checkedAt"] == prior_bug_stamp
                  and bugs["state"] == prior_bugs["state"] and bugs.get("displayedCount") == prior_bugs.get("displayedCount")
                  and isinstance(prior_bugs.get("changedSincePrevious"), bool)):
                bugs["changedSincePrevious"] = prior_bugs["changedSincePrevious"]
                if prior_bugs.get("previousState") in {"available", "disabled", "hidden", "locked"}:
                    bugs["previousState"] = prior_bugs["previousState"]
    page["freshness"] = freshness(page.get("checkedAt"), now)
    bugs["freshness"] = freshness(bugs.get("checkedAt"), now, PAGE_TTL_SECONDS)
    checked = [stamp for stamp in (page.get("checkedAt"), bugs.get("checkedAt")) if _timestamp(stamp)]
    result["checkedAt"] = max(checked) if checked else None
    states = {page["freshness"]["state"], bugs["freshness"]["state"]}
    aggregate = "stale" if "stale" in states else "clock-skew" if "clock-skew" in states else "partial" if states == {"fresh", "unknown"} else "fresh" if states == {"fresh"} else "unknown"
    result["freshness"] = {"state": aggregate, "advisory": True}
    actions = result["actions"]
    def add(key: str, severity: str, label: str, detail: str, action: str, url: str = "", evidence: str = "") -> None:
        actions.append({"id": key, "severity": severity, "label": label, "detail": detail,
                        "action": action, "url": url, "evidence": evidence})
    if not links or source.get("kind") == "advisory":
        add("link-source", "info", "Confirm the original source", "Link the exact project before using provider facts to choose an update.", "link-source", links.get("project", ""), "MO2 source identity")
    update = item.get("update") if isinstance(item.get("update"), dict) else {}
    if update.get("verdict") == "candidate":
        latest = str(update.get("latestVersion") or "the newer version")[:120]
        add("review-update", "info", "Review available update", f"Installed version {str(item.get('installedVersion') or '?')[:120]}; provider candidate {latest}. Check exact files, requirements and installed options before updating.", "review-files", links.get("files", links.get("project", "")), str(update.get("source") or "Version comparison")[:100])
    if page.get("changedSincePrevious"):
        add("review-page-change", "info", "Review changed project page", "The provider's last-updated timestamp advanced since the previous check. Review the changelog and requirements; a timestamp alone does not prove a newer compatible file.", "open-url", links.get("changelog", links.get("project", "")), page["source"])
    if page["status"] in {"hidden", "removed", "under-moderation", "unpublished", "not-found", "unavailable"}:
        add("review-page-status", "warning", "Review source availability", f"The observed source status is {page['status']}. Keep the installed copy and review the original source before replacing or removing it.", "open-url", links.get("project", ""), page["source"])
    if bugs["state"] in {"disabled", "hidden", "locked"}:
        add("review-support-channel", "info", "Check the author's support instructions", f"The observed bugs section is {bugs['state']}. Check the description for another support channel; this observation does not identify who changed it.", "open-url", links.get("project", ""), bugs["source"])
    elif bugs.get("displayedCount") is not None and bugs["displayedCount"] > 0:
        add("review-bugs", "info", "Review Nexus bug reports", f"The observed Nexus badge shows {bugs['displayedCount']}. Reports may concern other versions or setups; this is not an open/total count or a diagnosis of this installation.", "open-url", links.get("bugs", ""), bugs["source"])
    elif bugs["state"] in {"unknown", "unavailable", "unsupported"} and links.get("bugs"):
        add("check-bugs", "info", "Open the bug or issue page", bugs["reason"], "open-url", links["bugs"], bugs["source"])
    if aggregate in {"stale", "clock-skew"}:
        add("refresh-health", "info", "Refresh health evidence", "Some observations are old or have an inconsistent timestamp. Refresh before relying on them.", "refresh-health", evidence="Observation timestamps")
    actions.extend(_local_actions(item, local_analysis))
    actions.sort(key=lambda action: 0 if action["severity"] == "warning" else 1)
    return result
