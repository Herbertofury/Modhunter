"""Minimal stock startup trace, only in the detached test observer."""
from __future__ import annotations

from collections import deque
import sys
from time import perf_counter_ns

MAX_EVENTS = 96
EVENTS = frozenset(("attempt", "enumeration-enter", "enumeration-exit", "worker-enter",
                    "worker-exit", "worker-delivered", "publish-enter", "publish-exit",
                    "finish", "invalidate", "retry"))
FAILURES = {
    "MO2 profile changed; refresh again": "stale-before-publish",
    "MO2 profile changed during enumeration": "stale-during-enumeration",
    "MO2 profile changed during capture": "stale-during-capture",
    "MO2 is closing": "closing",
}
TYPE_CODES = {
    "UnstableSnapshotMetadata": "unstable-metadata",
    "SnapshotCancelled": "cancelled",
    "RuntimeError": "runtime-error",
    "OSError": "io-error",
    "PermissionError": "permission-error",
    "ValueError": "value-error",
    "TypeError": "type-error",
}
CALLERS = {
    "_on_plugins_refreshed": "plugin-list-refreshed",
    "_on_plugin_states_changed": "plugin-state",
    "_on_mod_states_changed": "mod-state",
    "_on_profile_changed": "profile-changed",
    "_on_mod_installed": "mod-installed",
    "_on_mod_removed": "mod-removed",
    "_on_mod_moved": "mod-or-plugin-moved",
}


def code(error, worker=False):
    if error is None and worker:
        return "ok"
    if not isinstance(error, str):
        return "unknown"
    if worker:
        return TYPE_CODES.get(error, "other-type")
    if error in FAILURES:
        return FAILURES[error]
    for prefix, label in (("Could not capture MO2 profile: ", "capture"),
                          ("Could not refresh local metadata: ", "worker"),
                          ("Could not publish MO2 profile: ", "publish")):
        if error.startswith(prefix):
            return label + ":" + TYPE_CODES.get(error[len(prefix):], "other-type")
    return TYPE_CODES.get(error, "unknown")


class StartupTrace:
    def __init__(self, clock=perf_counter_ns):
        self.clock = clock
        self.events = deque(maxlen=MAX_EVENTS)
        self.active = None
        self.overflow = False
        self.tickets = 0
        self.retry = None

    def record(self, ticket, edge, **payload):
        if len(self.events) >= MAX_EVENTS:
            self.overflow = True
            return
        self.events.append((self.clock(), ticket, edge, payload))

    def begin(self, ordinal, epoch):
        self.tickets += 1
        self.active = ticket = self.tickets
        self.record(ticket, "attempt", ordinal=ordinal, epoch=epoch)
        return ticket

    def wrap_project(self, project):
        def observed(*args):
            ticket = self.active
            if ticket is None:
                return project(*args)
            self.record(ticket, "worker-enter")
            try:
                return project(*args)
            finally:
                self.record(ticket, "worker-exit")
        return observed


