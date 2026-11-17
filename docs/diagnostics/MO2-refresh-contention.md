# MO2 refresh contention investigation

Source baseline: `3231bb689bebb050e597044c4f001e006f7ab82a` (v0.12.1).

## Confirmed source-level defect and repair

`attach_native_plugin_state` calls `native_plugin_facts` for every display snapshot.
It only consumes native-state availability, per-owner header summaries, and warnings.
Previously that path also resolved every plugin's physical VFS winner on MO2's UI
thread, although the returned physical paths were discarded. Display capture now
omits that unused work. `build_audit_request` still captures every physical winner
for analysis. No mobase call is moved to an unsafe worker thread.

Regression coverage compares all displayed per-owner facts and warnings, verifies
zero path resolutions for display capture, and verifies exact physical paths are
still obtained for explicit audits. This is a narrow reduction in redundant work,
not proof that the reported MO2 lag or network warnings are fixed.

## Remaining investigation

The reported log shows `QIODevice::read (QSslSocket): device not open`,
`Connection closed`, and `HTTP/2 protocol error`. The excerpt has no traceback or
request identity establishing which component initiated those requests. The exact
strings and neighboring event-filter/profile-sync log lines are not present in
this integration's Python/Qt bridge source. Do not suppress global Qt warnings or
claim they are caused by this plugin without attributable runtime evidence.

`ArsenalService.set_snapshot` invalidates the previous plugin audit and starts a
new one after every refreshed inventory. Deep inspection remains enabled after a
deep audit has been requested. Evaluate refresh storms and overlapping CPU/disk
load using actual MO2 profiles before changing freshness semantics. Preserve
profile-switch cancellation, installed-file changes, and last-good data.

Windows MO2 acceptance still needs enabled/disabled plugin comparisons with the
same profile, capture of snapshot durations and native callbacks, exact installed
plugin build, and full timestamped request/error logs. The user's desktop was
offline during this pass; no real MO2 measurement or installation was performed.
