"""Credential-free hints for discovering an already-running local MO2 bridge.

Descriptors are not authority: the desktop must validate the loopback URL and
probe /api/health before using one. This module never scans ports or processes.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit

MAX_DESCRIPTOR_BYTES = 4096
LOG = logging.getLogger(__name__)


def default_session_directory() -> Path | None:
    if sys.platform != "win32":
        return None
    local = os.environ.get("LOCALAPPDATA", "").strip()
    return Path(local) / "Modhunter" / "mo2-sessions" if local else None


class SessionDiscovery:
    """Atomically publish one process/port descriptor and clean up only our file."""

    def __init__(self, directory: Path | None = None):
        self.directory = Path(directory) if directory is not None else default_session_directory()
        self._lock = threading.Lock()
        self._path: Path | None = None
        self._written: bytes | None = None

    def publish(self, endpoint: str, profile_id: str, profile_name: str) -> bool:
        if self.directory is None:
            return False
        temporary: Path | None = None
        with self._lock:
            try:
                url = urlsplit(endpoint)
                if (url.scheme != "http" or url.hostname != "127.0.0.1" or url.username
                        or url.password or url.path != "/" or url.query or url.fragment
                        or url.port is None or not 1024 <= url.port <= 65535):
                    raise ValueError("Discovery requires a strict IPv4 loopback endpoint")
                profile_id, profile_name = str(profile_id), str(profile_name)
                if len(profile_id) > 256 or len(profile_name) > 512:
                    raise ValueError("MO2 profile identity exceeds the discovery size bound")
                pid = os.getpid()
                document = {"app": "Modhunter", "protocol": 1, "endpoint": endpoint,
                            "profileId": profile_id, "profileName": profile_name, "pid": pid}
                encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                if len(encoded) > MAX_DESCRIPTOR_BYTES:
                    raise ValueError("MO2 discovery descriptor exceeds 4096 bytes")
                destination = self.directory / f"{pid}-{url.port}.json"
                if destination == self._path and encoded == self._written:
                    return True  # Ordinary metadata refreshes do not rewrite it.
                self.directory.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(mode="wb", prefix=destination.name + ".tmp-",
                                                 dir=self.directory, delete=False) as output:
                    temporary = Path(output.name)
                    output.write(encoded)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, destination)
                temporary = None
                self._path, self._written = destination, encoded
                return True
            except (OSError, ValueError, TypeError) as exc:
                LOG.warning("Modhunter MO2 session discovery could not be written: %s", exc)
                return False
            finally:
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError as exc:
                        LOG.warning("Modhunter discovery temporary file could not be removed: %s", exc)

    def close(self) -> None:
        with self._lock:
            path, written = self._path, self._written
            self._path = self._written = None
            if path is None or written is None:
                return
            try:
                with path.open("rb") as source:
                    current = source.read(MAX_DESCRIPTOR_BYTES + 1)
                if current == written:
                    path.unlink()
                # Another writer's replacement is deliberately left alone.
            except FileNotFoundError:
                pass
            except OSError as exc:
                LOG.warning("Modhunter MO2 session discovery could not be removed: %s", exc)