def verify_trace(journal, *, origin, first, published, terminal_count):
    """Output only after validation; missing/ambiguous attempts are not a pass."""
    def check(condition, message):
        if not condition:
            raise ValueError("Incomplete startup trace: " + message)

    check(not journal.overflow and 0 < len(journal.events) < MAX_EVENTS
          and journal.active is None and journal.retry is None, "unfinished or overflowed")
    public = []
    attempts = []
    previous_finish = None
    previous_retry = None
    final = None
    required_ok = ("enumeration-enter", "enumeration-exit", "worker-enter", "worker-exit",
                   "worker-delivered", "publish-enter", "publish-exit")
    # Owner and worker may append in a different order from their captured
    # timestamps. Sort only after the stop edge, without a timed-path lock.
    events = sorted(journal.events, key=lambda item:item[0])
    for offset, ticket, edge, payload in events:
        check(edge in EVENTS and origin <= offset and 1 <= ticket <= journal.tickets, "invalid event")
        public.append({"msFromPluginInit": round((offset-origin)/1e6, 6),
                       "attempt": ticket, "edge": edge, **payload})
    for ticket in range(1, journal.tickets + 1):
        trace = [(t, edge, x) for t, n, edge, x in events if n == ticket]
        edges = [edge for _, edge, _ in trace]
        check(edges and edges[0] == "attempt" and edges.count("finish") == 1, "missing attempt boundary")
        point = {edge: (t, x) for t, edge, x in trace if edge != "invalidate"}
        check(len(point) == len(trace)-edges.count("invalidate"), "duplicate phase")
        start, attempt = point["attempt"]
        end, outcome = point["finish"]
        check((previous_finish is None and first <= start or previous_finish is not None and previous_finish <= start
               and previous_retry is not None and previous_retry <= start)
              and start <= end, "invalid attempt order")
        phases = {edge: point[edge][0] for edge in required_ok if edge in point}
        check(list(phases.values()) == sorted(phases.values()) and all(start <= v <= end for v in phases.values()),
              "invalid phase order")
        for a,b in (("enumeration-enter","enumeration-exit"),("worker-enter","worker-exit"),("publish-enter","publish-exit")):
            check((a in point)==(b in point), "partial phase")
        invalidated = [(t,x) for t,edge,x in trace if edge == "invalidate"]
        check(outcome["epoch"] >= attempt["epoch"] and bool(invalidated) == outcome["invalidated"],
              "native invalidation outcome was not fully observed")
        for t,x in invalidated:
            check(start <= t <= end and attempt["epoch"] <= x["epochBefore"] < x["epochAfter"] <= outcome["epoch"]
                  and x["callback"] in set(CALLERS.values()) | {"other"}, "invalid native invalidation")
        if "worker-delivered" in point:
            check("worker-exit" in point, "delivery before worker phase")
        if "publish-enter" in point:
            check("worker-delivered" in point, "publication without worker delivery")
        status=outcome["outcome"]
        if ticket == journal.tickets:
            check(status == "ok" and "retry" not in point and len(phases) == len(required_ok)
                  and phases["publish-enter"] <= published <= phases["publish-exit"] <= end
                  and attempt["ordinal"] == terminal_count, "missing final accepted publication")
            final = ticket
        else:
            check(status != "ok" and "retry" in point and point["retry"][0] >= end
                  and point["retry"][1]["requestedMs"] == (0 if outcome["invalidated"] else 1000),
                  "no observed production retry or incompatible delay")
        attempts.append({"id": ticket,"productionOrdinal":attempt["ordinal"],"outcome":status,
                         "durationMs":round((end-start)/1e6,6),"invalidated":outcome["invalidated"],
                         "startEpoch":attempt["epoch"],"finishEpoch":outcome["epoch"],
                         "retryRequestedMs":point["retry"][1]["requestedMs"] if "retry" in point else None})
        previous_finish = end
        previous_retry = point["retry"][0] if "retry" in point else None
    check(final is not None and sum(1 for x in attempts if x["outcome"]=="ok")==1, "no unique accepted attempt")
    return {"schemaVersion":1,"eventLimit":MAX_EVENTS,"attempts":attempts,"edges":public}


class StartupTraceMixin:
    def __init__(self):
        self._scale_trace = StartupTrace()
        super().__init__()

    def init(self, organizer):
        ok = super().init(organizer)
        if ok:
            j = self._scale_trace
            self._background_refresh._project = j.wrap_project(self._background_refresh._project)
        return ok

    def _start_automatically(self):
        # Read the released guard for observation, never block the base call.
        if (not self._closing and self.server is None and (self._ui_initialized or self._startup_open)
                and not self._startup_inflight and self._startup_attempts < 5):
            self._scale_trace.begin(self._startup_attempts+1, self._background_refresh._epoch)
        return super()._start_automatically()

    def _begin_background_capture(self, valid):
        j = self._scale_trace
        ticket = j.active
        if ticket is None:
            return super()._begin_background_capture(valid)
        j.record(ticket, "enumeration-enter")
        try:
            return super()._begin_background_capture(valid)
        finally:
            j.record(ticket, "enumeration-exit")

    def _on_background_refresh_completed(self, completed):
        j = self._scale_trace
        if j.active is not None:
            j.record(j.active, "worker-delivered", workerOutcome=code(completed[2], worker=True))
        return super()._on_background_refresh_completed(completed)

    def _publish_background_refresh(self, result, valid):
        j = self._scale_trace
        ticket = j.active
        if ticket is None:
            return super()._publish_background_refresh(result, valid)
        j.record(ticket, "publish-enter")
        try:
            return super()._publish_background_refresh(result, valid)
        finally:
            j.record(ticket, "publish-exit")

    def _schedule_refresh(self):
        j = self._scale_trace
        ticket = j.active
        if ticket is None:
            return super()._schedule_refresh()
        epoch = self._background_refresh._epoch
        callback = CALLERS.get(sys._getframe(1).f_code.co_name, "other")
        try:
            return super()._schedule_refresh()
        finally:
            after = self._background_refresh._epoch
            if after != epoch:
                j.record(ticket, "invalidate", callback=callback, epochBefore=epoch, epochAfter=after)

    def _finish_background_startup(self, result):
        j = self._scale_trace
        ticket = j.active
        if ticket is None:
            return super()._finish_background_startup(result)
        good = result.get("ok") is True
        j.record(ticket, "finish", outcome="ok" if good else code(result.get("error")),
                 invalidated=bool(self._startup_invalidated), epoch=self._background_refresh._epoch)
        j.active = None
        j.retry = ticket if not good else None
        try:
            return super()._finish_background_startup(result)
        finally:
            j.retry = None

    def _schedule_startup(self, delay=0):
        j = self._scale_trace
        ticket = j.retry
        before = self._startup_timer
        result = super()._schedule_startup(delay)
        if ticket is not None and before is None and self._startup_timer is not None:
            j.record(ticket, "retry", requestedMs=delay)
        return result
