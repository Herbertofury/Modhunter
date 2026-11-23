"""Bounded organizer-thread handoff for a background metadata snapshot.

The caller owns the Qt-thread capture, publication and event epoch. Only the
immutable primitive capture plus a cancellation event reaches project().
deliver() must queue a result to the owning thread, never publish it directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event, Thread, get_ident
from typing import Any, Callable

from .model import LocalMetadataCache


class UnstableSnapshotMetadata(Exception):
    """An owned metadata stream changed twice before publication."""


class RefreshMetadataCache(LocalMetadataCache):
    """Use the released cache logic, but never publish a known raced read.

    This cache is worker-owned and only one job uses it at a time. A stable
    ordinary cache hit keeps its single signature probe. Already-unreadable
    paths retain the released best-effort read semantics and are not cached.
    """

    def __init__(self):
        super().__init__()
        self._stamps = []

    def _signature(self, path):
        stamp = super()._signature(path)
        self._stamps.append(stamp)
        return stamp

    def inspect(self, path, notes, *, force=False):
        for attempt in range(2):
            self._stamps.clear()
            result = super().inspect(path, notes, force=force or attempt > 0)
            if len(self._stamps) < 2 or self._stamps[0] == self._stamps[1]:
                return result
        self._entries.pop(self._key(path), None)
        raise UnstableSnapshotMetadata()


Outcome = dict[str, Any]
Complete = Callable[[Outcome], None]


@dataclass
class _Work:
    token: object
    epoch: int
    context: tuple[str, ...]
    cancel: Event
    waiters: list[Complete] = field(default_factory=list)
    capture_state: tuple[Any, float] | None = None


class RefreshWork:
    """One cooperative job; at most one coalesced follow-up capture, no polling."""

    MAX_WAITERS = 8

    def __init__(self, *, context, capture, project, publish, deliver,
                 begin_capture=None, schedule_capture=None):
        if (begin_capture is None) != (schedule_capture is None):
            raise ValueError("Incremental capture requires an owner-thread scheduler")
        self._owner = get_ident()
        self._begin_capture = begin_capture
        self._schedule_capture = schedule_capture
        self._context = context
        self._capture = capture
        self._project = project
        self._publish = publish
        self._deliver = deliver
        self._active: _Work | None = None
        self._pending = False
        self._force = False
        self._waiters: list[Complete] = []
        self._epoch = 0
        self._closed = False

    def _check_owner(self):
        if get_ident() != self._owner:
            raise RuntimeError("Snapshot coordinator belongs to the organizer thread")

    @staticmethod
    def _error(message):
        return {"ok": False, "error": message}

    @staticmethod
    def _notify(waiters, result):
        for done in waiters:
            done(result)

    def enqueue(self, *, force=False, done=None):
        self._check_owner()
        if self._closed:
            if done is not None:
                done(self._error("MO2 is closing"))
            return
        if done is not None:
            if len(self._waiters) >= self.MAX_WAITERS:
                done(self._error("Another profile refresh is pending"))
                return
            self._waiters.append(done)
        self._pending = True
        self._force |= bool(force)
        self._start()

    @property
    def busy(self):
        self._check_owner()
        return self._active is not None or self._pending

    def invalidate(self):
        """Reject in-flight work even when the profile later returns to the same name."""
        self._check_owner()
        self._epoch += 1
        if self._active is not None:
            self._active.cancel.set()

    def close(self):
        self._check_owner()
        self._closed = True
        self.invalidate()
        waiting, self._waiters = self._waiters, []
        active, self._active = self._active, None
        self._pending = self._force = False
        if active is not None:
            self._close_capture(active)
            waiting += active.waiters
        self._notify(waiting, self._error("MO2 is closing"))

    def _start(self):
        if self._active is not None or not self._pending or self._closed:
            return
        waiters, self._waiters = self._waiters, []
        force, self._force = self._force, False
        self._pending = False
        try:
            epoch = self._epoch
            context = self._context()
            # Reserve before capture: a reentrant event invalidates this capture
            # and queues at most one follow-up instead of recursively starting.
            work = _Work(object(), epoch, context, Event(), waiters)
            self._active = work
            if self._begin_capture is None:
                captured = self._capture()
                if not self._valid(work):
                    if self._active is work:
                        self.complete((work.token, None, "MO2 profile changed during capture"))
                    return
                self._launch(work, captured, force)
            else:
                candidate = self._begin_capture(lambda: self._valid(work))
                if not self._valid(work):
                    candidate[0].close()
                    if self._active is work:
                        self.complete((work.token, None, "MO2 profile changed during enumeration"))
                    return
                work.capture_state = candidate
                self._schedule_capture(lambda: self._capture_step(work, force))
        except Exception as exc:
            failed, self._active = self._active, None
            if failed is not None:
                self._close_capture(failed)
            if not self._closed:
                self._notify(failed.waiters if failed is not None else waiters,
                             self._error("Could not capture MO2 profile: " + type(exc).__name__))
                self._start()

    def _valid(self, work):
        return (not self._closed and self._active is work and self._epoch == work.epoch
                and not work.cancel.is_set() and self._context() == work.context)

    @staticmethod
    def _close_capture(work):
        state, work.capture_state = work.capture_state, None
        if state is not None:
            state[0].close()

    def _capture_step(self, work, force):
        self._check_owner()
        if self._active is not work or work.capture_state is None:
            return
        try:
            if not self._valid(work):
                self.complete((work.token, None, "MO2 profile changed during capture"))
                return
            source, started = work.capture_state
            captured = source.step()
            if self._active is not work:
                return
            if not self._valid(work):
                self.complete((work.token, None, "MO2 profile changed during capture"))
            elif captured is None:
                self._schedule_capture(lambda: self._capture_step(work, force))
            else:
                self._close_capture(work)
                self._launch(work, (captured, started), force)
        except Exception as exc:
            self.complete((work.token, None, type(exc).__name__))

    def _launch(self, work, captured, force):
        # Worker holds only projector, queued delivery, token, cancel, and
        # copied primitives. No organizer object or capture handle is sent.
        project, deliver = self._project, self._deliver
        token, cancel = work.token, work.cancel

        def run():
            try:
                value = project(captured, force, cancel)
                completed = (token, value, None)
            except Exception as exc:
                completed = (token, None, type(exc).__name__)
            deliver(completed)

        Thread(target=run, name="ModHunter local metadata", daemon=True).start()

    def complete(self, completed):
        self._check_owner()
        token, value, error = completed
        work = self._active
        if work is None or work.token is not token or self._closed:
            return
        # Leave the reservation in place through publication, which can
        # synchronously invoke observers and queue one follow-up.
        self._close_capture(work)
        def valid():
            return self._valid(work)
        try:
            if not valid():
                result = self._error("MO2 profile changed; refresh again")
            elif error:
                result = self._error("Could not refresh local metadata: " + error)
            else:
                result = self._publish(value, valid)
        except Exception as exc:
            result = self._error("Could not publish MO2 profile: " + type(exc).__name__)
        if self._active is work:
            self._active = None
            try:
                self._notify(work.waiters, result)
            finally:
                self._start()
