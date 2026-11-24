"""Published GitHub security advisories for verified repositories and installed packages."""

from __future__ import annotations

from contextlib import closing, contextmanager
from hashlib import sha256
import os
import json
import re
import tempfile

try:
    import sqlite3
except ImportError:
    sqlite3 = None  # Some stock MO2 embedded Python distributions omit a required DLL.
from email.utils import parsedate_to_datetime
import time
from pathlib import Path
from urllib.parse import urlsplit

from .source_registry import _get_json, canonical_source_url
from .version_evidence import normalized_version

_DATABASE_ERROR = sqlite3.Error if sqlite3 is not None else OSError


def repository(source):
    if (
        not isinstance(source, dict)
        or source.get("verified") is not True
        or source.get("kind") == "advisory"
    ):
        return None
    raw_url = source.get("url")
    if not isinstance(raw_url, str) or "github.com/" not in raw_url.lower():
        return None
    url = urlsplit(canonical_source_url(raw_url))
    if url.hostname != "github.com":
        return None
    parts = url.path.strip("/").split("/")
    if (
        len(parts) < 2
        or parts[2:]
        and parts[2] not in ("releases", "issues", "tree", "security")
        or not all(
            re.fullmatch(r"[a-zA-Z0-9_.-]+", part) and part not in (".", "..")
            for part in parts[:2]
        )
    ):
        return None
    return parts[0].lower() + "/" + parts[1].removesuffix(".git").lower()


def package_manifest(path, source):
    """Only read an installed root package.json for a verified GitHub source."""
    if not path or not repository(source):
        return {}
    manifest = Path(path) / "package.json"
    try:
        if manifest.is_symlink() or not manifest.is_file():
            return {}
        with manifest.open(encoding="utf-8-sig") as stream:
            data = json.load(stream)
        name, version = data["name"], data["version"]
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"(?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+", name)
            or not isinstance(version, str)
        ):
            return {}
        return {
            "installedPackage": {
                "ecosystem": "npm",
                "name": name,
                "version": version,
                "evidence": "installed package.json",
            }
        }
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        return {}


def npm_in_range(version, affected):
    # Evaluate only unambiguous intersections of release-version comparisons.
    # npm prereleases, disjunctions, wildcards and arbitrary author labels need review.
    if not isinstance(version, str) or not re.fullmatch(
        r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){2}", version
    ):
        return None
    local = normalized_version(version)
    if not local or not isinstance(affected, str):
        return None
    comparisons = []
    for part in affected.split(","):
        match = re.fullmatch(
            r"\s*(<=|>=|<|>|=)?\s*((?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){2})\s*",
            part,
        )
        if not match:
            return None
        bound = normalized_version(match[2])
        if not bound:
            return None
        op = match[1] or "="
        comparisons.append(
            {
                "=": local == bound,
                "<": local < bound,
                ">": local > bound,
                "<=": local <= bound,
                ">=": local >= bound,
            }[op]
        )
    return all(comparisons)


def affected_rows(project, data):
    if not isinstance(data, list):
        raise ValueError("GitHub advisory list is incomplete")
    rows = []
    for raw in data:
        if not isinstance(raw, dict):
            raise ValueError("Malformed GitHub advisory")
        if (
            raw.get("state") != "published"
            or not raw.get("published_at")
            or raw.get("withdrawn_at")
        ):
            continue
        ghsa = str(raw.get("ghsa_id") or "")
        if not re.fullmatch(r"GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}", ghsa, re.I):
            raise ValueError("Invalid advisory identifier")
        url = f"https://github.com/{project}/security/advisories/{ghsa}"
        if str(raw.get("html_url") or "").casefold() != url.casefold():
            raise ValueError("Advisory belongs to another repository")
        risks = raw.get("vulnerabilities")
        if risks is not None and not isinstance(risks, list):
            raise ValueError("Incomplete advisory package evidence")
        for risk in risks or [{}]:
            if not isinstance(risk, dict):
                raise ValueError("Malformed advisory package")
            package = risk.get("package") or {}
            rows.append(
                {
                    "id": ghsa,
                    "url": url,
                    "publishedAt": raw["published_at"],
                    "updatedAt": raw.get("updated_at"),
                    "severity": raw.get("severity"),
                    "title": raw.get("summary"),
                    "ecosystem": package.get("ecosystem"),
                    "package": package.get("name"),
                    "affectedRange": risk.get("vulnerable_version_range"),
                    "patchedVersions": risk.get("patched_versions"),
                }
            )
    return rows


