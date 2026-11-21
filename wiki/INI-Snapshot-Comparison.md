# Compare two INI snapshots locally

This source-candidate feature adds a read-only command to the native Modhunter
core CLI. It is separate from the dashboard, Watchtower and any live profile
connection. It is not a released settings history tab.

    modhunter-cli ini-compare --before "Copy before.ini" --after "Copy after.ini"

Select two saved copies of the **same logical QSettings/ModOrganizer INI** in
the intended order. Modhunter does not discover or open other files, inspect
the current profile, resolve directories named in settings, or apply changes.
Each input uses the existing 256,000-byte INI budget. A missing, linked,
unreadable, changed, unsupported-encoding, malformed or duplicate-key input
returns a failure instead of a partial report.

The JSON reports added, removed and changed settings, and whether the complete
inputs were byte-identical or only equivalent under the existing Modhunter
decoder (for instance when comments, capitalization, escapes or encodings
differ). Case-insensitive duplicate assignments are ambiguous and rejected,
not guessed. This comparison is not a compatibility assertion for every game's
INI dialect, and does not compare comment edits or formatting as settings.

Only labels drawn from fields Modhunter's profile importer already consumes are
listed by name. Other changes are included in the unlabeled counts, and no
setting values, file paths, raw unknown names, timestamps, usernames, or secrets
are printed. The supplied ordering is explicit; profile identity, original
editor and modification time are not inferred. No revision journal, backup,
restore, or automatic observation is created.

## Desktop toolbar source preview

**Compare INI**, next to **Instance INI**, asks for the before copy and after
copy in that order. Each copy is read once as soon as its selection returns,
so replacing the first file while the second picker is open cannot silently
substitute newer bytes. If the first copy cannot be read, the second picker does
not open. This action does not replace the saved Instance INI or require a live
connection. A short native report shows totals and recognized setting labels.
No path, value, or unknown key is printed; closing the application, switching
to a newer view, or canceling the picker discards an unfinished review. This
action schedules no background scans or retained history.


## Watchtower review

Open **Watch Pages > Preferences > Review INI changes > Choose two saved
copies**. The same native picker reads each exact copy when you choose it.
Filter the report by Changed, Added, Removed, or All changes. The table
includes unrecognized changes in its totals but never lists their names.
Known labels come from the existing importer; every value and path remains
hidden. Editor and edit time are unknown. This is a temporary review, not a
saved history entry or a binding to your installed game, mod, or profile.
Leaving Preferences or closing the window clears or drops the review rather
than reinterpreting it for a new view. The existing toolbar route also stays
available without opening Watchtower.