class GitHubAdvisories:
    def __init__(self, path, fetch=_get_json):
        self.path, self.fetch = Path(path), fetch

    @contextmanager
    def _cache(self, project):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if sqlite3 is None:
            # One atomic document per project avoids losing unrelated entries when
            # multiple embedded hosts share the Mod Hunter cache directory.
            root = self.path.with_name(self.path.name + ".records")
            root.mkdir(exist_ok=True)
            key = sha256(project.encode("utf-8")).hexdigest()

            def read(name):
                try:
                    return json.loads((root / name).read_text(encoding="utf-8"))
                except FileNotFoundError:
                    return None

            def save(name, value):
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(
                        dir=root, delete=False, mode="w", encoding="utf-8"
                    ) as stream:
                        temporary = Path(stream.name)
                        json.dump(value, stream)
                        stream.flush()
                        os.fsync(stream.fileno())
                    temporary.replace(root / name)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)

            yield (
                read(key),
                read("backoff"),
                lambda row: save(key, row),
                lambda until: save("backoff", until),
            )
            return

        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS advisories (project TEXT PRIMARY KEY, checked REAL, evidence TEXT)"
            )
            saved = db.execute(
                "SELECT checked, evidence FROM advisories WHERE project=?", (project,)
            ).fetchone()
            db.execute(
                "CREATE TABLE IF NOT EXISTS backoff (provider TEXT PRIMARY KEY, until REAL)"
            )
            backoff = db.execute(
                "SELECT until FROM backoff WHERE provider='github'"
            ).fetchone()

            def save_result(result):
                db.execute(
                    "INSERT OR REPLACE INTO advisories VALUES (?,?,?)",
                    (project, result["checkedAt"], json.dumps(result)),
                )

            def save_backoff(until):
                db.execute(
                    "INSERT OR REPLACE INTO backoff VALUES ('github',?)", (until,)
                )

            yield (
                json.loads(saved[1]) if saved else None,
                backoff[0] if backoff else None,
                save_result,
                save_backoff,
            )

    def observe(self, project, cancel):
        now = time.time()
        with self._cache(project) as (prior, backoff, save_result, save_backoff):
            prior = prior or {}
            if prior and 0 <= now - prior["checkedAt"] < 4 * 3600:
                return prior
            endpoint = f"https://api.github.com/repos/{project}/security-advisories"
            if backoff and now < backoff:
                return {
                    **prior,
                    "repository": project,
                    "state": "rate-limited",
                    "retryAt": backoff,
                    "attemptedAt": now,
                    "sourceUrl": endpoint,
                    "findings": prior.get("findings", []),
                }
            rows, page, seen_pages = [], 1, set()
            try:
                while not cancel.is_set():
                    data = self.fetch(
                        f"{endpoint}?state=published&per_page=100&page={page}"
                    )
                    batch = affected_rows(project, data)
                    rows.extend(batch)
                    if len(data) < 100:
                        break
                    signature = tuple(item.get("ghsa_id") for item in data)
                    if signature in seen_pages:
                        raise ValueError("Provider repeated an advisory page")
                    seen_pages.add(signature)
                    page += 1
                if cancel.is_set():
                    return {"state": "cancelled", "findings": []}
                result = {
                    "repository": project,
                    "state": "checked",
                    "checkedAt": now,
                    "sourceUrl": endpoint,
                    "findings": rows,
                }
                save_result(result)
                return result
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                code = getattr(exc, "code", None)
                headers = getattr(exc, "headers", {}) or {}
                limited = (
                    code == 429
                    or code == 403
                    and (
                        headers.get("X-RateLimit-Remaining") == "0"
                        or "Retry-After" in headers
                    )
                )
                retry_at = None
                if limited:
                    delay = headers.get("Retry-After", "")
                    try:
                        retry_at = (
                            now + int(delay)
                            if delay.isdecimal()
                            else parsedate_to_datetime(delay).timestamp()
                        )
                    except (ValueError, TypeError, OverflowError):
                        retry_at = None
                    try:
                        reset = float(headers.get("X-RateLimit-Reset", 0))
                    except (ValueError, TypeError, OverflowError):
                        reset = 0
                    retry_at = max(now + 1, retry_at or reset or now + 60)
                    save_backoff(retry_at)
                return {
                    **prior,
                    "repository": project,
                    "state": "rate-limited" if limited else "unavailable",
                    "retryAt": retry_at,
                    "attemptedAt": now,
                    "sourceUrl": endpoint,
                    "httpStatus": code,
                    "findings": prior.get("findings", []),
                }

    def annotate(self, items, records, cancel):
        shared = {}
        for item in items:
            if cancel.is_set():
                return
            record = records.get(str(item.get("id")))
            if not isinstance(record, dict):
                continue
            project = repository(item.get("source"))
            if not project:
                continue
            if project not in shared:
                retry = record.get("retryAt")
                state = record.get("availability")
                if (
                    state in ("rate-limited", "authentication-required")
                    or type(retry) in (int, float)
                    and retry > time.time()
                ):
                    shared[project] = {
                        "repository": project,
                        "state": state or "rate-limited",
                        "retryAt": retry,
                        "findings": [],
                    }
                else:
                    try:
                        shared[project] = self.observe(project, cancel)
                    except (OSError, ValueError, TypeError, KeyError, _DATABASE_ERROR):
                        shared[project] = {
                            "repository": project,
                            "state": "unavailable",
                            "findings": [],
                        }
            record["securityAdvisories"] = evaluate(shared[project], item)


def evaluate(observation, item):
    local = item.get("installedPackage") or {}
    findings = []
    for notice in observation.get("findings", []):
        applies = None
        if (
            local.get("evidence") == "installed package.json"
            and local.get("ecosystem") == "npm"
            and notice.get("ecosystem") == "npm"
            and local.get("name") == notice.get("package")
        ):
            applies = npm_in_range(local.get("version"), notice.get("affectedRange"))
        findings.append(
            {
                **notice,
                "applicability": "affected"
                if applies is True
                else "outside-range"
                if applies is False
                else "unverified",
                "installedPackageVersion": local.get("version")
                if applies is not None
                else None,
            }
        )
    return {**observation, "findings": findings}
